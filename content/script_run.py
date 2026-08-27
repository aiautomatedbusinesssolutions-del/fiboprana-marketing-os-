"""The script agent: draft the weekly video script the moment its inputs land.

    python -m content.script_run --week 2026-08-24 --video news
    python -m content.script_run                    # every lane owed a draft
    python -m content.script_run --dry-run          # show inputs, no LLM call

Drain-pattern wire 8 (founder-approved build 2026-08-22): script drafting was
the last big manual step in the video chain. Triggers (fleet/dashboard.py
post_flow_state):

  news/facts done      -> draft the news script from the facts report
  feature/example done -> draft the feature script from the worked example

The draft lands on the script card as status "draft" with the founder's pass
still the gate: nothing downstream (deck/thumbs/pkg fan-out) fires until he
marks the script done, exactly as before. Regenerating after feedback stays a
deliberate in-session act, same as every other artifact.

Inputs per lane:
  news    - picked idea + facts report + news template + the most recent
            earlier news script (voice/format exemplar).
  feature - worked example card + this week's news script (the bridge) +
            feature template + most recent earlier feature script + a
            best-effort fetch of the tool page's own text (never required).
"""

import argparse
import re
import sys
import urllib.request
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from fleet import llm  # noqa: E402
from content.script_prompts import (  # noqa: E402
    SCRIPT_FEATURE_SYSTEM_PROMPT, SCRIPT_NEWS_SYSTEM_PROMPT)
from content.video_artifacts_run import (  # noqa: E402
    EM_DASH, _generate_validated, _now, read_state, write_state)

MODEL = llm.model_for("script_agent", "claude-sonnet-4-6")

SCRIPTS_DIR = ROOT / "videos" / "scripts"
TEMPLATES = ROOT / "videos" / "templates"
# TODO: until Fiboprana ships product/tool pages, the "feature" lane points at
# the landing/waitlist; widen this pattern when real product pages exist.
TOOL_URL_RE = re.compile(r"fiboprana\.com(?:/[\w-]+)*")


def _monday():
    return str(date.today() - timedelta(days=date.today().weekday()))


def _slugify(text, fallback):
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48]
    return slug or fallback


