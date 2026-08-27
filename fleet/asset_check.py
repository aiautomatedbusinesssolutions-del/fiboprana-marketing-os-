"""Smoke-test the Marketing OS spine tables before wiring anything to them.

    python -m fleet.asset_check          # read-only: 4 tables + 3 views reachable?
    python -m fleet.asset_check --write  # full round-trip: video -> decision ->
                                          # publish -> checks -> outcome -> experiment
                                          # guards -> write-once moat assertions

Run this right after applying BOTH migrations (20260703000001 + 20260703000002).
If the read-only pass is green, the migrations applied and the schema is exposed.
The --write pass also asserts the learning contract's hard layer: the experiment
uniqueness indexes, the refuse-if-published funnel rule, and the column-scoped
write-once grants (including the reply_ledger.predicted_grade revoke).

The --write rows are tagged slug/hypothesis 'smoke-…' / 'SMOKE …' and every
outcome check they open is closed or skipped, so nothing lingers in the queue.
Rows can't be deleted with the anon key (no DELETE grant); clean up in Studio's
SQL editor (service role) when done:
    delete from marketing.outcome_checks where entity_id in
        (select id from marketing.videos where slug like 'smoke-%');
    delete from marketing.decisions where subject_id in
        (select id from marketing.videos where slug like 'smoke-%');
    delete from marketing.experiments where hypothesis like 'SMOKE%';
    delete from marketing.videos where slug like 'smoke-%';
"""

import argparse
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fleet import asset_store, supabase  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402

