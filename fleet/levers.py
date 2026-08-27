"""The lever vocabulary — the learning taxonomy every decisions row is tagged with.

A LEVER is one thing we can deliberately change (a title's wording, a thumbnail's
composition, an upload slot). Learning is logged BY LEVER, not by agent: whether
one packaging agent makes three decisions or three agents make one each is an
implementation detail, but the training data must be lever-separated from day 1
(MARKETING_OS.md §3).

Enforced in code, not SQL (this repo's vocab rule): asset_store.log_decision()
validates against these sets, so a typo'd lever never silently splits a training
set. Text columns in the DB stay free for evolution — add a lever here, no
migration needed.

OUTCOME_BASIS maps each lever to its IDENTIFYING metric — the one signal that can
close a row on THIS lever without confounds (MARKETING_OS.md §6). A row closed
with no clean signal gets outcome_basis='none' and is excluded from calibration
(the view filters it): "[no signal]" is an explicit value, not laziness.

Clone note: these levers are business-agnostic mechanics. Anything
business-specific (pillars, style rules, compliance) lives in its own
config/prompt files, never in logic — this file travels with the machine.
"""

SURFACES = {"youtube", "x", "reddit", "shorts"}

# lever -> (its identifying outcome_basis, one-line honesty note)
OUTCOME_BASIS = {
    # research
    "digest_sourcing":        ("founder_verdict",      "L1 Sunday verdict on the digest"),
    "topic_selection":        ("video_7d_vs_pillar",   "derived video 7d views vs pillar baseline; one hop only"),
    # packaging (per-video)
    "title_wording":          ("ctr_7d_vs_pillar",     "observational; flagged confounded_by_tc if a T&C ran in-window"),
    "thumbnail_composition":  ("tc_watch_share",       "T&C per-arm watch-time share — the only truly isolated packaging signal"),
    "description_seo":        ("search_impressions_28d", "traffic-source breakdown; API-only, NULL until OAuth"),
    "upload_time":            ("views_24h_vs_pillar",  "first-24h views vs trailing pillar median; frozen first 4 weeks"),
    # posts / shorts
    "hook":                   ("impressions_7d",       "per-post; X numbers hand-entered, low trust"),
    "clip_selection":         ("short_views_7d",       "per-short, reasonably clean"),
    "caption_style":          ("short_views_7d",       "confounded with clip_selection unless held constant"),
    "pillar_mix":             ("impressions_7d",       "batch-level, weakly identified"),
    "post_time":              ("impressions_7d",       "weakly identified at 2 posts/day"),
    # replies
    "reply_voice":            ("edit_ratio",           "edit_ratio is the leading signal; op_replied 7d lags"),
    "candidate_selection":    ("op_replied_7d",        "did the OP/thread engage"),
    "subreddit_choice":       ("op_replied_7d",        "aggregate per community"),
}

LEVERS = set(OUTCOME_BASIS)

MODES = {"routine", "experiment"}
SUBJECT_TYPES = {"video", "post", "research_run", "reply"}
EXPERIMENT_METHODS = {"yt_test_compare", "sequential", "ab_manual"}
GRADES = {"strong", "ok", "off"}

# Levers that share the CTR numerator: an experiment on one may never run while
# a T&C (thumbnail) experiment is active, and vice versa (MARKETING_OS.md §7).
CTR_LEVERS = {"title_wording", "thumbnail_composition"}


def validate(*, lever, surface, mode="routine", subject_type=None):
    """Raise ValueError on any out-of-vocabulary tag. Called by the store funnel
    so a typo never silently splits a training set."""
    if lever not in LEVERS:
        raise ValueError(f"unknown lever {lever!r} — add it to fleet/levers.py first")
    if surface not in SURFACES:
        raise ValueError(f"unknown surface {surface!r} (expected one of {sorted(SURFACES)})")
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} (expected one of {sorted(MODES)})")
    if subject_type is not None and subject_type not in SUBJECT_TYPES:
        raise ValueError(f"unknown subject_type {subject_type!r} (expected one of {sorted(SUBJECT_TYPES)})")
