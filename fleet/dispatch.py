"""The daily dispatcher — the Marketing OS's ONE cron (MARKETING_OS.md §5).

No orchestrator agent (locked): coordination is this readable schedule + the
shared Supabase state + heartbeats. Railway runs it daily; it decides which jobs
are due, runs EACH ONE AS A SUBPROCESS with a hard wall-clock timeout, records
heartbeats, pings healthchecks.io, and ALWAYS exits — Railway blocks the next
cron run while the container is still running (not when it exits nonzero), so
the real discipline is per-job timeouts + clean exit; the external ping catches
the hang case a timeout can't.

    python -m fleet.dispatch                # the cron entrypoint: run all due jobs
    python -m fleet.dispatch --job reply    # run one job inline (what the
    python -m fleet.dispatch --job checks   #  subprocesses actually execute)
    python -m fleet.dispatch --list         # show the schedule + what's due now

Jobs (hardcoded, readable — the schedule IS the manager):
  * reply    Mon-Fri — finder -> judge -> drafter. NEVER sends: replies wait at
                       the founder gate (review CLI) and go out by hand.
  * checks   daily   — open outcome_checks for anything newly published without
                       them (idempotent sweep; published_at arms the clock).
  * metrics  daily   — snapshot the raw numbers (every X post/reply via
                       Typefully, reddit karma+comment scores when creds are
                       live) into metric_snapshots, and close due reply/post
                       outcome_checks windows with that day's fresh read.
                       Video windows still close by hand until the YouTube
                       agent lands.
  * outcome_checker
             Saturday — read real engagement for sent replies (Reddit official
                       API when REDDIT_* creds are set; X free via Typefully
                       analytics) and write it to reply_ledger + close the
                       matching outcome_checks. Runs the day before research
                       so Sunday reads fresh outcomes.
  * distiller
             Saturday — distill the week's draft->final reply edits into
                       PROPOSED style-guide rules + judge notes (a dated file
                       in fleet/distiller_proposals/); founder approves,
                       nothing auto-commits.
  * reply_eval
             Saturday — re-draft the gold-set posts with the current drafter
                       brain, blind pairwise judge vs the founder's sent
                       replies; win-rate per guide version appends to
                       fleet/eval_results.jsonl. The distiller proposes,
                       this measures whether approved rules actually helped.
  * research Sunday  — OFF by default behind DISPATCH_RESEARCH=1. Cutover rule:
                       delete the standalone research cron service and flip this
                       flag IN THE SAME DEPLOY (never both on -> double runs).

Env (beyond the usual SUPABASE_* / ANTHROPIC_API_KEY):
  HEALTHCHECKS_URL   optional healthchecks.io ping URL; success pings it, any
                     failure pings <url>/fail. Wire this BEFORE trusting the cron.
  DISPATCH_RESEARCH  "1" to fold the Sunday research run into this dispatcher.
"""

import argparse
import os
import socket
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # local dev; on Railway the platform injects env vars

# Belt and braces under the per-job wall clocks: no single socket may hang a job.
socket.setdefaulttimeout(30)

CHECK_SWEEP_DAYS = 35  # how far back the checks sweep looks for unarmed publishes


def _due_daily(now):
    return True


def _due_weekday(now):
    # Reply hunting runs Mon-Fri only (founder call 2026-07-23): weekend posts
    # get found Monday if still fresh, and the xAI spend drops ~30%.
    return now.weekday() < 5


def _due_mon_wed_fri(now):
    # Reply listening cadence since the finder-trial call (founder, 2026-08-07):
    # the Reddit scan now rides Apify (~$0.46/run), and Mon/Wed/Fri keeps the
    # month inside Apify's ~$5 free credit. Revisit when REPLY_CHANNELS adds x
    # back — the X hunt wants Mon-Fri (_due_weekday) and may deserve its own job.
    return now.weekday() in (0, 2, 4)


def _due_research(now):
    return now.weekday() == 6 and os.environ.get("DISPATCH_RESEARCH") == "1"


def _due_saturday(now):
    # The day before the Sunday research run, so research reads fresh outcomes.
    return now.weekday() == 5


