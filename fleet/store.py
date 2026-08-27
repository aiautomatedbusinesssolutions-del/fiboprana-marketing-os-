"""The one interface every fleet agent — and me, working in chat — uses to read
and write the shared marketing ledger in Supabase.

Same spirit as reply_finder/store.py (decision + reasoning + outcome, one API for
both drivers), but it writes to Supabase's `marketing` schema instead of local
SQLite, because the agents run in the cloud and share one central ledger. The
transport lives in fleet/supabase.py; this module is the domain vocabulary.

Two tables, two jobs:
  * research_runs        -> the LEARNING ledger (what an agent decided + why + how it did)
  * agent_execution_logs -> the OPS log (what ran, when, cost, errors) for every agent

Typical research-run flow:

    from fleet import store

    run = store.log_research_run(
        digest_md=digest_text,
        content_pull={"headline": "...", "video_angle": "...",
                      "reddit_themes": ["..."], "feature_seed": "..."},
        reasoning="why these five made the cut; the education-vs-advice call",
        inputs={"hn": 40, "rss": 60, "exa": 10, "prior_verdicts_considered": 3},
        model="claude-sonnet-4-6",
        week_start="2026-06-22",
    )
    # ...after you've seen it (or downstream content has landed):
    store.update_outcome(run["id"], verdict="strong",
                         note="Coinbase SEC-agent item = dead in my lane, hadn't seen it")

The next run reads recent_runs_for_learning() first, so the agent gets better over time.
"""

from datetime import datetime, timezone

from fleet import supabase

RESEARCH_RUNS = "research_runs"
EXECUTION_LOGS = "agent_execution_logs"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _compact(d):
    """Drop None values so DB column defaults apply to anything omitted."""
    return {k: v for k, v in d.items() if v is not None}


def log_research_run(*, digest_md=None, content_pull=None, reasoning=None,
                     inputs=None, model=None, week_start=None,
                     agent_name="research", status="complete",
                     run_started_at=None, run_finished_at=None):
    """Insert one research_runs row (a self-contained training example) and return
    it, including the DB-generated id + timestamps.

    content_pull is the weekly pull object — the five WORKFLOW fields (headline,
    video_angle, competitor_note, reddit_themes[], feature_seed). The run id is the
    provenance anchor: content made from a seed stores this run's id (+ which seed).
    inputs / outcome_metrics are free-form jsonb — denormalize the run's context
    onto the row so it stands alone without joins.
    """
    row = _compact({
        "agent_name": agent_name,
        "model": model,
        "week_start": week_start,
        "status": status,
        "inputs": inputs,
        "digest_md": digest_md,
        "content_pull": content_pull,
        "reasoning": reasoning,
        "run_started_at": run_started_at,
        "run_finished_at": run_finished_at,
    })
    return supabase.insert(RESEARCH_RUNS, row)


def update_outcome(run_id, *, verdict=None, note=None, metrics=None):
    """Fill in / refresh a run's outcome after you've judged it or content has
    landed. Stamps outcome_checked_at automatically. Returns the updated row(s);
    [] if nothing to change.

    verdict (L1, manual): 'strong' | 'ok' | 'off'   (vocab enforced here, not in SQL)
    note    (L1, manual): one-line founder read
    metrics (L2, auto):   downstream content performance, attributed back by item_id
    """
    changes = _compact({
        "outcome_verdict": verdict,
        "outcome_note": note,
        "outcome_metrics": metrics,
    })
    if not changes:
        return []
    changes["outcome_checked_at"] = _now()
    return supabase.update(RESEARCH_RUNS, {"id": run_id}, changes)


def recent_runs_for_learning(limit=5, agent_name="research"):
    """The most recent GRADED runs (newest first) — what the agent reads at the
    START of a run to learn from past decisions + your verdicts. Filtered to runs
    that actually carry an outcome_verdict, so a few ungraded weeks can't blank the
    learning input: `limit` counts GRADED runs, not calendar-recent ones. Returns
    only the fields the prompt uses, to stay lean. Pass agent_name=None for all agents.
    """
    params = {
        "select": "id,week_start,created_at,reasoning,outcome_verdict,outcome_note",
        "outcome_verdict": "not.is.null",  # only graded runs carry a learning signal
        "order": "created_at.desc",
        "limit": limit,
    }
    if agent_name:
        params["agent_name"] = f"eq.{agent_name}"
    return supabase.select(RESEARCH_RUNS, params=params)


def recent_content_pulls(limit=3, agent_name="research"):
    """The last few runs' content_pull objects with their dates (newest first),
    graded or not — the pull prompt's don't-repeat-yourself input. Distinct from
    recent_runs_for_learning, which only returns GRADED runs (the quality signal);
    this one is about novelty, so every recent pick counts."""
    params = {
        "select": "week_start,created_at,content_pull",
        "order": "created_at.desc",
        "limit": limit,
    }
    if agent_name:
        params["agent_name"] = f"eq.{agent_name}"
    return supabase.select(RESEARCH_RUNS, params=params)


