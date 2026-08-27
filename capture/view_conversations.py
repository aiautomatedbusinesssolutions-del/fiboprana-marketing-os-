"""Browse captured conversations with optional filters.

Examples:
    python view_conversations.py
    python view_conversations.py --tag broker-question
    python view_conversations.py --source reddit --segment beginner
    python view_conversations.py --from 2026-04-01 --to 2026-04-30
    python view_conversations.py --id 7
    python view_conversations.py --full --limit 50
"""

import argparse

from db import ensure_schema, get_connection


FREE_TEXT_FIELDS = {
    "stated_goal", "pain_points", "quotes", "feature_implications",
    "feedback_on_app", "notes",
}
TRUNCATE_AT = 200


def build_query(args):
    where = []
    params = {}

    if args.tag:
        where.append("tags LIKE :tag")
        params["tag"] = f"%{args.tag.lower()}%"
    if args.source:
        where.append("source = :source")
        params["source"] = args.source
    if args.segment:
        where.append("segment = :segment")
        params["segment"] = args.segment
    if args.date_from:
        where.append("date >= :date_from")
        params["date_from"] = args.date_from
    if args.date_to:
        where.append("date <= :date_to")
        params["date_to"] = args.date_to
    if args.id is not None:
        where.append("id = :id")
        params["id"] = args.id

    sql = "SELECT * FROM conversations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date DESC, id DESC"
    if args.id is None:
        sql += f" LIMIT {int(args.limit)}"
    return sql, params


def print_row(row, show_all):
    print(f"id: {row['id']}")
    print(f"date: {row['date']}")
    for col in row.keys():
        if col in ("id", "date"):
            continue
        if col == "created_at" and not show_all:
            continue
        val = row[col]
        if val is None or val == "":
            continue
        display = str(val)
        if (not show_all) and col in FREE_TEXT_FIELDS and len(display) > TRUNCATE_AT:
            display = display[:TRUNCATE_AT] + "…"
        if "\n" in display:
            print(f"{col}:")
            for line in display.splitlines():
                print(f"    {line}")
        else:
            print(f"{col}: {display}")
    print("---")


def parse_args():
    p = argparse.ArgumentParser(description="Browse captured conversations.")
    p.add_argument("--tag", help="substring match on tags column")
    p.add_argument("--source", help="exact match on source")
    p.add_argument("--segment", help="exact match on segment")
    p.add_argument("--from", dest="date_from", help="YYYY-MM-DD, inclusive")
    p.add_argument("--to", dest="date_to", help="YYYY-MM-DD, inclusive")
    p.add_argument("--limit", type=int, default=20, help="max rows (default 20)")
    p.add_argument("--id", type=int, help="show a single entry by id")
    p.add_argument("--full", action="store_true", help="do not truncate long fields")
    return p.parse_args()


def main():
    args = parse_args()
    conn = get_connection()
    ensure_schema(conn)

    sql, params = build_query(args)
    rows = conn.execute(sql, params).fetchall()

    if not rows:
        print("(no matching entries)")
        return

    show_all = args.full or args.id is not None
    for row in rows:
        print_row(row, show_all)

    print(f"{len(rows)} entr{'y' if len(rows) == 1 else 'ies'} shown.")


if __name__ == "__main__":
    main()
