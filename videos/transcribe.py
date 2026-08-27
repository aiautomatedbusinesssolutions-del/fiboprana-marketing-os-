r"""Transcribe a recorded video (or audio file) into the brand-voice corpus.

    python -m videos.transcribe path\to\video.mp4
    python -m videos.transcribe raw.mp4 --pillar other --title "Why I stopped checking my score"
    python -m videos.transcribe raw.mp4 --model small.en      # faster first test
    python -m videos.transcribe raw.mp4 --hotwords "Fiboprana Oura qigong"

What it does
------------
Runs faster-whisper locally (CPU by default) on the file's audio and writes two
artifacts side by side in content/videos/:

    {YYYY-MM-DD}_{slug}.md    transcript + a YAML header — one entry in the
                              growing brand-voice corpus.
    {YYYY-MM-DD}_{slug}.srt   segment-timed captions, for later subtitle use.

The corpus is the RAW, ever-growing voice layer. It is meant to be read back
into content-generation prompts as live examples of how you actually talk.
BRAND_VOICE.md is the CURATED layer and is never written to from here — voice
patterns get hand-distilled into it deliberately (see content/videos/README.md).

faster-whisper reads the audio straight out of the video (no separate extract
step). The first run downloads the model (distil-large-v3 ~= 1.5 GB); pass a
smaller --model (e.g. base.en, small.en) for a quick first smoke test. Whisper
doesn't know proper nouns like "Fiboprana" — --hotwords biases it toward them.
"""

import argparse
import datetime as dt
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = ROOT / "content" / "videos"

VALID_PILLARS = ("quit-tracker", "is-it-working", "mind-science", "other")

# Proper nouns Whisper reliably mishears. A deterministic post-fix is more
# reliable than --hotwords for an invented word. Add variants as you spot them.
_BRAND_CORRECTIONS = {
    "fibo prana": "Fiboprana",
    "fibro prana": "Fiboprana",
    "fibroprana": "Fiboprana",
    "fiber prana": "Fiboprana",
    "fibre prana": "Fiboprana",
    "fee bow prana": "Fiboprana",
    "phoebo prana": "Fiboprana",
    "fibonacci prana": "Fiboprana",
    "fibo piranha": "Fiboprana",
    "fibopranha": "Fiboprana",
    "fibo prada": "Fiboprana",
    "fiboprano": "Fiboprana",
    "chi gong": "qigong",
    "chee gong": "qigong",
}


def _apply_corrections(text):
    for wrong, right in _BRAND_CORRECTIONS.items():
        text = re.sub(re.escape(wrong), right, text, flags=re.IGNORECASE)
    return text


def _slugify(text, fallback):
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or fallback


def _format_timestamp(seconds):
    """seconds (float) -> SRT timestamp 'HH:MM:SS,mmm'."""
    millis = max(0, round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _to_srt(segments):
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _format_timestamp(seg["start"])
        end = _format_timestamp(seg["end"])
        lines += [str(i), f"{start} --> {end}", seg["text"].strip(), ""]
    return "\n".join(lines)


def _to_transcript(segments):
    """Join segment texts into one clean, normalized block."""
    text = " ".join(seg["text"].strip() for seg in segments)
    return re.sub(r"\s+", " ", text).strip()


def _yaml_header(meta):
    lines = ["---"]
    for key, value in meta.items():
        if isinstance(value, bool):
            value = "true" if value else "false"
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines)


