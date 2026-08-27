"""One-time, idempotent backfill: pre-cutover SQLite reply_log rows -> Supabase
reply_ledger.

Before the finder/judge/drafter existed, sent replies were logged to the local
SQLite ledger (reply_finder/reply_log.db). This lifts those rows into the shared
reply_ledger so the whole training set lives in one place. A legacy row has no
candidate, no draft, and no AI draft to diff against, so it lands with the
hand-written reply as final_sent, ai_draft/edit_* null, and edit_type='legacy' —
which keeps it OUT of the voice metrics (the view excludes 'legacy') while still
counting as a 'what landed' + calibration example.

Idempotent: legacy_sqlite_id carries the source row id under a unique index, so this
upserts ignore-duplicates on it — re-run it as often as you like; it only ever
inserts rows it hasn't seen. Live-flow ledger rows (legacy_sqlite_id null) are never
touched.

TODAY THIS IS A NO-OP: reply_finder/reply_log.db has zero rows (nothing was logged
to SQLite before cutover). The script ships now so the path is proven and ready the
moment any legacy row exists.

    python scripts/backfill_reply_log_to_supabase.py            # import
    python scripts/backfill_reply_log_to_supabase.py --dry-run  # report only, no writes
    python scripts/backfill_reply_log_to_supabase.py --db path/to/reply_log.db
"""

import argparse
import sqlite3
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from fleet import reply_store  # noqa: E402
from reply_finder import db as legacy_db  # noqa: E402


def _read_legacy_rows(db_path):
    """Return reply_log rows as plain dicts, oldest first. ([] if the DB file or table
    isn't there — both are clean no-ops, not errors.) Opens read-only so a missing or
    empty database is never created or mutated."""
    if not db_path.exists():
        return None  # signals "no legacy DB at all" so the caller can say so
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.execute("SELECT * FROM reply_log ORDER BY id")
        return [dict(r) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        return []  # DB exists but reply_log table was never created
    finally:
        conn.close()


def _importable(rows):
    """Rows that can become a ledger row: a non-empty reply_text and an id."""
    return [r for r in rows
            if (r.get("reply_text") or "").strip() and r.get("id") is not None]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Backfill legacy SQLite reply_log rows into Supabase reply_ledger.")
    parser.add_argument("--db", type=Path, default=legacy_db.DB_PATH,
                        help=f"legacy SQLite path (default {legacy_db.DB_PATH})")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would import; write nothing")
    args = parser.parse_args(argv)

    rows = _read_legacy_rows(args.db)
    if rows is None:
        print(f"No legacy DB at {args.db} — nothing to backfill. (Expected today.)")
        return 0
    if not rows:
        print(f"{args.db} has no reply_log rows — nothing to backfill.")
        return 0

    candidates = _importable(rows)
    skipped = len(rows) - len(candidates)

    existing = reply_store.existing_legacy_ids()  # one Supabase read
    new = [r for r in candidates if r["id"] not in existing]
    already = len(candidates) - len(new)

    print(f"Legacy rows: {len(rows)} found · {skipped} unimportable (no reply_text) · "
          f"{already} already in the ledger · {len(new)} new")

    if args.dry_run:
        for r in new[:10]:
            text = (r.get("reply_text") or "").replace("\n", " ")
            print(f"  would import #{r['id']} [{r.get('platform')}] "
                  f"{r.get('community') or '?'} — {text[:70]}")
        if len(new) > 10:
            print(f"  ... and {len(new) - 10} more")
        print("(dry run — nothing written.)")
        return 0

    if not new:
        print("Up to date — nothing to import.")
        return 0

    inserted = reply_store.import_legacy_replies(rows)  # ignore-duplicates does the rest
    print(f"Imported {len(inserted)} legacy repl{'y' if len(inserted) == 1 else 'ies'} "
          "into reply_ledger (edit_type='legacy', excluded from voice metrics).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