# name -> (due?, argv run as a subprocess, hard wall-clock seconds)
JOBS = [
    {"name": "reply",    "due": _due_mon_wed_fri, "timeout_s": 900,
     "argv": ["-m", "fleet.dispatch", "--job", "reply"]},
    {"name": "checks",   "due": _due_daily,    "timeout_s": 300,
     "argv": ["-m", "fleet.dispatch", "--job", "checks"]},
    {"name": "metrics",  "due": _due_daily,    "timeout_s": 300,
     "argv": ["-m", "fleet.metrics_agent"]},          # records its own heartbeat
    # First-7-day video outcomes + thumbnail CTR (personal-brand port
    # 2026-08-23). Needs the YouTube OAuth env vars, so it skips honestly
    # in the cloud until they're copied there; the founder's PC runs it.
    {"name": "video_outcomes", "due": _due_daily, "timeout_s": 300,
     "argv": ["-m", "fleet.video_outcomes"]},         # records its own heartbeat
    # Off-PC drain v1 (2026-08-22): acts on the click-state MIRROR - logs a
    # Studio-scheduled video and schedules an approved xpost with the
    # founder's PC off; deltas land in the outbox for the local runner.
    {"name": "drain",    "due": _due_daily,    "timeout_s": 300,
     "argv": ["-m", "fleet.fleet_state"]},            # records its own heartbeat
     # after checks: the sweep arms windows, this closes the ones now due
    {"name": "outcome_checker", "due": _due_saturday, "timeout_s": 300,
     "argv": ["-m", "fleet.outcome_checker_agent"]},  # records its own heartbeat
    {"name": "distiller", "due": _due_saturday, "timeout_s": 300,
     "argv": ["-m", "fleet.distiller_agent"]},        # records its own heartbeat
    {"name": "reply_eval", "due": _due_saturday, "timeout_s": 900,
     "argv": ["-m", "fleet.reply_eval_agent"]},       # records its own heartbeat
    {"name": "research", "due": _due_research, "timeout_s": 1800,
     "argv": ["-m", "fleet.research_run"]},   # records its own heartbeat
]


# ── the jobs themselves (run inside the child subprocess) ────────────────────
def _job_reply():
    """Finder -> judge -> drafter, exactly the pipeline the founder runs by hand.
    Output lands in the Railway log; the drafts land in Supabase and WAIT at the
    review gate. A 0-candidate day is logged in the heartbeat message — a streak
    of zeros in agent_heartbeat is the Reddit-blocked-from-datacenter-IP alarm."""
    from fleet import llm, reply_drafter_agent, reply_finder_agent, reply_store
    from fleet.channels import enabled_adapters

    llm.reset_usage()  # judge + drafter calls meter themselves via fleet/llm.py
    started = time.monotonic()

    # Freshness sweep first: age out queue rows past their platform window so
    # the review queue is only ever posts still worth replying to.
    expired = reply_store.expire_stale_candidates()
    print("expired stale candidates: "
          + (", ".join(f"{p} {n}" for p, n in expired.items() if n) or "none"))

    find = reply_finder_agent.run_find_candidates(top_n=10)
    print(reply_finder_agent.format_for_chat(find))

    # Draft for every enabled channel (REPLY_CHANNELS env), not just reddit —
    # the drafter itself is channel-agnostic.
    drafted, draft_ok, draft_errors = 0, True, []
    for adapter in enabled_adapters():
        draft = reply_drafter_agent.run_draft_reply(platform=adapter.name, limit=3)
        print(reply_drafter_agent.format_for_chat(draft))
        drafted += draft.get("drafted", 0)
        draft_ok = draft_ok and bool(draft.get("ok"))
        if draft.get("error"):
            draft_errors.append(f"{adapter.name}: {draft['error']}")

    ok = bool(find.get("ok")) and draft_ok
    message = (f"judged {find.get('judged', 0)}, engaged {find.get('engaged', 0)}, "
               f"drafted {drafted}")
    errors = "; ".join([e for e in [find.get("error")] if e] + draft_errors)
    _heartbeat("reply", ok, message if not errors else f"{message} | {errors}",
               usage=llm.usage_totals(),
               duration_ms=int((time.monotonic() - started) * 1000))
    return 0 if ok else 1


