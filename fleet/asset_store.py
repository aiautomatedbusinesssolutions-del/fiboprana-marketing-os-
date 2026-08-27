"""The one interface for the Marketing OS spine tables — videos, decisions,
experiments, outcome_checks (MARKETING_OS.md §3-4).

Same spirit as reply_store.py (one domain API that both me-in-chat and the agents
call), but for the lever-ledger system that generalizes what the reply loop proved:

  * videos         -> the lineage spine (idea -> published -> closed)
  * decisions      -> one row per lever-tagged decision, self-contained training
                      example: chosen + alternatives + reasoning (+ prediction
                      when a real hypothesis exists)
  * experiments    -> hypotheses that span min_n observations; the DB enforces
                      one-active-globally (+ one concurrent Test & Compare)
  * outcome_checks -> the 24h/7d/28d work queue that closes the loop

This module is also where the learning contract's soft rules live (enforced in
the store funnel, bypassable only via raw PostgREST — the column-scoped grants
are the hard layer):

  * lever/surface/mode vocabulary          -> levers.validate()
  * alternatives-considered are mandatory  -> log_decision() refuses without them
  * predictions are pre-publish only       -> log_decision() refuses a published subject
  * predictions required iff experiment    -> log_decision() enforces both directions
  * one CTR experiment at a time           -> open_experiment() guards title vs T&C
  * outcome_basis is explicit at close     -> update_decision_outcome() requires it

Typical week:

    from fleet import asset_store as a

    v = a.create_video(slug="oura-adds-mind-tracking", pillar="mind-science",
                       reasoning="perishable news angle from run 5bd4...",
                       source_research_run_id=run_id)
    a.update_video(v["id"], status="scripted", script_path="videos/scripts/...")

    a.log_decision(lever="title_wording", surface="youtube",
                   subject_type="video", subject_id=v["id"],
                   chosen="Oura Now Watches Your Mind. Kind Of.",
                   alternatives=[{"option": "Oura's New Stress Feature...", "why_rejected": "buries the news"}],
                   reasoning="lead with the mind-tracking angle; trackers search the brand")

    a.mark_published(v["id"], youtube_video_id="dQw4...")   # arms the outcome clock

    for c in a.due_checks():                                 # daily banner / analytics agent
        a.close_check(c["id"], result={"views": 214, "ctr": 4.1}, source="manual")
"""

from datetime import datetime, timedelta, timezone

from fleet import levers, supabase

VIDEOS = "videos"
DECISIONS = "decisions"
EXPERIMENTS = "experiments"
OUTCOME_CHECKS = "outcome_checks"
CONTENT_CALENDAR = "content_calendar"

# Outcome windows per entity (MARKETING_OS.md §6). Fixed at publish, never
# Sunday-relative — the clock is causal, not calendar.
WINDOWS = {
    VIDEOS: ("24h", "7d", "28d"),
    CONTENT_CALENDAR: ("7d",),
    "reply_ledger": ("7d",),
}
WINDOW_DELTAS = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "28d": timedelta(days=28)}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat() if isinstance(dt, datetime) else dt


def _compact(d):
    """Drop None values so DB column defaults apply to anything omitted."""
    return {k: v for k, v in d.items() if v is not None}


def _get_one(table, row_id):
    rows = supabase.select(table, params={"id": f"eq.{row_id}", "limit": 1})
    return rows[0] if rows else {}


# ── videos: the lineage spine ────────────────────────────────────────────────
def create_video(*, slug, pillar=None, status="idea", reasoning=None,
                 source_research_run_id=None, source_item_id=None,
                 title_final=None, script_path=None, transcript_path=None,
                 deck_path=None, payload=None, notes=None, legacy=False):
    """Insert a video node, deduping on slug (idempotent: re-creating an existing
    slug returns the existing row untouched). slug is the dedup memory that
    retires IDEAS_LOG.md."""
    row = _compact({
        "slug": slug, "pillar": pillar, "status": status, "reasoning": reasoning,
        "source_research_run_id": source_research_run_id,
        "source_item_id": source_item_id, "title_final": title_final,
        "script_path": script_path, "transcript_path": transcript_path,
        "deck_path": deck_path, "payload": payload, "notes": notes,
    })
    if legacy:
        row["legacy"] = True
    inserted = supabase.upsert(VIDEOS, row, on_conflict="slug",
                               resolution="ignore-duplicates")
    if inserted:
        return inserted[0]
    return video_by_slug(slug)


