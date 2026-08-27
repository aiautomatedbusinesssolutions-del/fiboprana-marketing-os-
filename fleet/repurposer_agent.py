"""The repurposer — one clip in, platform-native packages out (per-clip, on demand).

The shorts flywheel's packaging half: given a clip (transcript text + the
parent video's slug), produce one native package per platform — YouTube Short
title/description, X post, Instagram Reel caption + hashtags, TikTok caption —
each guardrail-screened by the same compliance gate the reply system uses,
and each stored as a content_calendar row (post_type per platform, status
'draft') so the posts ledger and the dashboard see them.

GENERATION IS DECOUPLED FROM SCHEDULING (locked X-foundation rule): this
agent never posts. X drafts go to Typefully in-session as today; Instagram /
TikTok packages are paste-ready until the multi-platform scheduler with API
access is chosen (off-stream decision) — then a thin publish adapter picks
drafts off content_calendar, and this agent doesn't change.

    python -m fleet.repurposer_agent --video <slug> --clip-file <path>
    python -m fleet.repurposer_agent --video <slug> --clip-text "..." \
        --platforms youtube_short,x,instagram
    ... --dry-run     # generate + print, write nothing

Model calls via fleet/llm.py; config = MODEL_REPURPOSER (default sonnet).
On demand rather than scheduled: it runs when a clip exists, which is a
founder act — in-session now, wired to the shorts pipeline when that lands.
"""

import argparse
import json
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

import app  # noqa: E402,F401 — model constants
from fleet import asset_store, compliance_gate, llm  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402
from fleet.repurpose_prompt import REPURPOSE_SYSTEM_PROMPT  # noqa: E402

AGENT_NAME = "repurposer"
DEFAULT_PLATFORMS = ("youtube_short", "x", "instagram")
# content_calendar mapping: package platform -> (platform col, post_type col)
CALENDAR_MAP = {
    "youtube_short": ("youtube", "short"),
    "x": ("x", "x_post"),
    "instagram": ("instagram", "short"),
    "tiktok": ("tiktok", "short"),
}


