"""Generate the weekly batch of X posts from the latest research digest.

    python -m content.x_run                  # default count, from the latest digest
    python -m content.x_run --count 10
    python -m content.x_run --dry-run         # build the prompt and exit (no API call / no cost)
    python -m content.x_run --quiz-url ... --tool-url ... --themes "..."

Reads the latest research digest from Supabase (marketing.research_runs — where the
fleet research agent persists each weekly run), generates a batch of short-form X
posts across the content pillars (~1 in 5 a soft CTA; default 14 = two a day for the
week), and writes a scheduler-agnostic batch to content/x_batches/<date>.md: plain-text
posts to paste into Typefully/Publer, plus per-post metadata (pillar, CTA link-reply,
suggested day). Also writes the week's slot in fleet/x_batch.json so the /week
dashboard card can show the posts and collect founder feedback (prior feedback on the
week is preserved across regenerations). No DB writes; the posts/outcome store comes
later. This is the same generation interface a fleet agent will call.
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Windows consoles default to cp1252; a stray currency symbol in the digest
# would crash the summary prints AFTER the batch files were already written.
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(errors="replace")

from attribution import autolink  # noqa: E402
from fleet import llm, supabase  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402
from content.weekly_x_prompt import WEEKLY_X_SYSTEM_PROMPT, PILLARS  # noqa: E402
from content import utils as content_utils  # noqa: E402

DEFAULT_COUNT = 14  # two short posts a day for the week
# CTA destinations: the email-list funnel is the primary target until free
# tools/resources ship on fiboprana.com — update these when they do.
DEFAULT_QUIZ_URL = "https://fiboprana.com"
DEFAULT_TOOL_URL = "https://fiboprana.com"
MODEL = llm.model_for("x_batch", "claude-sonnet-4-6")
BATCH_DIR = Path(__file__).resolve().parent / "x_batches"
X_BATCH_STATE = ROOT / "fleet" / "x_batch.json"  # /week dashboard card reads this
_PILLAR_LABELS = dict(PILLARS)


def load_latest_digest():
    """Return the most recent research digest from Supabase, or None.

    Reads marketing.research_runs — where the fleet research agent
    (`python -m fleet.research_run`) persists each weekly run. Returns a dict with
    id / generated_at / digest_md (generated_at maps from the row's created_at) so
    the rest of this module is unchanged. (This used to read radar/seen.db, the OLD
    local SQLite store — the cloud agent writes to Supabase, not that file, so the
    local copy went stale.)
    """
    rows = supabase.select("research_runs", params={
        "select": "id,created_at,digest_md",
        "order": "created_at.desc",
        "limit": 1,
    })
    if not rows:
        return None
    row = rows[0]
    return {"id": row["id"], "generated_at": row["created_at"], "digest_md": row["digest_md"]}


def format_batch_markdown(posts, digest_row):
    """Render the batch as a scheduler-paste file: each post + its metadata."""
    cta = sum(1 for p in posts if p["is_cta"])
    over = sum(1 for p in posts if len(p["text"]) > 280)
    header_note = f"{len(posts)} posts, {cta} CTA" + (
        f", {over} over 280 (trim before scheduling)." if over else "."
    )
    lines = [
        f"# X post batch — {datetime.now():%Y-%m-%d}",
        "",
        f"> From digest #{digest_row['id']} ({digest_row['generated_at']}). {header_note}",
        "> Paste each **Post** into your scheduler (Typefully/Publer), 2/day. For CTA posts, "
        "post it, then add the **Link reply** as a reply to your own post (dodges X's link "
        "throttle). Plain text, no markdown — copies clean.",
        "",
    ]
    for i, p in enumerate(posts, 1):
        label = _PILLAR_LABELS.get(p["pillar"], p["pillar"])
        tag = " · CTA" if p["is_cta"] else ""
        n = len(p["text"])
        length = f"TRIM {n}>280" if n > 280 else f"{n} chars"
        lines += [f"## {i}. {label}{tag}  (suggested: {p['suggested_day']}; {length})",
                  "", "Post:", p["text"]]
        if p["is_cta"] and p.get("link_reply"):
            lines += ["", "Link reply:", p["link_reply"]]
        lines.append("")
    return "\n".join(lines)


def write_week_state(posts, digest_row, batch_file):
    """Write this week's batch into fleet/x_batch.json for the /week dashboard.

    Keyed by the week's Monday (same convention as the dashboard's other state
    files). Regenerating replaces the posts but keeps any feedback the founder
    already left on the week — the notes are what drove the regeneration.
    """
    monday = date.today() - timedelta(days=date.today().weekday())
    try:
        state = json.loads(X_BATCH_STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    old = state.get(str(monday), {})
    state[str(monday)] = {
        "posts": [{
            "pillar": p["pillar"],
            "pillar_label": _PILLAR_LABELS.get(p["pillar"], p["pillar"]),
            "is_cta": p["is_cta"],
            "text": p["text"],
            "link_reply": p.get("link_reply"),
            "suggested_day": p["suggested_day"],
        } for p in posts],
        "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "written_by": "content.x_run",
        "batch_file": batch_file,
        "digest_id": digest_row["id"],
        "feedback": old.get("feedback", []),
    }
    tmp = X_BATCH_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(X_BATCH_STATE)


ECHO_THRESHOLD = 0.30  # shingle-overlap Jaccard above this = near-duplicate
RECENT_BATCHES = 3     # how many past weekly batches form the no-echo corpus


def _recent_post_texts():
    """Post texts from the last few weekly batch files - the no-echo corpus.

    X silently rejects posts substantially similar to an account's past posts
    (a sibling account lost 4 of 13 at scheduler fire time), so the generator
    sees what already went out and a mechanical check drops echoes it writes
    anyway. The batch files ARE what got scheduled (Typefully pulls from
    them), so they're the honest corpus and work offline."""
    texts = []
    for path in sorted(BATCH_DIR.glob("*.md"), reverse=True)[:RECENT_BATCHES]:
        try:
            body = path.read_text(encoding="utf-8-sig")
        except OSError:
            continue
        for chunk in body.split("\nPost:\n")[1:]:
            for stop in ("\nLink reply:", "\n## "):
                chunk = chunk.split(stop)[0]
            text = chunk.strip()
            if text:
                texts.append(text)
    return texts