def prior_script(state, week, video):
    """The most recent earlier week's script for this lane (the exemplar)."""
    for wk in sorted(state.keys(), reverse=True):
        if wk >= week:
            continue
        md = ((state[wk].get(video) or {}).get("script") or {}).get("script_md")
        if md:
            return md
    return None


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data):
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def fetch_tool_text(url, cap=6000):
    """Best-effort: the live tool page's visible text (shipped copy). A JS-only
    page or any error returns None and the script leans on the example card."""
    try:
        req = urllib.request.Request(f"https://{url}", headers={
            "User-Agent": "fiboprana-fleet/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read(400_000).decode("utf-8", "replace")
        p = _Text()
        p.feed(html)
        text = "\n".join(p.parts)
        return text[:cap] if len(text) > 200 else None
    except Exception:  # noqa: BLE001 - strictly optional input
        return None


def validate_script(md, video):
    md = (md or "").strip()
    if md.startswith("```"):
        return "wrapped in a code fence - output the raw markdown only"
    if not md.startswith("# "):
        return "must start with the H1 title"
    if EM_DASH in md:
        return "em dash in the output"
    if len(md) < 3000:
        return "too short to be a full script draft"
    low = md.lower()
    if "verbatim" not in low and "word for word" not in low:
        return "hook section must be marked verbatim / word for word"
    if "fiboprana" not in low:
        return "missing the Fiboprana close"
    if "tell me below" not in low and "in the comments" not in low:
        return "missing the reply-loop question"
    # TODO: tighten to a product-page path once Fiboprana ships tool/feature
    # pages; the landing/waitlist URL is the gate until then.
    if video == "feature" and "fiboprana.com" not in low:
        return "feature script must name the live page URL"
    return None


def build_user_message(state, week, video, slot):
    """(user_message, slug) or (None, error)."""
    template = (TEMPLATES / f"{'news_video' if video == 'news' else 'feature_video'}.md"
                ).read_text(encoding="utf-8")
    exemplar = prior_script(state, week, video) or "(no earlier script - follow the template)"
    parts = [f"WEEK: {week} (fill this into the header)"]

    if video == "news":
        picked = slot.get("picked")
        idea = next((i for i in slot.get("ideas", []) if i.get("id") == picked), None)
        facts = (slot.get("facts") or {}).get("report_md")
        if not idea or not facts:
            return None, "needs a picked idea and a verified facts report"
        parts.append(f"THE PICKED IDEA:\ntitle: {idea.get('title')}\n"
                     f"angle: {idea.get('angle')}\n"
                     f"why starred: {slot.get('why_star') or ''}")
        parts.append(f"VERIFIED FACTS REPORT:\n<<<FACTS\n{facts}\nFACTS>>>")
        slug = _slugify(idea.get("title"), f"news-{week}")
    else:
        example = (slot.get("example") or {}).get("example_md")
        if not example:
            return None, "needs the worked-example card first"
        news_script = ((state.get(week, {}).get("news") or {}).get("script")
                       or {}).get("script_md") or "(news script not written yet)"
        parts.append(f"WORKED-EXAMPLE CARD (the demo truth):\n<<<EXAMPLE\n{example}\nEXAMPLE>>>")
        parts.append(f"THIS WEEK'S NEWS SCRIPT (the bridge):\n<<<NEWS\n{news_script[:8000]}\nNEWS>>>")
        m = TOOL_URL_RE.search(example) or TOOL_URL_RE.search(news_script)
        if m:
            parts.append(f"TOOL URL: {m.group(0)}")
            tool_text = fetch_tool_text(m.group(0))
            if tool_text:
                parts.append(f"TOOL PAGE TEXT (shipped copy, best-effort fetch):\n"
                             f"<<<TOOL\n{tool_text}\nTOOL>>>")
        slug = _slugify((m.group(0).rsplit("/", 1)[-1] + "-demo") if m
                        else f"feature-{week}", f"feature-{week}")

    parts.append(f"THE LOCKED TEMPLATE:\n<<<TEMPLATE\n{template}\nTEMPLATE>>>")
    parts.append(f"THE MOST RECENT {video.upper()} SCRIPT (voice + format exemplar):\n"
                 f"<<<EXEMPLAR\n{exemplar}\nEXEMPLAR>>>")
    return "\n\n".join(parts), slug


def run(week, video, dry_run=False):
    state = read_state()
    slot = state.get(week, {}).get(video)
    if not slot:
        return f"{video}: SKIP (no slot for week {week})"
    if (slot.get("script") or {}).get("script_md"):
        return f"{video}: SKIP (script exists; regenerate in session after feedback)"
    built, slug = build_user_message(state, week, video, slot)
    if built is None:
        return f"{video}: SKIP ({slug})"  # slug carries the reason here
    if dry_run:
        return (f"{video}: DRY RUN - would draft '{slug}' with "
                f"{len(built)} chars of inputs")

    system = SCRIPT_NEWS_SYSTEM_PROMPT if video == "news" else SCRIPT_FEATURE_SYSTEM_PROMPT

    def parse(text):
        md = text.strip()
        verr = validate_script(md, video)
        return (md, None) if verr is None else (None, verr)

    md, gerr = _generate_validated(system, built, max_tokens=8000,
                                   temperature=0.5, parse=parse, model=MODEL)
    # state may have moved while the model ran (other cards write too) -
    # re-read before writing, same slot-scoped write as the other artifacts.
    state = read_state()
    slot = state.get(week, {}).get(video) or {}
    slot.pop("script_generating_since", None)
    if gerr:
        state.setdefault(week, {})[video] = slot
        write_state(state)
        return f"{video}: FAIL ({gerr})"
    out = SCRIPTS_DIR / f"{slug}.md"
    if out.exists():  # never clobber a hand-written script file
        out = SCRIPTS_DIR / f"{slug}-{week}.md"
    out.write_text(md + "\n", encoding="utf-8")
    slot["script"] = {
        "status": "draft", "written_at": _now(),
        "written_by": "content.script_run",
        "file": str(out.relative_to(ROOT)).replace("\\", "/"),
        "script_md": md,
        "feedback": (slot.get("script") or {}).get("feedback", []),
    }
    state.setdefault(week, {})[video] = slot
    write_state(state)
    return f"{video}: ok ({out.name}, {len(md)} chars) - awaiting founder pass"


def main():
    parser = argparse.ArgumentParser(description="Draft weekly video scripts.")
    parser.add_argument("--week", default=None, help="Monday of the week (default: current)")
    parser.add_argument("--video", default=None, choices=["news", "feature"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    week = args.week or _monday()
    lanes = [args.video] if args.video else ["news", "feature"]
    failed = False
    for lane in lanes:
        msg = run(week, lane, dry_run=args.dry_run)
        print(msg)
        failed = failed or msg.split(": ", 1)[1].startswith("FAIL")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