def recent_digest_mds(n=3, agent_name="research"):
    """The last n runs' digest texts joined newest-first — the digest's
    "PREVIOUSLY REPORTED" dedupe block. Three weeks, not one, because items
    that skip a week resurface looking new with a one-week memory (the Visa
    AI assistant got re-reported as fresh on 2026-08-02 after appearing
    2026-07-19). Sourced from Supabase, so it works even when the local radar
    DB is ephemeral (e.g. the cloud cron). Returns the joined text or None."""
    params = {"select": "digest_md,created_at", "order": "created_at.desc",
              "limit": n}
    if agent_name:
        params["agent_name"] = f"eq.{agent_name}"
    rows = supabase.select(RESEARCH_RUNS, params=params)
    parts = [f"--- digest from {r.get('created_at', '?')[:10]} ---\n{r['digest_md']}"
             for r in rows if r.get("digest_md")]
    return "\n\n".join(parts) or None


def latest_content_pull(agent_name="research"):
    """The most recent run's content_pull object (headline, video_angle,
    competitor_note, reddit_themes[], feature_seed). The reply judge injects this
    as the timely 'what we're talking about this week' referent for scoring fit.
    Sourced from Supabase like last_digest_md, so it works from the cloud cron.
    Returns the dict or None."""
    params = {"select": "content_pull", "order": "created_at.desc", "limit": 1}
    if agent_name:
        params["agent_name"] = f"eq.{agent_name}"
    rows = supabase.select(RESEARCH_RUNS, params=params)
    return (rows[0].get("content_pull") if rows else None) or None


def log_execution(*, agent_name, event, status=None, model=None,
                  tokens_input=None, tokens_output=None, cost_usd=None,
                  duration_ms=None, message=None, detail=None,
                  correlation_id=None):
    """Append one ops row to agent_execution_logs (every agent calls this).

    correlation_id ties a log line to its run (e.g. a research_runs.id) without a
    hard FK, since all agents share this table. Returns the inserted row.
    """
    row = _compact({
        "agent_name": agent_name,
        "correlation_id": correlation_id,
        "event": event,
        "status": status,
        "model": model,
        "tokens_input": tokens_input,
        "tokens_output": tokens_output,
        "cost_usd": cost_usd,
        "duration_ms": duration_ms,
        "message": message,
        "detail": detail,
    })
    return supabase.insert(EXECUTION_LOGS, row)


# ── Heartbeat / dead-man's switch ────────────────────────────────────────────
# The autonomous weekly cron has nobody watching it. A failed scrape, an auth
# error, or a cron that simply stops firing all look identical from outside. So
# every scheduled run records a heartbeat in the ops log, and an external check
# (or `python -m fleet.research_run --check-heartbeat`) reads the last
# SUCCESSFUL one to tell whether the job has silently gone dark.
HEARTBEAT_EVENT = "heartbeat"


def record_heartbeat(*, agent_name="research", status="success", message=None,
                     correlation_id=None, model=None, tokens_input=None,
                     tokens_output=None, cost_usd=None, duration_ms=None,
                     detail=None):
    """Write the run's 'I'm alive' signal to agent_execution_logs. status is
    'success' on a clean run, 'failure' on a degraded/failed one (the ops-log
    vocab). A thin wrapper over log_execution with a fixed event.

    The usage kwargs (model/tokens/cost/duration) are filled from an
    llm_meter.totals() roll-up — record_heartbeat(..., **usage) — so the
    heartbeat doubles as the run's telemetry row on the /week Fleet view.
    """
    return log_execution(agent_name=agent_name, event=HEARTBEAT_EVENT,
                         status=status, message=message,
                         correlation_id=correlation_id, model=model,
                         tokens_input=tokens_input, tokens_output=tokens_output,
                         cost_usd=cost_usd, duration_ms=duration_ms,
                         detail=detail)


def _parse_ts(iso):
    """PostgREST timestamptz string -> aware datetime (tolerates a trailing Z)."""
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def last_success_at(agent_name="research"):
    """ISO timestamp of the most recent SUCCESSFUL heartbeat, or None if the
    agent has never recorded one. The dead-man's switch reads this."""
    params = {
        "select": "created_at",
        "event": f"eq.{HEARTBEAT_EVENT}",
        "status": "eq.success",
        "order": "created_at.desc",
        "limit": 1,
    }
    if agent_name:
        params["agent_name"] = f"eq.{agent_name}"
    rows = supabase.select(EXECUTION_LOGS, params=params)
    return (rows[0].get("created_at") if rows else None) or None


def heartbeat_status(agent_name="research", max_age_days=8):
    """Dead-man's switch read-out. Returns
    {last_success, age_days, stale, max_age_days}. `stale` is True when the last
    successful heartbeat is older than max_age_days OR there has never been one.
    For a WEEKLY job the default 8 days = the cadence plus a day of slack."""
    last = last_success_at(agent_name)
    if not last:
        return {"last_success": None, "age_days": None,
                "stale": True, "max_age_days": max_age_days}
    age_days = (datetime.now(timezone.utc) - _parse_ts(last)).total_seconds() / 86400
    return {"last_success": last, "age_days": round(age_days, 2),
            "stale": age_days > max_age_days, "max_age_days": max_age_days}
