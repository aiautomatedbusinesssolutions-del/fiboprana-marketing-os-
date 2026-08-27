r"""Mechanically cut the silent gaps out of a recorded video (jump-cut editor).

    python -m videos.silence_cut "path\to\raw.mp4"            # -> raw.cut.mp4 alongside it
    python -m videos.silence_cut raw.mp4 -o edited.mp4
    python -m videos.silence_cut raw.mp4 --dry-run            # detect + report, no encode
    python -m videos.silence_cut raw.mp4 --noise -27dB --min-silence 0.8 --pad 0.10

What it does
------------
Two ffmpeg passes, no extra dependencies (just ffmpeg/ffprobe on PATH):

  1. `silencedetect` finds gaps quieter than --noise for longer than --min-silence.
  2. The kept (speaking) windows are stitched back together with select/aselect +
     setpts, which keeps audio and video in sync, and re-encoded to a new file.

This is MECHANICAL only — it removes dead air, nothing else. It never removes
retakes or flubs (that's a human call) and never overwrites the original; output
goes to a NEW file (default: <name>.cut.mp4). Pure dead-air trimming, so the talk
still flows the way you recorded it.

Tuning: if it removes too little, the room tone is probably above the threshold —
raise --noise toward -25dB / -22dB. If it clips the starts of words, raise --pad.
--min-silence keeps natural sub-second pauses between sentences intact.

Founder feedback 2026-07-23 (first published batch): cuts sat too tight against
the words and quick in-out cuts read as fake. Defaults loosened: pad 0.08->0.15,
min-silence 0.6->0.75, merge-gap 0.12->0.2. Direction: breathing room over
maximum trim; nudge further only if it still feels clipped.
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# silencedetect prints to stderr: "silence_start: 12.34" and
# "silence_end: 15.67 | silence_duration: 3.33".
_RE_START = re.compile(r"silence_start:\s*(-?[\d.]+)")
_RE_END = re.compile(r"silence_end:\s*(-?[\d.]+)")


def _run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def probe_duration(path):
    r = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
              "-of", "default=noprint_wrappers=1:nokey=1", str(path)])
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise SystemExit(f"Could not read duration (is this a media file?):\n{r.stderr}")


def detect_silences(path, noise, min_silence):
    """Return a list of (start, end) silence intervals, in seconds."""
    r = _run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
              "-af", f"silencedetect=noise={noise}:d={min_silence}",
              "-f", "null", "-"])
    starts, ends = [], []
    for line in r.stderr.splitlines():
        m = _RE_START.search(line)
        if m:
            starts.append(max(0.0, float(m.group(1))))
        m = _RE_END.search(line)
        if m:
            ends.append(float(m.group(1)))
    # Pair them up in order; a trailing silence may have a start with no end.
    silences = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        silences.append((s, e))
    return silences


def keep_windows(silences, duration, pad, merge_gap):
    """Complement of the silences -> speaking windows, padded and merged."""
    keeps, cursor = [], 0.0
    for s, e in silences:
        if s > cursor:
            keeps.append([cursor, s])
        cursor = max(cursor, e if e is not None else duration)
    if cursor < duration:
        keeps.append([cursor, duration])

    # Pad each window outward (give words a little breathing room), clamp, merge.
    padded = []
    for a, b in keeps:
        a, b = max(0.0, a - pad), min(duration, b + pad)
        if padded and a - padded[-1][1] <= merge_gap:
            padded[-1][1] = max(padded[-1][1], b)
        else:
            padded.append([a, b])
    # Drop any window shorter than ~1 frame's worth of time.
    return [(a, b) for a, b in padded if b - a > 0.04]


def build_filtergraph(keeps):
    """trim/atrim each kept window, then concat — scales to 100+ cuts, where a
    single giant select='between()+between()...' expression blows the parser."""
    chains, labels = [], []
    for i, (a, b) in enumerate(keeps):
        chains.append(f"[0:v]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[v{i}]")
        chains.append(f"[0:a]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[v{i}][a{i}]")
    chains.append("".join(labels) + f"concat=n={len(keeps)}:v=1:a=1[outv][outa]")
    return ";".join(chains)


def main():
    p = argparse.ArgumentParser(description="Cut silent gaps out of a video (mechanical jump-cut).")
    p.add_argument("media", help="path to the recorded video")
    p.add_argument("-o", "--out", default="", help="output path (default: <name>.cut.mp4)")
    p.add_argument("--noise", default="-30dB", help="silence threshold (default -30dB; raise toward -25dB if too little is cut)")
    p.add_argument("--min-silence", type=float, default=0.75, help="min gap length to cut, seconds (default 0.75)")
    p.add_argument("--pad", type=float, default=0.15, help="keep this much around speech, seconds (default 0.15)")
    p.add_argument("--merge-gap", type=float, default=0.2, help="merge kept windows closer than this, seconds")
    p.add_argument("--crf", type=int, default=18, help="x264 quality, lower=better (default 18)")
    p.add_argument("--preset", default="veryfast", help="x264 preset (default veryfast)")
    p.add_argument("--dry-run", action="store_true", help="detect + report only; don't encode")
    args = p.parse_args()

    src = Path(args.media).expanduser()
    if not src.is_file():
        raise SystemExit(f"File not found: {src}")
    out = Path(args.out).expanduser() if args.out else src.with_suffix(".cut" + src.suffix)

    duration = probe_duration(src)
    print(f"Source: {src.name}  ({duration/60:.1f} min)")
    print(f"Detecting silence (noise={args.noise}, min={args.min_silence}s)...")
    silences = detect_silences(src, args.noise, args.min_silence)
    keeps = keep_windows(silences, duration, args.pad, args.merge_gap)

    if not keeps:
        raise SystemExit("No speaking windows found — check --noise (audio may be quieter than the threshold).")

    kept = sum(b - a for a, b in keeps)
    removed = duration - kept
    print(f"  {len(silences)} silence gap(s) found; keeping {len(keeps)} window(s).")
    print(f"  cut ~{removed/60:.1f} min of dead air "
          f"({removed/duration*100:.0f}%) -> new length ~{kept/60:.1f} min.")

    if args.dry_run:
        print("\n(dry run — no file written. Re-run without --dry-run to encode.)")
        return 0

    graph = build_filtergraph(keeps)
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-i", str(src),
        "-filter_complex", graph,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", args.preset, "-crf", str(args.crf), "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
        str(out),
    ]
    print(f"\nEncoding -> {out.name} ...")
    r = subprocess.run(cmd)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg failed (exit {r.returncode}).")
    print(f"Done: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