def video_by_slug(slug):
    rows = supabase.select(VIDEOS, params={"slug": f"eq.{slug}", "limit": 1})
    return rows[0] if rows else {}


def get_video(video_id):
    return _get_one(VIDEOS, video_id)


def videos_by_status(status=None, limit=50):
    """The pipeline read: what's parked at which gate. status=None -> everything
    still in flight (not closed/skipped)."""
    params = {"select": "*", "order": "created_at.desc", "limit": limit}
    if status:
        params["status"] = f"eq.{status}"
    else:
        params["status"] = "not.in.(closed,skipped)"
    return supabase.select(VIDEOS, params=params)


_VIDEO_MUTABLE = ["pillar", "status", "title_final", "youtube_video_id", "published_at",
                  "script_path", "transcript_path", "deck_path", "payload", "notes",
                  "outcome_verdict", "outcome_note", "outcome_metrics", "outcome_checked_at"]


def update_video(video_id, **fields):
    """Advance lifecycle facts (status, paths, title_final...). Only the mutable
    columns pass through — slug/provenance/reasoning are write-once by grant, so
    a typo here fails loudly at the DB instead of silently."""
    changes = _compact({k: _iso(v) if k.endswith("_at") else v
                        for k, v in fields.items() if k in _VIDEO_MUTABLE})
    unknown = set(fields) - set(_VIDEO_MUTABLE)
    if unknown:
        raise ValueError(f"not updatable on videos: {sorted(unknown)}")
    if not changes:
        return []
    return supabase.update(VIDEOS, {"id": video_id}, changes)


def mark_published(video_id, *, youtube_video_id, published_at=None):
    """The one required manual entry after uploading by hand: records the publish
    facts AND opens the 24h/7d/28d outcome checks — published_at arms the clock
    (the causal anchor). Idempotent: re-running never duplicates checks."""
    when = published_at or _now()
    rows = supabase.update(VIDEOS, {"id": video_id}, {
        "status": "published",
        "youtube_video_id": youtube_video_id,
        "published_at": _iso(when),
    })
    checks = open_checks(VIDEOS, video_id, published_at=when)
    return {"video": rows[0] if rows else None, "checks_opened": len(checks)}


# ── decisions: the uniform lever ledger ──────────────────────────────────────
def log_calendar_post(*, platform, post_type, draft_text, title=None,
                      video_id=None, pillar=None, hook_text=None,
                      gate=None, payload=None, notes=None, status="draft"):
    """One posts-ledger row (content_calendar) for a generated package —
    the repurposer's write. draft_text is the write-once diff anchor;
    final_text/edit_* land later at schedule time. gate is the dict
    compliance_gate.check() returns."""
    row = _compact({
        "platform": platform,
        "post_type": post_type,
        "status": status,
        "title": title,
        "video_id": video_id,
        "pillar": pillar,
        "hook_text": hook_text,
        "draft_text": draft_text,
        "payload": payload,
        "notes": notes,
        **(gate or {}),
    })
    return supabase.insert(CONTENT_CALENDAR, row)


