"""The one interface the reply agents — and me, reviewing in the CLI — use to read
and write the reply system's tables in Supabase's `marketing` schema.

Same spirit as fleet/store.py (one domain API for both drivers) but for the reply
pipeline. Three tables, three jobs:

  * reply_candidates -> the finder's output + the thin judge's verdict (the queue)
  * reply_drafts     -> the drafter's 2-3 variants per candidate (incl. the regex gate)
  * reply_ledger     -> one row per SENT reply, WITH the draft->final diff (the moat)

The transport lives in fleet/supabase.py; this module is the vocabulary. Typical
flow across the pipeline:

    from fleet import reply_store as rs

    # finder: upsert candidates (dedup on platform+external_id), judge the new ones
    new = rs.log_candidates(candidates, platform="reddit")
    rs.update_judge(new[0]["id"], verdict="engage", score=82, on_theme=True,
                    rationale="beginner buy/sell Q, squarely in the self-knowledge lane")

    # drafter: 2-3 gated variants for an 'engage' candidate
    rs.log_drafts(cand_id, platform="reddit", variants=[...], style_version="guide-v1")

    # me, in the CLI: pick a variant, edit it, send by hand, log the diff
    rs.log_sent_reply(candidate_id=cand_id, draft_id=draft_id, final_sent=my_text,
                      edit_type="voice", intent_preserved=True,
                      style_notes="cut the hedge; shorter open", predicted_grade="strong")

    # days later, once engagement is in:
    rs.update_outcome(ledger_id, outcome_score=14, op_replied="yes")

The draft->final diff (edit_delta / edit_distance / edit_ratio) is computed here with
stdlib difflib, so the voice-learning signal is captured at the one write everything
funnels through.
"""

import difflib
from datetime import datetime, timedelta, timezone

from fleet import supabase

REPLY_CANDIDATES = "reply_candidates"
REPLY_DRAFTS = "reply_drafts"
REPLY_LEDGER = "reply_ledger"


def _now():
    return datetime.now(timezone.utc).isoformat()


def _compact(d):
    """Drop None values so DB column defaults apply to anything omitted."""
    return {k: v for k, v in d.items() if v is not None}


def _get_one(table, row_id):
    """Fetch a single row by id, or {} if it's gone."""
    rows = supabase.select(table, params={"id": f"eq.{row_id}", "limit": 1})
    return rows[0] if rows else {}


def _diff_stats(ai_draft, final_sent):
    """Return (edit_delta, edit_distance, edit_ratio) for ai_draft -> final_sent.

    ai_draft is None when you wrote your own reply (nothing to diff against), in
    which case all three are None. edit_ratio is SequenceMatcher.ratio() (1.0 = sent
    verbatim); edit_distance is a symmetric char-level distance; edit_delta is a
    unified diff you can eyeball.
    """
    if ai_draft is None:
        return None, None, None
    matcher = difflib.SequenceMatcher(None, ai_draft, final_sent, autojunk=False)
    ratio = round(matcher.ratio(), 4)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    distance = (len(ai_draft) - matched) + (len(final_sent) - matched)
    delta = "\n".join(difflib.unified_diff(
        ai_draft.splitlines(), final_sent.splitlines(),
        fromfile="ai_draft", tofile="final_sent", lineterm="", n=2,
    ))
    return (delta or None), distance, ratio


# ── Candidates: finder output + thin judge ───────────────────────────────────
# The channel adapter normalizes the scanner's (group, phrase) tuples into plain
# strings before calling log_candidates, so this stays channel-agnostic.
_CANDIDATE_COLS = [
    "platform", "intended_use", "external_id",
    "post_url", "community", "post_title", "post_excerpt", "post_author", "post_created_at",
    "finder_score", "flips", "soft_triggers", "angle", "flip_group", "finder_signal",
    "source_research_run_id", "run_correlation_id",
]


