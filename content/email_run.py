"""Draft the weekly "Notice" email from the latest research digest.

    python -m content.email_run                # draft this week's issue
    python -m content.email_run --dry-run      # build the prompt and exit (no API call)

The drain-pattern partner to content/x_run.py, same trigger: the founder
marking the post-research Q&A done on /week spawns both (the issue is
digest-only, so it never needs to wait on the X batch or a video pick).
Writes the issue into the week's slot in fleet/email_broadcast.json for the
/week email card — status "draft", prior feedback preserved across
regenerations. The send itself stays a founder-approved act (fleet/email_send.py
schedules Thursday 9am MT after the card is approved), and the CTA can be
swapped in session if the week's build ships before Thursday.
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fleet import llm, supabase  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402
from content.notice_prompt import NOTICE_SYSTEM_PROMPT  # noqa: E402
from content.x_run import load_latest_digest  # noqa: E402  (same digest source)

MODEL = llm.model_for("email_notice", "claude-sonnet-4-6")
EMAIL_STATE = ROOT / "fleet" / "email_broadcast.json"
# Live CTA destinations the issue may point at; the prompt picks the most
# on-pattern one. Newest first — add a line when a build ships. Pre-launch
# reality: the landing/waitlist is the only live destination; the core is
# free during beta, so there is never a pricing CTA.
# TODO: add the lead magnet ("The Missing Layer") URL once it has a public
# page, and product pages as they ship.
CTA_DESTINATIONS = [
    "https://fiboprana.com — the landing page and waitlist: see how your mind and body move together, over weeks, with no score; joining the list is how beta invites go out",
]


def build_user_message(digest_md):
    return (
        "Live CTA destinations (pick exactly one, the most on-pattern):\n"
        + "\n".join(f"- {d}" for d in CTA_DESTINATIONS)
        + "\n\nThis week's research digest:\n\n"
        + (digest_md or "").strip()
    )


# The only domains an issue may ever link to (founder rule 2026-08-10: every
# link points at the business or a Fiboprana social post, never external
# curation — sources get named in prose, not linked).
ALLOWED_LINK_DOMAINS = ("fiboprana.com", "youtube.com", "youtu.be", "x.com")
URL_RE = re.compile(r"https?://([^/\s)\"']+)")


def parse_issue(text):
    """Pull the issue JSON out of the model's reply and check every field the
    send script (fleet/email_send.py) depends on. Returns (issue, error)."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None, f"no JSON object in: {text[:200]}"
    try:
        issue = json.loads(text[start:end + 1])
    except ValueError as e:
        return None, f"bad JSON: {e}"
    for field in ("subject", "preview_text", "pattern_md", "cta"):
        if not (issue.get(field) or "").strip():
            return None, f"missing field: {field}"
        if "—" in issue[field]:
            return None, f"em dash in {field} (banned in published copy)"
    for field in ("subject", "preview_text", "pattern_md"):
        if URL_RE.search(issue[field]):
            return None, f"link in {field} — the CTA holds the email's only link"
    bad = [d for d in URL_RE.findall(issue["cta"])
           if not any(d == a or d.endswith("." + a) for a in ALLOWED_LINK_DOMAINS)]
    if bad:
        return None, f"CTA links outside the business: {bad}"
    if "https://fiboprana.com" not in issue["cta"]:
        return None, "CTA must carry one full https://fiboprana.com URL"
    return issue, None


def write_week_slot(issue, digest_row):
    monday = date.today() - timedelta(days=date.today().weekday())
    try:
        state = json.loads(EMAIL_STATE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        state = {}
    old = state.get(str(monday), {})
    state[str(monday)] = {
        "subject": issue["subject"],
        "preview_text": issue["preview_text"],
        "pattern_md": issue["pattern_md"],
        "cta": issue["cta"],
        "written_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "written_by": "content.email_run",
        "digest_id": digest_row["id"],
        "status": "draft",
        "feedback": old.get("feedback", []),
    }
    tmp = EMAIL_STATE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(EMAIL_STATE)
    return str(monday)


def main():
    parser = argparse.ArgumentParser(description="Draft the weekly Notice email from the research digest.")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--dry-run", action="store_true", help="build the prompt and exit (no API call, no cost)")
    args = parser.parse_args()

    try:
        digest = load_latest_digest()
    except SupabaseError as e:
        print(f"Couldn't read the digest from Supabase: {e}", file=sys.stderr)
        return 1
    if not digest:
        print("No research digest on record — run the research first.", file=sys.stderr)
        return 1

    user_message = build_user_message(digest["digest_md"])
    if args.dry_run:
        print(user_message)
        return 0

    result, err = llm.complete(model=args.model, system=NOTICE_SYSTEM_PROMPT,
                               user=user_message, max_tokens=2500, temperature=0.5)
    if err:
        print(f"Generation failed: {err}", file=sys.stderr)
        return 1

    issue, parse_err = parse_issue(result.text)
    if parse_err:
        print(f"Bad issue from the model: {parse_err}", file=sys.stderr)
        return 1

    week = write_week_slot(issue, digest)
    print(f"Drafted Notice for week {week}: \"{issue['subject']}\" — review on the /week email card.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