def log_decision(*, lever, surface, subject_type, subject_id, chosen,
                 alternatives, reasoning, agent_name="founder", model=None,
                 mode="routine", recipe_version=None, hypothesis=None,
                 predicted_grade=None, prediction=None, experiment_id=None,
                 ab_variant=None, decided_at=None):
    """Append one lever-tagged decision row (write-once; only its outcome layer
    ever mutates). The learning contract, enforced (MARKETING_OS.md §3):

      * alternatives are half the signal -> required, non-empty list of
        {option, why_rejected}. For a frozen/no-real-choice lever, say so
        explicitly: [{"option": "none", "why_rejected": "lever frozen 4 weeks"}].
      * predictions are honest only pre-publish -> refuses a subject that is
        already published (videos / calendar posts).
      * no hypothesis theater -> mode='experiment' REQUIRES hypothesis +
        prediction + experiment_id; mode='routine' takes none of the three
        (predicted_grade alone is still fine on routine rows).
    """
    levers.validate(lever=lever, surface=surface, mode=mode, subject_type=subject_type)
    if not alternatives:
        raise ValueError("alternatives are half the signal — pass at least one "
                         '{option, why_rejected} (or an explicit "none" entry).')
    if predicted_grade is not None and predicted_grade not in levers.GRADES:
        raise ValueError(f"predicted_grade must be one of {sorted(levers.GRADES)}")

    if mode == "experiment":
        missing = [f for f, v in (("hypothesis", hypothesis), ("prediction", prediction),
                                  ("experiment_id", experiment_id)) if not v]
        if missing:
            raise ValueError(f"mode='experiment' requires {', '.join(missing)}")
    elif hypothesis or prediction or experiment_id:
        raise ValueError("hypothesis/prediction/experiment_id belong on "
                         "mode='experiment' rows — no hypothesis theater on routine ones.")

    _refuse_if_published(subject_type, subject_id)

    row = _compact({
        "agent_name": agent_name, "model": model,
        "lever": lever, "surface": surface,
        "subject_type": subject_type, "subject_id": subject_id, "mode": mode,
        "decision": _compact({"chosen": chosen, "alternatives": alternatives,
                              "recipe_version": recipe_version}),
        "reasoning": reasoning,
        "hypothesis": hypothesis, "predicted_grade": predicted_grade,
        "prediction": prediction, "experiment_id": experiment_id,
        "ab_variant": ab_variant, "decided_at": _iso(decided_at),
    })
    return supabase.insert(DECISIONS, row)


def _refuse_if_published(subject_type, subject_id):
    """Predictions and decisions are pre-publish signals. A decision 'about' an
    already-published video/post would be indistinguishable from hindsight."""
    table = {"video": VIDEOS, "post": CONTENT_CALENDAR}.get(subject_type)
    if table is None:
        return  # replies log at send by design; research runs don't publish
    subject = _get_one(table, subject_id)
    if subject.get("published_at"):
        raise ValueError(f"{subject_type} {subject_id} is already published — "
                         "decisions must be logged before publish (no hindsight rows).")


def update_decision_outcome(decision_id, *, basis, verdict=None, score=None,
                            metrics=None, note=None, prediction_hit=None):
    """Close a decision row against its lever's IDENTIFYING metric. basis is
    REQUIRED and explicit: pass the lever's canonical basis (levers.OUTCOME_BASIS)
    when the signal was clean, or 'none' when it wasn't (confound, missing data) —
    'none' rows are excluded from calibration rather than pretending. Stamps
    outcome_checked_at."""
    if not basis:
        raise ValueError("outcome_basis is required — the lever's identifying metric, "
                         "or the explicit 'none' when the signal wasn't clean.")
    changes = _compact({
        "outcome_basis": basis, "outcome_verdict": verdict, "outcome_score": score,
        "outcome_metrics": metrics, "outcome_note": note, "prediction_hit": prediction_hit,
    })
    changes["outcome_checked_at"] = _iso(_now())
    return supabase.update(DECISIONS, {"id": decision_id}, changes)


def decisions_for_lever(lever, *, surface=None, closed_only=False, limit=20):
    """A lever's own closed-loop history — what an agent (or I) read at decision
    time. closed_only=True -> only rows whose outcome landed (real lessons)."""
    params = {"select": "*", "lever": f"eq.{lever}",
              "order": "decided_at.desc", "limit": limit}
    if surface:
        params["surface"] = f"eq.{surface}"
    if closed_only:
        params["outcome_checked_at"] = "not.is.null"
    return supabase.select(DECISIONS, params=params)


def open_decisions(limit=50):
    """Decisions still waiting on their outcome — the open-hypotheses ledger."""
    return supabase.select(DECISIONS, params={
        "select": "*", "outcome_checked_at": "is.null",
        "order": "decided_at.asc", "limit": limit})