def log_candidates(candidates, *, platform="reddit", intended_use="reply",
                   source_research_run_id=None, run_correlation_id=None):
    """Upsert finder candidates, deduping on (platform, external_id).

    Existing rows are left UNTOUCHED (ignore-duplicates) so a re-scanned post never
    resets a judged/drafted/sent one — and only the genuinely NEW rows are returned,
    which is exactly the set the judge needs to score (no wasted LLM calls on
    duplicates). Each candidate is a normalized dict; keys consumed: external_id,
    post_url, community, post_title, post_excerpt, post_author, post_created_at,
    finder_score, flips, soft_triggers, angle, flip_group, finder_signal.
    platform / intended_use / provenance are applied here.
    """
    rows = []
    for c in candidates:
        row = {col: c.get(col) for col in _CANDIDATE_COLS if c.get(col) is not None}
        row["platform"] = c.get("platform") or platform
        row["intended_use"] = c.get("intended_use") or intended_use
        if source_research_run_id is not None:
            row.setdefault("source_research_run_id", source_research_run_id)
        if run_correlation_id is not None:
            row.setdefault("run_correlation_id", run_correlation_id)
        rows.append(row)
    if not rows:
        return []
    return supabase.upsert(REPLY_CANDIDATES, rows,
                           on_conflict="platform,external_id",
                           resolution="ignore-duplicates")


def update_judge(candidate_id, *, verdict=None, score=None, rationale=None,
                 on_theme=None, audience_fit=None, model=None, judge=None,
                 status="judged"):
    """Write the thin judge's verdict onto a candidate and advance it to 'judged'.

    verdict: 'engage' | 'maybe' | 'skip'  (vocab enforced in code, not SQL)
    on_theme: the PRIMARY signal (does it fit our content); audience_fit: core |
    adjacent | off (collaborators = positive). judge: the full jsonb payload.
    """
    changes = _compact({
        "judge_verdict": verdict,
        "judge_score": score,
        "judge_rationale": rationale,
        "on_theme": on_theme,
        "audience_fit": audience_fit,
        "judge_model": model,
        "judge": judge,
        "status": status,
    })
    if not changes:
        return []
    return supabase.update(REPLY_CANDIDATES, {"id": candidate_id}, changes)


# Freshness windows, hours (founder call 2026-08-13): a reply's reach dies with
# the post, so unworked candidates leave the queue on their own instead of
# accumulating. X targets are dead after ~a day; Reddit rides a week because
# that queue doubles as feature-signal listening while the account is suspended.
# The review page mirrors these in FRESH_HOURS (fleet/review.html).
EXPIRE_HOURS = {"x": 36, "reddit": 7 * 24}


def expire_stale_candidates():
    """Mark unworked candidates past their platform's freshness window 'expired'.

    Only 'judged' and 'drafted' rows age out — 'sent' and 'skipped' are history,
    not queue. Runs at the top of every reply job. Returns {platform: n_expired}.
    """
    expired = {}
    for platform, hours in EXPIRE_HOURS.items():
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        stale = supabase.select(REPLY_CANDIDATES, params={
            "select": "id",
            "platform": f"eq.{platform}",
            "status": "in.(judged,drafted)",
            "created_at": f"lt.{cutoff}",
            "limit": 200,
        })
        for row in stale:
            supabase.update(REPLY_CANDIDATES, {"id": row["id"]},
                            {"status": "expired"}, returning=False)
        expired[platform] = len(stale)
    return expired


def dismiss_candidate(candidate_id, *, reason=None):
    """Mark a candidate 'skipped'. dismiss_reason on a high-scored pick is a
    finder false-positive signal the judge loop learns from later."""
    return supabase.update(REPLY_CANDIDATES, {"id": candidate_id},
                           _compact({"status": "skipped", "dismiss_reason": reason}))


def get_candidate(candidate_id):
    """Fetch one candidate row by id (or {} if gone) — the drafter/CLI's direct
    lookup when working a specific candidate rather than draining the queue."""
    return _get_one(REPLY_CANDIDATES, candidate_id)


def candidates_to_draft(platform="reddit", limit=5):
    """The drafter's work queue: judged candidates the judge said ENGAGE that don't
    have drafts yet, highest-scored / freshest first."""
    params = {
        "select": "*",
        "status": "eq.judged",
        "judge_verdict": "eq.engage",
        "order": "judge_score.desc,created_at.desc",
        "limit": limit,
    }
    if platform:
        params["platform"] = f"eq.{platform}"
    return supabase.select(REPLY_CANDIDATES, params=params)