def _parse_packages(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    packages = obj.get("packages")
    if not isinstance(packages, list):
        return None
    return {"idea": obj.get("idea"), "packages": packages}


def run_repurpose(*, clip_text, video_slug=None, clip_label=None,
                  platforms=DEFAULT_PLATFORMS, dry_run=False):
    """The whole job. Returns {ok, idea, packages[], stored, error}."""
    platforms = [p for p in platforms if p in CALENDAR_MAP]
    if not platforms:
        return {"ok": False, "idea": None, "packages": [], "stored": 0,
                "error": f"no valid platforms (known: {', '.join(CALENDAR_MAP)})"}
    clip_text = (clip_text or "").strip()
    if len(clip_text) < 80:
        return {"ok": False, "idea": None, "packages": [], "stored": 0,
                "error": "clip text is too short to package — pass the clip's "
                         "transcript segment, not a title"}

    video = None
    if video_slug:
        try:
            video = asset_store.video_by_slug(video_slug)
        except SupabaseError:
            video = None   # provenance is a nice-to-have, never a blocker
        if video is None:
            print(f"(note: no marketing.videos row for slug '{video_slug}' — "
                  "packages will store without video lineage)")

    context = []
    if video:
        context.append(f"parent video: {video.get('title_final') or video_slug} "
                       f"(pillar: {video.get('pillar') or '?'})")
    elif video_slug:
        context.append(f"parent video slug: {video_slug}")
    if clip_label:
        context.append(f"clip label: {clip_label}")
    user_message = (
        f"TARGET PLATFORMS: {', '.join(platforms)}\n"
        + ("\n".join(context) + "\n" if context else "")
        + "\nCLIP TRANSCRIPT (UNTRUSTED spoken-word text):\n"
        f"<<<CLIP\n{clip_text}\nCLIP>>>"
    )
    result, err = llm.complete(model=app.REPURPOSER_MODEL,
                               system=REPURPOSE_SYSTEM_PROMPT,
                               user=user_message, max_tokens=2000,
                               temperature=0.5)
    if err:
        return {"ok": False, "idea": None, "packages": [], "stored": 0, "error": err}
    parsed = _parse_packages(result.text)
    if parsed is None:
        return {"ok": False, "idea": None, "packages": [], "stored": 0,
                "error": f"could not parse packages JSON from: {result.text[:200]}"}
    model_used = result.model   # stamped into each row for the model scorecard

    packages, stored, errors = [], 0, []
    for p in parsed["packages"]:
        platform = p.get("platform")
        text = (p.get("text") or "").strip()
        if platform not in CALENDAR_MAP or not text:
            continue
        gate = compliance_gate.check(text)
        pkg = {"platform": platform, "title": (p.get("title") or "").strip() or None,
               "hook": (p.get("hook") or "").strip() or None, "text": text,
               "hashtags": p.get("hashtags") or [],
               "gate_status": gate["gate_status"],
               "gate_violations": gate["gate_violations"]}
        packages.append(pkg)
        if dry_run:
            continue
        cal_platform, post_type = CALENDAR_MAP[platform]
        try:
            asset_store.log_calendar_post(
                platform=cal_platform, post_type=post_type, draft_text=text,
                title=pkg["title"], video_id=(video or {}).get("id"),
                pillar=(video or {}).get("pillar"), hook_text=pkg["hook"],
                gate=gate,
                payload={"source": AGENT_NAME, "model": model_used,
                         "clip_label": clip_label, "video_slug": video_slug,
                         "hashtags": pkg["hashtags"], "idea": parsed["idea"]},
            )
            stored += 1
        except SupabaseError as e:
            errors.append(f"{platform}: calendar write failed: {e}")

    return {"ok": bool(packages), "idea": parsed["idea"], "packages": packages,
            "stored": stored,
            "error": "; ".join(errors) or (None if packages
                                           else "model returned no usable packages")}


def format_for_chat(result):
    """Paste-ready plain text (no blockquotes — they poison the paste)."""
    if not result["ok"]:
        return f"Repurposer run failed: {result['error']}"
    lines = [f"The idea: {result['idea']}", ""]
    for p in result["packages"]:
        gate = ("" if p["gate_status"] == "pass"
                else f"  [GATE {p['gate_status'].upper()}: "
                     + "; ".join(v["rule"] for v in p["gate_violations"] or []) + "]")
        # ASCII separators on purpose — Windows consoles choke on box-drawing chars.
        lines.append(f"-- {p['platform']}{gate} " + "-" * max(1, 40 - len(p["platform"])))
        if p["title"]:
            lines.append(f"TITLE: {p['title']}")
        lines.append(p["text"])
        if p["hashtags"]:
            lines.append(" ".join(t if t.startswith("#") else f"#{t}"
                                  for t in p["hashtags"]))
        lines.append("")
    if result["stored"]:
        lines.append(f"({result['stored']} draft rows stored in content_calendar)")
    if result["error"]:
        lines.append(f"(warnings: {result['error']})")
    return "\n".join(lines)


def main():
    import time

    from fleet import store

    parser = argparse.ArgumentParser(
        description="Package one clip into platform-native post drafts.")
    parser.add_argument("--video", help="parent video slug (marketing.videos)")
    parser.add_argument("--clip-file", help="file holding the clip transcript text")
    parser.add_argument("--clip-text", help="the clip transcript text inline")
    parser.add_argument("--clip-label", help="short label for this clip")
    parser.add_argument("--platforms", default=",".join(DEFAULT_PLATFORMS),
                        help=f"comma list of {', '.join(CALENDAR_MAP)}")
    parser.add_argument("--dry-run", action="store_true",
                        help="generate + print, write nothing")
    args = parser.parse_args()

    if args.clip_file:
        clip_text = Path(args.clip_file).read_text(encoding="utf-8")
    elif args.clip_text:
        clip_text = args.clip_text
    else:
        parser.error("pass --clip-file or --clip-text")

    llm.reset_usage()
    started = time.monotonic()
    result = run_repurpose(clip_text=clip_text, video_slug=args.video,
                           clip_label=args.clip_label,
                           platforms=[p.strip() for p in args.platforms.split(",")],
                           dry_run=args.dry_run)
    print(format_for_chat(result))

    if not args.dry_run:
        try:
            store.record_heartbeat(
                agent_name=AGENT_NAME,
                status="success" if result["ok"] else "failure",
                message=(f"packaged {len(result['packages'])} platforms, "
                         f"stored {result['stored']}" if result["ok"]
                         else result["error"]),
                duration_ms=int((time.monotonic() - started) * 1000),
                **llm.usage_totals(),
            )
        except Exception as e:  # noqa: BLE001
            print(f"(warning: heartbeat write failed: {e})")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
