"""Run the weekly research in one command: scan all sources, then distill the
brand-voiced trend digest, then print it.

    python -m radar.run             # scan (HN + RSS + Exa) then generate the digest
    python -m radar.run --no-scan   # skip the scan, just (re)generate from the pile
    python -m radar.run --scan-only # just gather; don't spend a model call yet

This is the same scan + digest the /radar dashboard buttons run — wrapped as a CLI
so the weekly research is one repeatable step. The digest is written to the radar
DB (radar_digests) exactly as the dashboard would, and is viewable at /radar/digest.
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Import app first so the app<->blueprint circular import resolves in the right
# order (app defines RADAR_DIGEST_MODEL + friends before importing
# radar.routes). We're __main__, so app.run() under its guard never fires.
import app  # noqa: F401,E402
from radar import db as radar_db  # noqa: E402
from radar import config as radar_config  # noqa: E402
from radar import scanner as radar_scanner  # noqa: E402
from radar.exa_client import get_exa_client, RadarConfigError  # noqa: E402
from radar.routes import _generate_trend_digest  # noqa: E402


def _scan(conn):
    config = radar_config.load_config()
    try:
        exa = get_exa_client()
    except RadarConfigError as e:
        exa = None
        print(f"  (Exa disabled: {e})")
    summary = radar_scanner.scan_all(conn, config, exa_client=exa)
    print(
        f"  HN {summary['hn_fetched']} | RSS {summary['rss_fetched']} | "
        f"Exa {summary['exa_fetched']}  ->  {summary['inserted']} new, "
        f"{summary['duplicates']} dupes, {summary['filtered']} filtered"
    )
    for err in summary["errors"]:
        print(f"  ! {err[:160]}")


def main():
    parser = argparse.ArgumentParser(description="Weekly research: scan + trend digest.")
    parser.add_argument("--no-scan", action="store_true", help="skip the scan step")
    parser.add_argument("--scan-only", action="store_true", help="scan but don't digest")
    args = parser.parse_args()

    conn = radar_db.get_connection()
    radar_db.ensure_schema(conn)

    if not args.no_scan:
        print("Scanning HN + RSS + Exa ...")
        _scan(conn)

    if args.scan_only:
        return 0

    print("\nGenerating trend digest ...")
    ok, message = _generate_trend_digest(conn)
    print()
    if ok:
        print("=== TREND DIGEST ===\n")
        print(message)
    else:
        print(f"(no digest: {message})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