# ── Drafts: the drafter's 2-3 variants ───────────────────────────────────────
_DRAFT_COLS = ["variant_index", "angle", "draft_text", "rationale", "model",
               "style_version", "gate_status", "gate_violations", "gate_ruleset_version"]


def log_drafts(candidate_id, *, platform, variants, style_version=None):
    """Insert one reply_drafts row per variant and advance the candidate to
    'drafted'. Each variant is a dict: variant_index, angle, draft_text, rationale,
    model, gate_status, gate_violations, gate_ruleset_version. Empty drafts are
    skipped (never stored). Returns the inserted rows.
    """
    inserted = []
    for v in variants:
        if not (v.get("draft_text") or "").strip():
            continue
        row = {col: v.get(col) for col in _DRAFT_COLS if v.get(col) is not None}
        row["candidate_id"] = candidate_id
        row["platform"] = platform
        if style_version is not None:
            row.setdefault("style_version", style_version)
        inserted.append(supabase.insert(REPLY_DRAFTS, row))
    if inserted:
        supabase.update(REPLY_CANDIDATES, {"id": candidate_id}, {"status": "drafted"})
    return inserted


def drafts_for_candidate(candidate_id):
    """A candidate's variants, in order — what the CLI shows you to pick from."""
    return supabase.select(REPLY_DRAFTS, params={
        "candidate_id": f"eq.{candidate_id}",
        "order": "variant_index.asc",
    })


def choose_variant(draft_id):
    """Mark a variant as the one you picked (your selection signal)."""
    return supabase.update(REPLY_DRAFTS, {"id": draft_id}, {"chosen": True})


def review_queue(platform="reddit", limit=20):
    """Candidates whose drafts are ready for your review (not yet sent), freshest
    first — on a reply channel, fresh beats high-scoring (founder call 2026-08-13)."""
    params = {
        "select": "*",
        "status": "eq.drafted",
        "order": "created_at.desc,judge_score.desc",
        "limit": limit,
    }
    if platform:
        params["platform"] = f"eq.{platform}"
    return supabase.select(REPLY_CANDIDATES, params=params)


# ── Ledger: the sent reply WITH the diff (the moat) ──────────────────────────
def log_sent_reply(*, final_sent, candidate_id=None, draft_id=None, platform=None,
                   edit_type=None, intent_preserved=None, style_notes=None,
                   predicted_grade=None, replied_at=None, reply_url=None,
                   rationale=None, idea_id=None, source_research_run_id=None,
                   legacy_sqlite_id=None):
    """Append one reply_ledger row: the reply you actually sent, with the captured
    draft->final diff. This is the heart of the voice-learning loop.

    It pulls the post snapshot + finder/judge signal off the candidate and the AI's
    copy off the chosen draft (so the row stands alone as a training example),
    computes the diff, and inserts append-once. Pass draft_id when you edited one of
    the variants; omit it (ai_draft stays null) when you wrote your own. Also
    advances the candidate to 'sent' and flags the chosen draft.

    edit_type (you set this in the CLI): none | voice | substance | both | rejected.
    If omitted, defaults to 'none' when sent verbatim, 'rejected' when you wrote your
    own. intent_preserved + style_notes are the one-line "why I changed it" labels.
    """
    if not (final_sent or "").strip():
        raise ValueError("final_sent is required — the sent reply is the moat.")

    candidate = _get_one(REPLY_CANDIDATES, candidate_id) if candidate_id else {}
    draft = _get_one(REPLY_DRAFTS, draft_id) if draft_id else {}

    ai_draft = draft.get("draft_text")
    delta, distance, ratio = _diff_stats(ai_draft, final_sent)

    if edit_type is None:
        if ai_draft is None:
            edit_type = "rejected"                       # you wrote your own
        elif ai_draft.strip() == final_sent.strip():
            edit_type = "none"                           # sent verbatim

    row = _compact({
        "platform": platform or candidate.get("platform") or "reddit",
        "replied_at": replied_at or _now(),
        # post snapshot (copied so the row stands alone)
        "external_id": candidate.get("external_id"),
        "post_url": candidate.get("post_url"),
        "community": candidate.get("community"),
        "post_title": candidate.get("post_title"),
        "post_excerpt": candidate.get("post_excerpt"),
        "post_author": candidate.get("post_author"),
        # finder + judge signal
        "finder_score": candidate.get("finder_score"),
        "flips": candidate.get("flips"),
        "soft_triggers": candidate.get("soft_triggers"),
        "angle": draft.get("angle") or candidate.get("angle"),
        "judge_verdict": candidate.get("judge_verdict"),
        "judge_score": candidate.get("judge_score"),
        "judge_rationale": candidate.get("judge_rationale"),
        # diff capture (the learning signal)
        "ai_draft": ai_draft,
        "chosen_variant": draft.get("variant_index"),
        "final_sent": final_sent,
        "edit_delta": delta,
        "edit_distance": distance,
        "edit_ratio": ratio,
        "edit_type": edit_type,
        "intent_preserved": intent_preserved,
        "style_notes": style_notes,
        "predicted_grade": predicted_grade,
        "reply_url": reply_url,
        "rationale": rationale,
        # provenance
        "candidate_id": candidate_id,
        "draft_id": draft_id,
        "style_version": draft.get("style_version"),
        "source_research_run_id": source_research_run_id or candidate.get("source_research_run_id"),
        "idea_id": idea_id,
        "legacy_sqlite_id": legacy_sqlite_id,
    })
    ledger = supabase.insert(REPLY_LEDGER, row)
    if candidate_id:
        supabase.update(REPLY_CANDIDATES, {"id": candidate_id}, {"status": "sent"})
    if draft_id:
        supabase.update(REPLY_DRAFTS, {"id": draft_id}, {"chosen": True})
    return ledger


