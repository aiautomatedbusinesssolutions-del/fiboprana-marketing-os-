"""The review CLI — my cockpit for the reply loop. The one piece I drive by hand.

The finder, judge, and drafter run on their own and leave judged candidates with
2-3 gated draft variants in Supabase. This is where I take over: review a candidate,
pick a variant and edit it in $EDITOR (or write my own), tag WHY I changed it, and it
captures the draft->final diff into the ledger — the voice-training signal. Then it
hands me paste-ready text; I post by hand on Reddit and attach the permalink.

Login safety (REPLY_RUNBOOK §1): this NEVER posts. It prints text for me to paste.
The SEND stays human; automation never touches the live account.

    python -m fleet.reply_review                 # review the queue, one at a time
    python -m fleet.reply_review review <id>     # review one specific candidate
    python -m fleet.reply_review queue           # just list what's waiting
    python -m fleet.reply_review url <ledger_id> <permalink>   # attach the link after posting
    python -m fleet.reply_review outcome <ledger_id> --score 14 --op-replied yes
    python -m fleet.reply_review recent          # recent sent replies

The interactive bits (prompts + $EDITOR) are injected, so the flow is testable and
the same review_one() could later back an MCP/web review surface.
"""

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fleet import publish, reply_store  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402

_EDIT_TYPES = ("none", "voice", "substance", "both", "rejected")
# predicted_grade retired 2026-08-17 (founder call): the learning loop is
# finder/judge scores vs checker outcomes; the ledger column stays for the
# pre-08-17 rows but nothing prompts for it anymore.
_RULE = "-" * 60