READ_CHECKS = [
    ("videos",                       lambda: supabase.select("videos", params={"select": "id", "limit": 1})),
    ("decisions",                    lambda: supabase.select("decisions", params={"select": "id", "limit": 1})),
    ("experiments",                  lambda: supabase.select("experiments", params={"select": "id", "limit": 1})),
    ("outcome_checks",               lambda: supabase.select("outcome_checks", params={"select": "id", "limit": 1})),
    ("decision_calibration (view)",  lambda: supabase.select("decision_calibration", params={"limit": 1})),
    ("agent_heartbeat (view)",       lambda: supabase.select("agent_heartbeat", params={"limit": 1})),
    ("video_lineage (view)",         lambda: supabase.select("video_lineage", params={"limit": 1})),
    ("content_calendar new columns", lambda: supabase.select("content_calendar", params={"select": "id,video_id,post_type,draft_text", "limit": 1})),
    ("asset_store.videos_by_status", lambda: asset_store.videos_by_status()),
    ("asset_store.due_checks()",     lambda: asset_store.due_checks()),
    ("asset_store.open_decisions()", lambda: asset_store.open_decisions()),
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


def _expect_value_error(label, fn):
    """The store funnel's soft rules: these must raise ValueError locally."""
    try:
        fn()
        print(f"  WARNING — {label} was ACCEPTED (funnel rule not enforced).")
        return False
    except ValueError:
        print(f"  OK — {label} rejected by the store funnel.")
        return True


def _expect_db_reject(label, fn):
    """The hard layer: these must be rejected BY THE DATABASE (grant/index)."""
    try:
        fn()
        print(f"  WARNING — {label} SUCCEEDED (the DB is not enforcing this).")
        return False
    except SupabaseError:
        print(f"  OK — {label} rejected by the database.")
        return True


def _write_roundtrip():
    ok = True
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    slug = f"smoke-{stamp}"

    print("\nWrite round-trip (video -> decision -> publish -> checks -> outcome):")
    video = asset_store.create_video(slug=slug, pillar="news",
                                     reasoning="smoke test — safe to delete", legacy=True)
    print(f"  created video id={video['id']} slug={slug}")

    # Idempotency: same slug returns the existing row, inserts nothing.
    again = asset_store.create_video(slug=slug, pillar="news", legacy=True)
    if again.get("id") == video["id"]:
        print("  OK — create_video is idempotent on slug.")
    else:
        ok = False
        print("  WARNING — duplicate slug produced a second row!")

    decision = asset_store.log_decision(
        lever="thumbnail_composition", surface="youtube",
        subject_type="video", subject_id=video["id"],
        chosen="smoke option A",
        alternatives=[{"option": "smoke option B", "why_rejected": "smoke"}],
        reasoning="smoke test — safe to delete")
    print(f"  logged routine decision id={decision['id']}")

    print("\nStore-funnel contract (expect rejections):")
    ok &= _expect_value_error("unknown lever", lambda: asset_store.log_decision(
        lever="font_kerning", surface="youtube", subject_type="video",
        subject_id=video["id"], chosen="x", alternatives=[{"option": "y"}], reasoning="x"))
    ok &= _expect_value_error("missing alternatives", lambda: asset_store.log_decision(
        lever="title_wording", surface="youtube", subject_type="video",
        subject_id=video["id"], chosen="x", alternatives=[], reasoning="x"))
    ok &= _expect_value_error("experiment without hypothesis/prediction/experiment_id",
        lambda: asset_store.log_decision(
            lever="title_wording", surface="youtube", subject_type="video",
            subject_id=video["id"], chosen="x", alternatives=[{"option": "y"}],
            reasoning="x", mode="experiment"))
    ok &= _expect_value_error("hypothesis on a routine row (hypothesis theater)",
        lambda: asset_store.log_decision(
            lever="title_wording", surface="youtube", subject_type="video",
            subject_id=video["id"], chosen="x", alternatives=[{"option": "y"}],
            reasoning="x", hypothesis="sneaky"))

    print("\nPublish -> outcome clock:")
    result = asset_store.mark_published(video["id"], youtube_video_id=f"smoke-{stamp}")
    print(f"  published; checks opened: {result['checks_opened']} (expect 3)")
    ok &= result["checks_opened"] == 3

    ok &= _expect_value_error("decision on an already-published subject",
        lambda: asset_store.log_decision(
            lever="title_wording", surface="youtube", subject_type="video",
            subject_id=video["id"], chosen="x", alternatives=[{"option": "y"}],
            reasoning="hindsight"))

    checks = supabase.select("outcome_checks", params={
        "entity_id": f"eq.{video['id']}", "order": "due_at.asc"})
    asset_store.close_check(checks[0]["id"], result={"views": 0, "note": "smoke"},
                            source="manual")
    for c in checks[1:]:
        asset_store.skip_check(c["id"], reason="smoke")
    print("  closed 24h check, skipped 7d/28d — nothing lingers in the queue.")

    asset_store.update_decision_outcome(decision["id"], basis="none",
                                        verdict="off", score=0, note="smoke")
    print("  decision outcome closed with basis='none' (calibration-excluded).")

    print("\nExperiment guards (DB-enforced discipline):")
    active = asset_store.active_experiments()
    non_tc_active = [e for e in active if e.get("method") != "yt_test_compare"]
    if non_tc_active:
        # A REAL experiment holds the slot — verify the guard without disturbing it.
        ok &= _expect_db_reject("second active experiment (real one holds the slot)",
            lambda: asset_store.open_experiment(
                lever="upload_time", surface="youtube", method="sequential",
                hypothesis="SMOKE — should be rejected"))
    else:
        exp = asset_store.open_experiment(
            lever="upload_time", surface="youtube", method="sequential",
            hypothesis="SMOKE — safe to delete", min_n=1)
        print(f"  opened smoke experiment id={exp['id']}")
        ok &= _expect_db_reject("second concurrent active experiment",
            lambda: asset_store.open_experiment(
                lever="hook", surface="x", method="ab_manual",
                hypothesis="SMOKE — should be rejected"))
        asset_store.conclude_experiment(exp["id"], verdict="inconclusive",
                                        note="smoke", abandoned=True)
        print("  smoke experiment abandoned — active slot freed.")

    print("\nWrite-once moat (expect DB rejections):")
    ok &= _expect_db_reject("overwrite decisions.reasoning",
        lambda: supabase.update("decisions", {"id": decision["id"]}, {"reasoning": "TAMPERED"}))
    ok &= _expect_db_reject("overwrite videos.slug",
        lambda: supabase.update("videos", {"id": video["id"]}, {"slug": "tampered"}))
    ok &= _expect_db_reject("move outcome_checks.due_at (the clock)",
        lambda: supabase.update("outcome_checks", {"id": checks[0]["id"]},
                                {"due_at": "2030-01-01T00:00:00Z"}))

    ledger = supabase.select("reply_ledger", params={"select": "id,predicted_grade", "limit": 1})
    if ledger:
        # Patch to its CURRENT value: harmless if the revoke is missing, but still
        # proves whether the grant exists.
        row = ledger[0]
        ok &= _expect_db_reject("update reply_ledger.predicted_grade (20260703000001 revoke)",
            lambda: supabase.update("reply_ledger", {"id": row["id"]},
                                    {"predicted_grade": row.get("predicted_grade")}))
    else:
        print("  (reply_ledger empty — predicted_grade revoke not verifiable, skipped)")

    # Park the smoke video out of the in-flight pipeline view.
    asset_store.update_video(video["id"], status="closed", notes="smoke test row")
    print("\n  smoke video closed. Round-trip done.")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Marketing OS spine Supabase check.")
    parser.add_argument("--write", action="store_true",
                        help="also run the write round-trip (+ guard/moat assertions)")
    args = parser.parse_args()

    try:
        ok = _read_only()
        if args.write:
            ok = _write_roundtrip() and ok
        if ok:
            print("\nMarketing OS spine healthy.")
            return 0
        print("\nSome checks failed — see above.")
        return 1
    except SupabaseError as e:
        print(f"\nFAILED: {e}\n")
        print("Checklist:")
        print("  1. Applied supabase/migrations/20260703000001_video_system.sql in Studio?")
        print("  2. Applied supabase/migrations/20260703000002_content_calendar_posts.sql after it?")
        print("  3. SUPABASE_URL + SUPABASE_ANON_KEY set in .env?")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