# Legacy SQLite reply_log columns that map 1:1 onto reply_ledger (same name).
_LEGACY_PASSTHROUGH = [
    "platform", "post_url", "community", "post_title", "post_excerpt", "post_author",
    "finder_score", "flips", "soft_triggers", "angle",
    "reply_url", "rationale", "predicted_grade",
    "outcome_score", "op_replied", "led_to_profile", "outcome_notes", "outcome_checked_at",
]


def import_legacy_replies(legacy_rows):
    """Idempotently backfill pre-cutover SQLite reply_log rows into reply_ledger.

    Each legacy row is a dict from reply_finder's reply_log table. It predates the
    finder/judge/drafter, so there's no candidate, no draft, and no AI draft to diff
    against: the hand-written reply_text becomes final_sent, ai_draft / edit_* stay
    null, and edit_type='legacy' tags it OUT of the voice metrics (the
    reply_weekly_metrics view excludes 'legacy') while keeping it as a 'what landed'
    + calibration example.

    Idempotency is the DB's job: legacy_sqlite_id carries the source row id under a
    unique index, so this upserts ignore-duplicates on it — re-running imports nothing
    new (and live-flow rows, whose legacy_sqlite_id is null, never collide). Returns
    only the rows actually inserted this run.
    """
    rows = []
    for r in legacy_rows:
        text = (r.get("reply_text") or "").strip()
        sqlite_id = r.get("id")
        if not text or sqlite_id is None:
            continue                                   # final_sent NOT NULL; need the key
        row = {col: r.get(col) for col in _LEGACY_PASSTHROUGH if r.get(col) is not None}
        row["final_sent"] = text
        row["legacy_sqlite_id"] = sqlite_id
        row["edit_type"] = "legacy"
        row["replied_at"] = r.get("replied_at") or _now()
        if r.get("idea_id") is not None:
            row["idea_id"] = str(r["idea_id"])         # ledger idea_id is text, SQLite is int
        rows.append(row)
    if not rows:
        return []
    return supabase.upsert(REPLY_LEDGER, rows,
                           on_conflict="legacy_sqlite_id",
                           resolution="ignore-duplicates")


def existing_legacy_ids():
    """The set of legacy_sqlite_id values already in the ledger — lets the backfill
    report new-vs-already-imported (and run a meaningful --dry-run) without leaning on
    the upsert's return shape."""
    rows = supabase.select(REPLY_LEDGER, params={
        "select": "legacy_sqlite_id", "legacy_sqlite_id": "not.is.null"})
    return {r["legacy_sqlite_id"] for r in rows if r.get("legacy_sqlite_id") is not None}