# ── $EDITOR ──────────────────────────────────────────────────────────────────
def _open_editor(seed_text):
    """Open $EDITOR (or notepad/vi) on a temp file seeded with the draft, then read
    it back. Returns the edited text, or None if the editor couldn't be launched."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or (
        "notepad" if os.name == "nt" else "vi")
    fd, path = tempfile.mkstemp(suffix=".md", prefix="reply_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(seed_text or "")
        try:
            subprocess.call(shlex.split(editor, posix=(os.name != "nt")) + [path])
        except (OSError, ValueError) as e:
            print(f"  couldn't launch editor ({editor}): {e}. "
                  "Set $EDITOR and try again.", file=sys.stderr)
            return None
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ── formatting (ASCII-safe; no markdown blockquotes — they paste a vertical bar) ─
def _trim(text, n):
    text = (text or "").strip()
    return text if len(text) <= n else text[:n] + "..."


def format_candidate(c):
    lines = [_RULE,
             f"{c.get('community') or '(unknown)'}  |  judge {c.get('judge_verdict') or '?'}"
             f" ({c.get('judge_score')})  |  audience {c.get('audience_fit') or '?'}",
             f"TITLE: {(c.get('post_title') or '(no title)').strip()}"]
    excerpt = _trim(c.get("post_excerpt"), 600)
    if excerpt:
        lines.append(f"POST:  {excerpt}")
    if c.get("judge_rationale"):
        lines.append(f"WHY:   {c['judge_rationale'].strip()}")
    if c.get("post_url"):
        lines.append(f"URL:   {c['post_url']}")
    lines.append(f"id:    {c.get('id')}")
    return "\n".join(lines)


def format_variants(visible, hidden):
    out = []
    for i, d in enumerate(visible, 1):
        gate = d.get("gate_status") or "?"
        tag = f"[{i}] ({gate.upper()})"
        if d.get("angle"):
            tag += f" {d['angle']}"
        out.append(tag)
        viol = d.get("gate_violations") or []
        if viol:
            out.append("    flagged: " + ", ".join(
                f"{v.get('rule')}:{v.get('match')}" for v in viol))
        out.append("    " + (d.get("draft_text") or "").strip().replace("\n", "\n    "))
        out.append("")
    if hidden:
        out.append(f"  ({hidden} variant(s) hidden — hard-blocked by the compliance floor)")
    return "\n".join(out)


def format_diff(ai_draft, final_sent):
    delta, _dist, ratio = reply_store._diff_stats(ai_draft, final_sent)
    head = f"  edit_ratio {ratio} (1.0 = sent verbatim)"
    return head if not delta else head + "\n" + "\n".join("  " + ln for ln in delta.splitlines())


def format_logged(ledger, final_sent, platform="reddit"):
    lid = ledger.get("id") if isinstance(ledger, dict) else None
    ratio = ledger.get("edit_ratio") if isinstance(ledger, dict) else None
    return (f"  logged to the ledger (id {lid}, edit_ratio {ratio}).\n"
            + publish.handoff(platform, final_sent, ledger_id=lid))


# ── interactive prompts (each takes prompt_fn so the flow is testable) ───────
def _choose(prompt_fn, n):
    while True:
        raw = prompt_fn(f"  pick 1-{n}, [w]rite own, [s]kip, [d]ismiss, [q]uit: ").strip().lower()
        if raw in ("w", "s", "d", "q"):
            return raw
        if raw.isdigit() and 1 <= int(raw) <= n:
            return int(raw)


def _prompt_choice(prompt_fn, label, options, default=None):
    allowed = "/".join(options)
    suffix = f" (default {default})" if default else " (Enter to skip)"
    while True:
        raw = prompt_fn(f"  {label} [{allowed}]{suffix}: ").strip().lower()
        if not raw:
            return default
        if raw in options:
            return raw


def _prompt_bool(prompt_fn, label):
    while True:
        raw = prompt_fn(f"  {label} [y/n] (Enter to skip): ").strip().lower()
        if not raw:
            return None
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False


# ── the heart: review one candidate ─────────────────────────────────────────
def review_one(candidate, *, prompt_fn=input, editor_fn=_open_editor, out=print):
    """Show one candidate + its variants, take my pick + edit + tags, log the sent
    reply with the captured diff. Returns 'quit' to stop the session, else None.
    Never posts — prints paste-ready text at the end."""
    out(format_candidate(candidate))
    try:
        drafts = reply_store.drafts_for_candidate(candidate["id"])
    except SupabaseError as e:
        out(f"  couldn't load drafts: {e}")
        return None

    visible = [d for d in drafts if d.get("gate_status") != "block"]
    hidden = len(drafts) - len(visible)
    if not visible:
        out("  no postable variants (all hard-blocked or none drafted). Leaving it.")
        return None
    out(format_variants(visible, hidden))

    choice = _choose(prompt_fn, len(visible))
    if choice == "q":
        return "quit"
    if choice == "s":
        out("  skipped — left in the queue.")
        return None
    if choice == "d":
        reason = prompt_fn("  dismiss reason (optional): ").strip() or None
        try:
            reply_store.dismiss_candidate(candidate["id"], reason=reason)
            out("  dismissed.")
        except SupabaseError as e:
            out(f"  dismiss failed: {e}")
        return None

    if choice == "w":
        chosen, seed = None, ""
    else:
        chosen = visible[choice - 1]
        seed = chosen.get("draft_text") or ""

    final = editor_fn(seed)
    if final is None:
        out("  editor didn't open — candidate left in the queue.")
        return None
    if not final.strip():
        out("  empty reply — skipped, candidate left in the queue.")
        return None

    if chosen:
        out(format_diff(chosen.get("draft_text") or "", final))
        default_type = "none" if (chosen.get("draft_text") or "").strip() == final.strip() else "voice"
    else:
        default_type = "rejected"

    edit_type = _prompt_choice(prompt_fn, "edit_type", _EDIT_TYPES, default=default_type)
    intent = _prompt_bool(prompt_fn, "intent preserved?")
    style_notes = prompt_fn("  style note (one line — why you changed it): ").strip() or None
    url = prompt_fn("  Reddit permalink once posted (Enter to add later): ").strip() or None

    try:
        ledger = reply_store.log_sent_reply(
            candidate_id=candidate["id"],
            draft_id=chosen.get("id") if chosen else None,
            final_sent=final,
            edit_type=edit_type,
            intent_preserved=intent,
            style_notes=style_notes,
            reply_url=url,
        )
    except (SupabaseError, ValueError) as e:
        out(f"  LEDGER WRITE FAILED — not logged: {e}")
        return None
    out(format_logged(ledger, final, platform=candidate.get("platform") or "reddit"))
    return None


# ── subcommands ──────────────────────────────────────────────────────────────
def cmd_review(args):
    try:
        if args.candidate_id:
            cand = reply_store.get_candidate(args.candidate_id)
            if not cand:
                print(f"No candidate {args.candidate_id}.")
                return 1
            queue = [cand]
        else:
            queue = reply_store.review_queue(platform=args.platform, limit=args.limit)
    except SupabaseError as e:
        print(f"Lookup failed: {e}")
        return 1
    if not queue:
        print("Nothing to review — no candidates with drafts ready. "
              "Run the finder + drafter first.")
        return 0
    print(f"{len(queue)} candidate(s) ready for review.\n")
    for cand in queue:
        if review_one(cand) == "quit":
            print("\nStopped. The rest stay in the queue.")
            break
        print()
    return 0


def cmd_queue(args):
    try:
        queue = reply_store.review_queue(platform=args.platform, limit=args.limit)
    except SupabaseError as e:
        print(f"Lookup failed: {e}")
        return 1
    if not queue:
        print("Review queue empty.")
        return 0
    print(f"=== Review queue ({len(queue)}) ===")
    for c in queue:
        print(f"  [{c.get('judge_score')}] {c.get('community')} — "
              f"{_trim(c.get('post_title'), 70)}")
        print(f"        id {c.get('id')}")
    return 0


def cmd_url(args):
    try:
        updated = reply_store.set_reply_url(args.ledger_id, args.url)
    except SupabaseError as e:
        print(f"Failed: {e}")
        return 1
    print("Permalink attached." if updated else "No ledger row updated — check the id.")
    return 0


def cmd_outcome(args):
    try:
        updated = reply_store.update_outcome(
            args.ledger_id, outcome_score=args.score, op_replied=args.op_replied or None,
            led_to_profile=args.led_to_profile or None, outcome_notes=args.note or None)
    except SupabaseError as e:
        print(f"Failed: {e}")
        return 1
    print("Outcome saved." if updated else "No ledger row updated — check the id.")
    return 0


def cmd_recent(args):
    try:
        rows = reply_store.recent_sent(limit=args.limit, platform=args.platform or None)
    except SupabaseError as e:
        print(f"Lookup failed: {e}")
        return 1
    if not rows:
        print("No sent replies logged yet.")
        return 0
    print(f"=== Recent sent ({len(rows)}) ===")
    for r in rows:
        print(f"  r={r.get('edit_ratio')} "
              f"{r.get('community')} — {_trim(r.get('post_title'), 60)}")
        print(f"        id {r.get('id')}  outcome={r.get('outcome_score')}  "
              f"url={r.get('reply_url') or '(none)'}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Review, edit, and log reply drafts (you post by hand).")
    sub = parser.add_subparsers(dest="cmd")

    p_review = sub.add_parser("review", help="review the queue (or one candidate)")
    p_review.add_argument("candidate_id", nargs="?", help="one candidate id (else drain the queue)")
    p_review.add_argument("--platform", default="reddit")
    p_review.add_argument("--limit", type=int, default=20)
    p_review.set_defaults(func=cmd_review)

    p_queue = sub.add_parser("queue", help="list candidates with drafts ready")
    p_queue.add_argument("--platform", default="reddit")
    p_queue.add_argument("--limit", type=int, default=20)
    p_queue.set_defaults(func=cmd_queue)

    p_url = sub.add_parser("url", help="attach the permalink after you post")
    p_url.add_argument("ledger_id")
    p_url.add_argument("url")
    p_url.set_defaults(func=cmd_url)

    p_out = sub.add_parser("outcome", help="record a sent reply's outcome (weekly)")
    p_out.add_argument("ledger_id")
    p_out.add_argument("--score", type=int, default=None, help="upvotes/likes")
    p_out.add_argument("--op-replied", dest="op_replied", default="", help="yes|no|unknown")
    p_out.add_argument("--led-to-profile", dest="led_to_profile", default="", help="yes|no|unknown")
    p_out.add_argument("--note", default="")
    p_out.set_defaults(func=cmd_outcome)

    p_recent = sub.add_parser("recent", help="list recent sent replies")
    p_recent.add_argument("--platform", default="")
    p_recent.add_argument("--limit", type=int, default=20)
    p_recent.set_defaults(func=cmd_recent)

    args = parser.parse_args(argv)
    if not args.cmd:  # bare `reply_review` -> drain the review queue
        args.candidate_id, args.platform, args.limit = None, "reddit", 20
        return cmd_review(args)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