def transcribe(media_path, model_name, language, compute_type, use_vad, hotwords):
    """Return (segments, duration_seconds).

    The one place that touches faster-whisper, so the engine can be swapped
    (whisper.cpp, a cloud API) later without rewriting the rest of the script.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit(
            "faster-whisper is not installed. Activate the venv, then run:\n"
            "    pip install faster-whisper\n"
            "(it is listed in requirements.txt)."
        )

    model = WhisperModel(model_name, device="cpu", compute_type=compute_type)
    options = {"language": language, "vad_filter": use_vad}
    if hotwords:
        options["hotwords"] = hotwords
    try:
        segments, info = model.transcribe(str(media_path), **options)
    except TypeError:
        # Older faster-whisper builds don't accept hotwords — degrade gracefully.
        options.pop("hotwords", None)
        print("  (installed faster-whisper has no 'hotwords' option — transcribing without it)")
        segments, info = model.transcribe(str(media_path), **options)

    duration = getattr(info, "duration", 0.0)
    print(f"  audio duration: {duration / 60:.1f} min; decoding (CPU runs near real time)...")

    # segments is a lazy generator — realize it into plain dicts.
    out = [{"start": s.start, "end": s.end, "text": s.text} for s in segments]
    if out and not duration:
        duration = out[-1]["end"]
    return out, duration


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe a video/audio file into the brand-voice corpus.",
    )
    parser.add_argument("media", help="path to the video or audio file")
    parser.add_argument("--title", default="", help="human title (used in the header + filename)")
    parser.add_argument("--pillar", default="other", choices=VALID_PILLARS,
                        help="content pillar this video belongs to")
    parser.add_argument("--source-url", default="", help="YouTube / source URL, if any")
    parser.add_argument("--date", default="", help="recording date YYYY-MM-DD (default: today)")
    parser.add_argument("--model", default="distil-large-v3",
                        help="faster-whisper model: distil-large-v3 (default), large-v3, small.en, base.en")
    parser.add_argument("--language", default="en", help="spoken-language code")
    parser.add_argument("--compute-type", default="int8", help="CTranslate2 compute type (CPU: int8)")
    parser.add_argument("--hotwords", default="Fiboprana",
                        help="terms to bias toward, for proper nouns Whisper mishears "
                             "(default: Fiboprana; pass '' to disable)")
    parser.add_argument("--no-vad", action="store_true",
                        help="disable the voice-activity filter (keep all audio, incl. silences)")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR),
                        help="where to write the .md + .srt")
    args = parser.parse_args()

    media_path = Path(args.media).expanduser()
    if not media_path.is_file():
        raise SystemExit(f"File not found: {media_path}")

    date_str = args.date or dt.date.today().isoformat()
    title = args.title or media_path.stem
    slug = _slugify(args.title or media_path.stem, fallback="video")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{date_str}_{slug}.md"
    srt_path = out_dir / f"{date_str}_{slug}.srt"

    print(f"Transcribing {media_path.name} with faster-whisper '{args.model}' (cpu)...")
    segments, duration = transcribe(
        media_path, args.model, args.language, args.compute_type,
        use_vad=not args.no_vad, hotwords=args.hotwords or None,
    )
    if not segments:
        raise SystemExit("No speech transcribed (empty result).")

    for seg in segments:
        seg["text"] = _apply_corrections(seg["text"])

    transcript = _to_transcript(segments)
    meta = {
        "date_recorded": date_str,
        "title": title,
        "pillar": args.pillar,
        "source_url": args.source_url,
        "time_range": f"00:00:00-{_format_timestamp(duration)[:8]}",
        "model": args.model,
        "language": args.language,
        "voice_exemplar": True,   # set false in the file to exclude it from voice prompts
        "do_not_use": "",          # note any lines that should NOT seed the voice
        "transcribed_at": dt.datetime.now().isoformat(timespec="seconds"),
    }

    md_path.write_text(_yaml_header(meta) + "\n\n" + transcript + "\n", encoding="utf-8")
    srt_path.write_text(_to_srt(segments), encoding="utf-8")

    words = len(transcript.split())
    print(f"\nWrote {md_path.relative_to(ROOT)}  ({words} words, ~{duration / 60:.1f} min)")
    print(f"Wrote {srt_path.relative_to(ROOT)}")
    preview = transcript[:400] + ("..." if len(transcript) > 400 else "")
    print("\n--- preview (does this sound like you?) ---")
    print(preview)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