# ── experiments: the hypotheses layer ────────────────────────────────────────
def open_experiment(*, lever, surface, hypothesis, method, prediction=None,
                    arms=None, scope=None, min_n=None):
    """Open an ACTIVE experiment. The DB enforces one-active-globally (+ one
    concurrent Test & Compare); this store guard adds the cross-index rule the
    indexes can't see: title and thumbnail share the CTR numerator, so a
    title_wording experiment may never overlap a T&C and vice versa."""
    levers.validate(lever=lever, surface=surface)
    if method not in levers.EXPERIMENT_METHODS:
        raise ValueError(f"method must be one of {sorted(levers.EXPERIMENT_METHODS)}")

    if lever in levers.CTR_LEVERS:
        clash = [e for e in active_experiments()
                 if e["lever"] in levers.CTR_LEVERS and e["lever"] != lever]
        if clash:
            raise ValueError(f"CTR collision: {clash[0]['lever']} experiment "
                             f"{clash[0]['id']} is active — title and thumbnail "
                             "share the CTR numerator, one must wait "
                             "(thumbnail wins by default, MARKETING_OS.md §7).")

    row = _compact({
        "lever": lever, "surface": surface, "hypothesis": hypothesis,
        "prediction": prediction, "method": method, "arms": arms,
        "scope": scope, "min_n": min_n,
        "status": "active", "started_at": _iso(_now()),
    })
    return supabase.insert(EXPERIMENTS, row)


def active_experiments():
    return supabase.select(EXPERIMENTS, params={"status": "eq.active"})


def conclude_experiment(experiment_id, *, verdict, note=None, learned_rule=None,
                        applied_to_recipe=None, abandoned=False):
    """Conclude (or abandon) an experiment, freeing the active slot. verdict:
    supported | refuted | inconclusive. learned_rule is the body it leaves —
    hand-paste it into the recipe doc and record where in applied_to_recipe
    (machine proposes; human edits canon)."""
    changes = _compact({
        "status": "abandoned" if abandoned else "concluded",
        "concluded_at": _iso(_now()),
        "result_verdict": verdict, "result_note": note,
        "learned_rule": learned_rule, "applied_to_recipe": applied_to_recipe,
    })
    return supabase.update(EXPERIMENTS, {"id": experiment_id}, changes)


# ── outcome_checks: the work queue that closes the loop ──────────────────────
def open_checks(entity_table, entity_id, *, published_at=None, windows=None):
    """Open the outcome windows for a newly published entity. Idempotent (the
    unique index arbitrates), so the dispatcher's daily sweep can call this for
    anything published-with-no-checks without ever duplicating."""
    when = published_at or _now()
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    rows = [{"entity_table": entity_table, "entity_id": entity_id,
             "window_label": w, "due_at": _iso(when + WINDOW_DELTAS[w])}
            for w in (windows or WINDOWS[entity_table])]
    return supabase.upsert(OUTCOME_CHECKS, rows,
                           on_conflict="entity_table,entity_id,window_label",
                           resolution="ignore-duplicates")


def due_checks(limit=50):
    """Open checks whose window has arrived — the daily banner's nag list and,
    later, the analytics agent's work queue. Oldest first."""
    return supabase.select(OUTCOME_CHECKS, params={
        "select": "*", "status": "eq.open", "due_at": f"lte.{_iso(_now())}",
        "order": "due_at.asc", "limit": limit})


def close_check(check_id, *, result, source="manual"):
    """Close a due check with the raw numbers. source labels trust: yt_api |
    manual | screenshot (hand-entered X numbers are queryably lower-trust)."""
    return supabase.update(OUTCOME_CHECKS, {"id": check_id}, {
        "status": "done", "result": result, "source": source,
        "checked_at": _iso(_now())})


def skip_check(check_id, *, reason=None):
    """Honestly skip a check instead of letting it rot open. The skip itself is
    recorded — close-coverage is a tracked metric."""
    return supabase.update(OUTCOME_CHECKS, {"id": check_id}, {
        "status": "skipped", "result": {"skipped_reason": reason or "unspecified"},
        "checked_at": _iso(_now())})
