"""Smoke-test the reply system's Supabase tables before wiring the agents.

    python -m fleet.reply_check          # read-only: are the 3 tables + 2 views reachable?
    python -m fleet.reply_check --write  # also a full insert->judge->draft->send round-trip
                                          # (+ assert the moat columns are write-once)

Run this right after applying supabase/migrations/20260626000001_reply_system.sql. If
the read-only pass is green, the migration applied and the schema is exposed, so the
agents and the review CLI will reach the same tables (same store, same creds).

The --write rows are tagged community='r/SMOKE-TEST' so they're easy to find. They
can't be deleted with the anon key (no DELETE grant), so clean up in Studio's SQL
editor (service role) when done:
    delete from marketing.reply_ledger    where community = 'r/SMOKE-TEST';
    delete from marketing.reply_drafts     where candidate_id in
        (select id from marketing.reply_candidates where community = 'r/SMOKE-TEST');
    delete from marketing.reply_candidates where community = 'r/SMOKE-TEST';
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fleet import reply_store, supabase  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402

# Each check exercises a table/view (and, for the last two, the data layer's reads).
READ_CHECKS = [
    ("reply_candidates",            lambda: supabase.select("reply_candidates", params={"select": "id", "limit": 1})),
    ("reply_drafts",                lambda: supabase.select("reply_drafts", params={"select": "id", "limit": 1})),
    ("reply_ledger",                lambda: supabase.select("reply_ledger", params={"select": "id", "limit": 1})),
    ("reply_weekly_metrics (view)", lambda: supabase.select("reply_weekly_metrics", params={"limit": 1})),
    ("reply_calibration (view)",    lambda: supabase.select("reply_calibration", params={"limit": 1})),
    ("reply_store.review_queue()",  lambda: reply_store.review_queue()),
    ("reply_store.recent_sent()",   lambda: reply_store.recent_sent()),
]


def _read_only():
    print("Reachability + data-layer reads:")
    ok = True
    for label, fn in READ_CHECKS:
        try:
            rows = fn()
            print(f"  OK   {label}  ({len(rows)} row(s) visible)")
        except SupabaseError as e:
            ok = False
            print(f"  FAIL {label}\n        {e}")
    return ok


def _write_roundtrip():
    print("\nWrite round-trip (candidate -> judge -> draft -> send -> outcome):")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    ext = f"smoke-{stamp}"

    new = reply_store.log_candidates([{
        "external_id": ext,
        "post_url": f"https://www.reddit.com/r/SMOKE/comments/{ext}/",
        "community": "r/SMOKE-TEST",
        "post_title": "smoke test — safe to delete",
        "post_excerpt": "connectivity round-trip",
        "post_author": "smoke",
        "finder_score": 1, "flips": "is it working",
        "soft_triggers": "new to meditation", "angle": "smoke",
        "flip_group": "practice_doubt",
    }])
    if not new:
        print("  FAIL — log_candidates returned nothing (duplicate external_id?).")
        return False
    cand = new[0]
    print(f"  inserted candidate id={cand['id']}")

    reply_store.update_judge(cand["id"], verdict="engage", score=99, on_theme=True,
                             audience_fit="core", rationale="smoke", model="n/a")
    drafts = reply_store.log_drafts(
        cand["id"], platform="reddit", style_version="smoke",
        variants=[{"variant_index": 0, "angle": "smoke",
                   "draft_text": "Here is the AI's draft reply.", "gate_status": "pass"}])
    print(f"  inserted draft id={drafts[0]['id']}")

    ledger = reply_store.log_sent_reply(
        candidate_id=cand["id"], draft_id=drafts[0]["id"],
        final_sent="Here is the AI's draft reply, but in my own words.",
        edit_type="voice", intent_preserved=True, style_notes="smoke", predicted_grade="off")
    print(f"  logged sent reply id={ledger['id']}  "
          f"edit_ratio={ledger.get('edit_ratio')}  delta_captured={bool(ledger.get('edit_delta'))}")

    reply_store.update_outcome(ledger["id"], outcome_score=0, op_replied="no")
    print("  outcome updated. Round-trip OK.")

    print("\nWrite-once moat check (expect REJECTED):")
    try:
        supabase.update("reply_ledger", {"id": ledger["id"]}, {"final_sent": "TAMPERED"})
        print("  WARNING — final_sent was overwritable! The column-scoped grant is NOT enforced.")
        return False
    except SupabaseError:
        print("  OK — overwrite of final_sent rejected; the moat is write-once even under anon.")
    return True


def main():
    parser = argparse.ArgumentParser(description="Reply-system Supabase connectivity check.")
    parser.add_argument("--write", action="store_true", help="also do a write round-trip (+ moat check)")
    args = parser.parse_args()

    try:
        ok = _read_only()
        if args.write:
            ok = _write_roundtrip() and ok
        if ok:
            print("\nReply tables healthy.")
            return 0
        print("\nSome checks failed — see above.")
        return 1
    except SupabaseError as e:
        print(f"\nFAILED: {e}\n")
        print("Checklist:")
        print("  1. Applied supabase/migrations/20260626000001_reply_system.sql in Studio?")
        print("  2. `marketing` exposed under Settings -> API -> Exposed schemas (already on if research works)?")
        print("  3. SUPABASE_URL + SUPABASE_ANON_KEY set in .env?")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
