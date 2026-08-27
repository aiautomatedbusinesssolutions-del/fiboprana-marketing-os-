"""Smoke-test the Supabase marketing-schema connection before wiring any agents.

    python -m fleet.check          # read-only: list recent research_runs
    python -m fleet.check --write  # also insert + update a throwaway row

Run this right after applying the migration and exposing the `marketing` schema.
If it works here, the MCP server will work — same store, same creds. The --write
row is tagged agent_name='smoke-test' (so it never pollutes the research learning
read); delete it in Studio when you're done.
"""

import argparse
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fleet import store  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Supabase fleet connectivity check.")
    parser.add_argument("--write", action="store_true", help="also do a write round-trip")
    args = parser.parse_args()

    try:
        print("Reading recent research_runs ...")
        rows = store.recent_runs_for_learning(limit=5)
        print(f"  OK — read succeeded; {len(rows)} recent run(s) visible.")
        for r in rows:
            print(f"   - {r.get('created_at')}  verdict={r.get('outcome_verdict') or '-'}")

        if args.write:
            print("Writing a throwaway run ...")
            run = store.log_research_run(
                agent_name="smoke-test",
                digest_md="(smoke test — safe to delete)",
                content_pull={"headline": "connectivity check"},
                reasoning="fleet.check --write round-trip",
                inputs={"smoke": True},
                model="n/a",
            )
            print(f"  inserted id={run['id']}")
            store.update_outcome(run["id"], verdict="off", note="smoke-test row")
            print("  outcome updated. Round-trip OK. (Delete this row in Studio.)")

        print("\nConnection healthy.")
        return 0
    except SupabaseError as e:
        print(f"\nFAILED: {e}\n")
        print("Checklist:")
        print("  1. Migration applied in Supabase Studio?")
        print("  2. `marketing` added under Settings -> API -> Exposed schemas?")
        print("  3. SUPABASE_URL + SUPABASE_ANON_KEY set in .env (or the environment)?")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
