"""Cron / CLI entrypoint for the weekly research run — the AUTONOMOUS path.

Same brain as the MCP server, no chat. Schedule this WEEKLY on Railway as a CRON
JOB (not a web service — stdio MCP is the interactive door; this is the autonomous
one). It writes the run to Supabase and prints the digest for the logs.

    python -m fleet.research_run                  # scan + digest + pull + persist
    python -m fleet.research_run --no-scan         # reuse the existing radar pile
    python -m fleet.research_run --check-heartbeat  # dead-man's switch: is the
                                                     # weekly cron alive? exit 1 if stale

Dead-man's switch: every real run records a heartbeat in the Supabase ops log.
Point an external monitor (an uptime cron, healthchecks.io, or a future monitoring
agent) at `--check-heartbeat` so a silently-failing or never-firing weekly cron
gets noticed instead of quietly going dark.
"""

import argparse
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # local dev; in the cloud the platform injects env vars

# store is light (stdlib + urllib), so importing it up top lets --check-heartbeat
# run WITHOUT pulling in the whole agent/radar/anthropic stack.
from fleet import store  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402

# A healthy WEEKLY run should land at least every 7 days; 8 = one day of slack so
# a slightly-late cron doesn't trip the alert.
HEARTBEAT_MAX_AGE_DAYS = 8


def _check_heartbeat():
    """Dead-man's switch: report whether the autonomous cron is still alive.
    Returns a process exit code (0 = fresh, 1 = stale / none / unreachable)."""
    try:
        s = store.heartbeat_status(agent_name="research",
                                   max_age_days=HEARTBEAT_MAX_AGE_DAYS)
    except SupabaseError as e:
        print(f"heartbeat: UNKNOWN - could not reach the ledger: {e}")
        return 1
    if s["last_success"] is None:
        print("heartbeat: STALE - no successful run on record yet.")
        return 1
    state = "STALE" if s["stale"] else "OK"
    print(f"heartbeat: {state} - last success {s['last_success']} "
          f"({s['age_days']} days ago; threshold {s['max_age_days']}).")
    return 1 if s["stale"] else 0


def main():
    parser = argparse.ArgumentParser(description="Weekly research agent run (autonomous).")
    parser.add_argument("--no-scan", action="store_true",
                        help="reuse the existing radar pile instead of scanning")
    parser.add_argument("--check-heartbeat", action="store_true",
                        help="don't run; report whether the weekly cron is alive (exit 1 if stale)")
    args = parser.parse_args()

    if args.check_heartbeat:
        return _check_heartbeat()

    # Heavy import (pulls in app / radar) — defer past the cheap check above.
    from fleet import llm, research_agent

    # Every model call this run makes goes through fleet/llm.py, which meters
    # itself. The roll-up rides the heartbeat into agent_execution_logs -> the
    # /week Fleet view.
    llm.reset_usage()
    started = time.monotonic()

    result = research_agent.run_research(scan=not args.no_scan)
    print(research_agent.format_for_chat(result))

    # A clean run = produced a digest AND hit no pull/ledger error.
    ok = bool(result.get("ok") and not result.get("error"))

    # Heartbeat: the dead-man's switch's "I'm alive" signal (status 'success' /
    # 'failure'). A heartbeat write failure must NEVER change the run's own exit
    # code, so swallow its errors.
    try:
        usage = llm.usage_totals()
        store.record_heartbeat(
            agent_name="research",
            status="success" if ok else "failure",
            message=result.get("error"),
            correlation_id=result.get("run_id"),
            duration_ms=int((time.monotonic() - started) * 1000),
            **usage,
        )
    except Exception as e:  # noqa: BLE001
        print(f"(warning: heartbeat write failed: {e})")

    # Non-zero on any failure OR a partial run (pull/ledger error) so the cron's
    # exit-code alerting catches a silently-degraded run, not just a hard crash.
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
