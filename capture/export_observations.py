"""Export the observations table to markdown and CSV snapshots.

Writes two files into capture/exports/ (or a custom directory):
    observations_YYYY-MM-DD.md
    observations_YYYY-MM-DD.csv

Re-running on the same day overwrites that day's files.

Examples:
    python export_observations.py
    python export_observations.py --out-dir ../review-packets
"""

import argparse
import csv
from datetime import date
from pathlib import Path

from db import MODULE_DIR, ensure_schema, get_connection


DEFAULT_OUT_DIR = MODULE_DIR / "exports"

COLUMN_ORDER = [
    "id", "date_observed", "source", "source_detail", "source_url",
    "raw_content", "segment_guess", "pain_points", "notable_quotes",
    "feature_implications", "content_hooks", "tags", "created_at",
]

MD_SKIP_IN_BODY = {"id", "date_observed", "tags"}


def write_markdown(path, rows):
    with path.open("w", encoding="utf-8") as f:
        f.write(f"# Observations export — {date.today().isoformat()}\n\n")
        f.write(f"{len(rows)} entries.\n\n")
        for r in rows:
            header_bits = [f"Entry #{r['id']}", r['date_observed']]
            if r['source']:
                header_bits.append(r['source'])
            f.write(f"## {' — '.join(header_bits)}\n\n")

            for col in COLUMN_ORDER:
                if col in MD_SKIP_IN_BODY:
                    continue
                val = r[col]
                if not val:
                    continue
                if "\n" in str(val):
                    f.write(f"**{col}:**\n\n")
                    for line in str(val).splitlines():
                        f.write(f"> {line}\n")
                    f.write("\n")
                else:
                    f.write(f"**{col}:** {val}\n\n")

            if r['tags']:
                f.write(f"_tags: {r['tags']}_\n\n")
            f.write("---\n\n")


def write_csv(path, rows):
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(COLUMN_ORDER)
        for r in rows:
            writer.writerow([r[c] if r[c] is not None else "" for c in COLUMN_ORDER])


def parse_args():
    p = argparse.ArgumentParser(description="Export observations to .md + .csv.")
    p.add_argument("--out-dir", help="destination directory (default: capture/exports/)")
    return p.parse_args()


def main():
    args = parse_args()
    conn = get_connection()
    ensure_schema(conn)

    rows = conn.execute(
        "SELECT * FROM observations ORDER BY date_observed ASC, id ASC"
    ).fetchall()

    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    md_path = out_dir / f"observations_{today}.md"
    csv_path = out_dir / f"observations_{today}.csv"

    write_markdown(md_path, rows)
    write_csv(csv_path, rows)

    print(f"Wrote {md_path}")
    print(f"Wrote {csv_path}")
    print(f"({len(rows)} entr{'y' if len(rows) == 1 else 'ies'})")


if __name__ == "__main__":
    main()