def drop_unpostable(posts, corpus):
    """Enforce the two mechanical batch rules, returning (kept, notes).

    Over-280 posts are DROPPED, never trimmed: X Premium would post them but
    the feed collapses them behind Show more, which kills the hook. Echoes of
    the recent corpus are dropped because X quietly suppresses near-dupes."""
    kept, notes = [], []
    for p in posts:
        if len(p["text"]) > 280:
            notes.append(f"dropped ({len(p['text'])} chars): {p['text'][:60]}...")
            continue
        echo = next((c for c in corpus
                     if content_utils.shingle_overlap(p["text"], c) >= ECHO_THRESHOLD),
                    None)
        if echo is not None:
            notes.append(f"dropped (echoes a recent post): {p['text'][:60]}...")
            continue
        kept.append(p)
    return kept, notes


def main():
    parser = argparse.ArgumentParser(
        description="Generate the weekly X post batch from the research digest."
    )
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT,
                        help=f"posts to generate (default {DEFAULT_COUNT})")
    parser.add_argument("--quiz-url", default=DEFAULT_QUIZ_URL)
    parser.add_argument("--tool-url", default=DEFAULT_TOOL_URL)
    parser.add_argument("--themes", default=None, help="optional Reddit listening themes for flavor")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--dry-run", action="store_true",
                        help="build the prompt and exit (no API call, no cost)")
    args = parser.parse_args()

    try:
        digest = load_latest_digest()
    except SupabaseError as e:
        print(f"Couldn't read the digest from Supabase: {e}", file=sys.stderr)
        return 1
    if digest is None:
        print("No research digest in Supabase. Run `python -m fleet.research_run` first.",
              file=sys.stderr)
        return 1
    print(f"Using digest from {digest['generated_at']} (id {digest['id']}).")

    # Optional voice inputs (ported from the personal-brand workflow
    # 2026-08-22): the founder's own weekly takes and the mechanism log of
    # what has measurably worked. Missing/empty files simply drop the section.
    def _read_optional(path):
        try:
            text = path.read_text(encoding="utf-8-sig").strip()
            return text if text and "(no takes this week)" not in text else None
        except OSError:
            return None

    founder_takes = _read_optional(ROOT / "fleet" / "founder_takes.md")
    pattern_log = _read_optional(ROOT / "content" / "x_pattern_log.md")
    recent_posts = _recent_post_texts()

    user_message = content_utils.format_digest_for_weekly_x_prompt(
        digest["digest_md"], args.count, args.quiz_url, args.tool_url, args.themes,
        founder_takes=founder_takes, pattern_log=pattern_log,
        recent_posts=recent_posts,
    )

    if args.dry_run:
        print("\n--- dry run: user message that would be sent ---\n")
        print(user_message)
        return 0

    print(f"Generating {args.count} posts with {args.model} ...")
    result, err = llm.complete(model=args.model, system=WEEKLY_X_SYSTEM_PROMPT,
                               user=user_message, max_tokens=6000, temperature=0.8)
    if err:
        print(err, file=sys.stderr)
        return 1
    if result.truncated:
        print("Warning: response hit the token cap — the batch may be incomplete.",
              file=sys.stderr)
    raw = result.text

    try:
        posts = content_utils.parse_weekly_x_response(raw, expected_count=args.count)
    except ValueError as e:
        print(f"AI response wasn't valid: {e}", file=sys.stderr)
        return 1

    posts, drop_notes = drop_unpostable(posts, recent_posts)
    for note in drop_notes:
        print(note, file=sys.stderr)
    if not posts:
        print("Every post was dropped (length/echo) - regenerate.", file=sys.stderr)
        return 1

    # Auto-attach tracking: every CTA link reply leaves here as a
    # fiboprana.com/r/<code> short link stamped with this batch's identity
    # (campaign = the week, content = the slot), so each posted link's
    # clicks are attributable to the exact post. Fail-open — a Supabase
    # hiccup ships the bare URL instead of costing the batch.
    monday = date.today() - timedelta(days=date.today().weekday())
    for i, p in enumerate(posts, 1):
        if p.get("link_reply"):
            p["link_reply"] = autolink.rewrite_urls(
                p["link_reply"], source="x", medium="post",
                campaign=f"weekly-x-{monday}", content=f"slot-{i}",
                post_text=p["text"])

    md = format_batch_markdown(posts, digest)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)
    out_path = BATCH_DIR / f"{datetime.now():%Y-%m-%d}.md"
    out_path.write_text(md, encoding="utf-8")
    write_week_state(posts, digest, out_path.name)

    cta = sum(1 for p in posts if p["is_cta"])
    over = sum(1 for p in posts if len(p["text"]) > 280)
    suffix = f", {over} over 280 — trim" if over else ""
    print(f"\nWrote {len(posts)} posts ({cta} CTA{suffix}) to {out_path}")
    print(f"Week slot updated in {X_BATCH_STATE.name} — review on the /week dashboard.\n")
    print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