def set_reply_url(ledger_id, url):
    """Attach the live permalink after you post by hand. reply_url is the one
    post-send field outside the moat's write-once columns (ai_draft / final_sent /
    edit_* stay frozen), so the CLI can fill it in once the reply is up."""
    return supabase.update(REPLY_LEDGER, {"id": ledger_id}, {"reply_url": url})


def ledger_for_distiller(limit=100):
    """Every real (non-legacy) sent reply with its diff-capture fields, oldest
    first — the distiller's training data. Legacy imports carry no draft->final
    diff, so they teach nothing about voice."""
    return supabase.select(REPLY_LEDGER, params={
        "select": ("id,platform,replied_at,ai_draft,final_sent,edit_ratio,"
                   "edit_type,intent_preserved,style_notes,predicted_grade,"
                   "curation_tag"),
        "edit_type": "neq.legacy",
        "order": "replied_at.asc",
        "limit": limit,
    })


def ledger_gold_pairs(limit=200):
    """Sent replies WITH their post snapshot — the eval harness seeds its gold
    set from these (filtering happens in the agent; this is just the read)."""
    return supabase.select(REPLY_LEDGER, params={
        "select": ("id,platform,community,post_title,post_excerpt,final_sent,"
                   "edit_type,edit_ratio,curation_tag,replied_at"),
        "edit_type": "neq.legacy",
        "order": "replied_at.asc",
        "limit": limit,
    })


def awaiting_outcomes(*, platform=None, min_age_hours=48, limit=100):
    """Sent replies still missing an outcome check, oldest first — the outcome
    checker agent's work queue (and the session ritual's). min_age_hours gives a
    reply time to accumulate engagement before it's worth reading."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=min_age_hours)).isoformat()
    params = {
        "select": ("id,platform,replied_at,final_sent,reply_url,post_url,"
                   "post_title,post_author,community"),
        "outcome_checked_at": "is.null",
        "replied_at": f"lte.{cutoff}",
        "order": "replied_at.asc",
        "limit": limit,
    }
    if platform:
        params["platform"] = f"eq.{platform}"
    return supabase.select(REPLY_LEDGER, params=params)


_OUTCOME_FIELDS = ["outcome_score", "op_replied", "led_to_profile",
                   "outcome_notes", "outcome_metrics"]


def update_outcome(ledger_id, *, outcome_score=None, op_replied=None,
                   led_to_profile=None, outcome_notes=None, outcome_metrics=None):
    """Fill in / refresh a sent reply's outcome once it's had time to land. Stamps
    outcome_checked_at automatically. op_replied / led_to_profile: yes | no | unknown.
    Returns the updated row(s); [] if nothing to change."""
    changes = _compact({
        "outcome_score": outcome_score,
        "op_replied": op_replied,
        "led_to_profile": led_to_profile,
        "outcome_notes": outcome_notes,
        "outcome_metrics": outcome_metrics,
    })
    if not changes:
        return []
    changes["outcome_checked_at"] = _now()
    return supabase.update(REPLY_LEDGER, {"id": ledger_id}, changes)


def recent_sent(limit=20, platform=None):
    """Recent sent replies (newest first) — the learning read, like the old
    reply_finder.recent_replies but from Supabase."""
    params = {"select": "*", "order": "replied_at.desc", "limit": limit}
    if platform:
        params["platform"] = f"eq.{platform}"
    return supabase.select(REPLY_LEDGER, params=params)


def curated_exemplars(limit=8, platform=None):
    """Sent replies you've flagged as voice exemplars (curation_tag='exemplar') —
    few-shot fuel for the drafter. Empty until you curate; the drafter falls back to
    the hand-seeded examples in fleet/reply_style_guide.md."""
    params = {"select": "*", "curation_tag": "eq.exemplar",
              "order": "replied_at.desc", "limit": limit}
    if platform:
        params["platform"] = f"eq.{platform}"
    return supabase.select(REPLY_LEDGER, params=params)


def negative_examples(limit=3, platform=None):
    """Sent replies flagged as off-voice negatives (curation_tag='negative') — the
    'not like this, because…' few-shot that teaches voice fastest at low volume."""
    params = {"select": "*", "curation_tag": "eq.negative",
              "order": "replied_at.desc", "limit": limit}
    if platform:
        params["platform"] = f"eq.{platform}"
    return supabase.select(REPLY_LEDGER, params=params)