def _job_checks():
    """Idempotent sweep: open outcome checks for anything published in the last
    CHECK_SWEEP_DAYS that has none yet (the unique index makes re-opening a
    no-op). Videos get 24h/7d/28d, posts and replies get 7d."""
    from datetime import timedelta

    from fleet import asset_store, supabase

    since = (datetime.now(timezone.utc) - timedelta(days=CHECK_SWEEP_DAYS)).isoformat()
    opened = 0
    sweeps = [
        ("videos", "published_at", {"published_at": f"gte.{since}"}),
        ("content_calendar", "published_at", {"published_at": f"gte.{since}"}),
        ("reply_ledger", "replied_at", {"replied_at": f"gte.{since}"}),
    ]
    ok = True
    for table, ts_col, filters in sweeps:
        try:
            rows = supabase.select(table, params={
                "select": f"id,{ts_col}", **filters, "limit": 200})
            for row in rows:
                opened += len(asset_store.open_checks(table, row["id"],
                                                      published_at=row[ts_col]))
        except Exception as e:  # noqa: BLE001 — one bad sweep must not kill the rest
            ok = False
            print(f"checks sweep failed for {table}: {e}")
    # Close the video windows that just came due, daily — a 24h check closed
    # only on the checker's Saturday run would always read days late. The
    # YouTube stats read is free and the closer is idempotent (due rows only).
    from fleet import outcome_checker_agent
    videos = outcome_checker_agent.run_check_videos()
    ok = ok and videos["ok"]
    print(f"video checks: closed {videos['closed']}, skipped {videos['skipped']}"
          + (f" | {videos['error']}" if videos.get("error") else ""))
    due = len(asset_store.due_checks()) if ok else -1
    print(f"checks: opened {opened} new, {due} now due (the banner's nag list)")
    _heartbeat("checks", ok,
               f"opened {opened}, videos closed {videos['closed']}, due {due}")
    return 0 if ok else 1


def _heartbeat(agent_name, ok, message, usage=None, duration_ms=None):
    """A heartbeat write failure must never change a job's own exit code.
    `usage` is an llm_meter.totals() roll-up (tokens/cost land in the ops log)."""
    try:
        from fleet import store
        store.record_heartbeat(agent_name=agent_name,
                               status="success" if ok else "failure",
                               message=message, duration_ms=duration_ms,
                               **(usage or {}))
    except Exception as e:  # noqa: BLE001
        print(f"(warning: {agent_name} heartbeat write failed: {e})")


INLINE_JOBS = {"reply": _job_reply, "checks": _job_checks}


# ── the dispatcher (the cron entrypoint) ─────────────────────────────────────
def _ping_healthchecks(ok):
    """Dead-man's switch: success pings the URL, failure pings <url>/fail. A
    MISSED ping (container hung, cron never fired) is what alerts — which is why
    this must be wired before the dispatcher is trusted. Silently skipped if
    HEALTHCHECKS_URL is unset; ping errors never affect the exit code."""
    url = os.environ.get("HEALTHCHECKS_URL")
    if not url:
        return
    target = url.rstrip("/") + ("" if ok else "/fail")
    try:
        urllib.request.urlopen(target, timeout=10).read()
    except Exception as e:  # noqa: BLE001
        print(f"(warning: healthchecks ping failed: {e})")


def dispatch():
    now = datetime.now(timezone.utc)
    due = [j for j in JOBS if j["due"](now)]
    print(f"dispatch @ {now.isoformat()} — due: {[j['name'] for j in due] or 'nothing'}")

    failures = []
    try:
        for job in due:
            print(f"\n=== {job['name']} (timeout {job['timeout_s']}s) ===")
            try:
                result = subprocess.run([sys.executable, *job["argv"]],
                                        timeout=job["timeout_s"])
                if result.returncode != 0:
                    failures.append(f"{job['name']} exit {result.returncode}")
            except subprocess.TimeoutExpired:
                failures.append(f"{job['name']} TIMEOUT after {job['timeout_s']}s (killed)")
            except Exception as e:  # noqa: BLE001 — one job must never block the rest
                failures.append(f"{job['name']} launch error: {e}")
    finally:
        ok = not failures
        summary = "all jobs clean" if ok else "; ".join(failures)
        print(f"\ndispatch done — {summary}")
        _heartbeat("dispatch", ok, summary)
        _ping_healthchecks(ok)
    return 0 if ok else 1


def main():
    parser = argparse.ArgumentParser(description="Marketing OS daily dispatcher.")
    parser.add_argument("--job", choices=sorted(INLINE_JOBS),
                        help="run one job inline (the subprocess entrypoint)")
    parser.add_argument("--list", action="store_true",
                        help="show the schedule and what's due right now")
    args = parser.parse_args()

    if args.list:
        now = datetime.now(timezone.utc)
        for j in JOBS:
            print(f"  {j['name']:<10} due_now={j['due'](now)}  timeout={j['timeout_s']}s")
        return 0
    if args.job:
        return INLINE_JOBS[args.job]()
    return dispatch()


if __name__ == "__main__":
    raise SystemExit(main())
