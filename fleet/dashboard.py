"""The Marketing OS dashboard — one local command, one live page.

    python -m fleet.dashboard            # serves http://127.0.0.1:8765 and opens it
    python -m fleet.dashboard --no-open  # just serve (e.g. it's already open)
    python -m fleet.dashboard --port 9000

What it is: a read-only window over the Supabase `marketing` schema — the same
tables the agents and the founder write (MARKETING_OS.md). The page itself is
`fleet/dashboard.html`; this module is just a ~100-line stdlib HTTP server that

  1. serves that page at `/`, and
  2. proxies `GET /api/<table>?<postgrest params>` to Supabase via the existing
     `fleet.supabase` client — so the anon key stays in `.env` (never in the
     HTML, never committed) and the browser never talks to Supabase directly
     (no CORS, no key exposure).

Read-only by construction: only GET, and only against the whitelisted tables and
views below. Writing still goes through the store modules / review CLI — the
dashboard is a window, not a door.

One deliberate exception: `/week` (mission control: This Week / Flow Graph /
Fleet views) needs to remember founder check-offs and fleet pause switches.
That state lives in local JSON files (`fleet/flow_state.json`,
`fleet/fleet_controls.json`, `fleet/video_ideas.json` — the week's drafted
video-idea options + the founder's pick — `fleet/x_batch.json` — the week's
X post batch + founder feedback, written by content/x_run.py — and
`fleet/email_broadcast.json` — the week's "Notice" newsletter draft + feedback,
drafted in session — all gitignored) written by POST /api/flow_state,
/api/fleet_controls, /api/video_ideas, /api/x_batch, and /api/email_broadcast —
never Supabase, so the window-not-a-door rule for the database still holds.

Second deliberate exception (founder-directed 2026-07-24): `/review` gets two
narrow write endpoints so the review flow needs zero terminal work —
POST /api/log_sent (write one reply_ledger row via reply_store.log_sent_reply,
capturing the draft->final diff) and POST /api/dismiss (pass on a candidate,
reason recorded). Both go through the same store functions the CLI uses; the
SEND itself stays human — the page opens X's composer, a person presses Post
(publish.py's no-auto-post wall is untouched).

Third deliberate exception (founder-directed 2026-07-24): the founder-gate
cards on `/` are clickable and act in place. POST /api/gate/* endpoints —
research_verdict, approve_packaging, mark_published, close_check, skip_check —
are each a thin wrapper over the same fleet.store / fleet.asset_store function
the CLI path uses. The physical acts stay human (uploading to YouTube, reading
the digest); the page only records what the founder already did or judged.

Fourth deliberate exception (founder-directed 2026-07-28): POST
/api/gate_capture writes ONE product_ideas row (capture SQLite) so the research
run's feature seed can enter the ideas registry straight from the /week gate
overlay — without this the agent's pick had no idea page to record a call on.
Idempotent on title; the gate answers themselves are still recorded only on
the idea's own page (/ideas/<id> -> Build gate).

Fifth deliberate exception (founder-directed 2026-07-30): POST /api/week_publish
backs the /week "Publish + log it" card — paste the YouTube URL, one click
finds-or-creates this week's marketing.videos row, records the publish via
asset_store.mark_published (arming the outcome checks), and stamps the
flow_state step. Same store functions as the / gate; the upload itself
stays human. week_publish_scan (founder-directed 2026-08-12) is the same
exception without the paste: the card asks the channel credential whether
the upload already happened and funnels a clean match into week_publish.

Surfaces (each is one static HTML file served by the router above; they are
alternatives, not a hierarchy — the founder is deliberately running several
variations to find the one that fits):
  /         dashboard.html   the table-level window over the ledger
  /kanban   kanban.html      the reply pipeline as columns
  /flow     flow.html        the weekly loop as a graph
  /week     week.html        mission control (this week / flow / fleet)
  /review   review.html      the reply queue as reviewable cards (the one door)
  /map      fleet_map.html   the fleet map: roster, wiring, dispatcher, signals
  /finders  finders.html     the Reddit finder tool trial (RSS vs Apify vs OpenCLI)

These same surfaces are ALSO mounted inside the main Flask dashboard
(app.py, port 5000) by fleet/routes.py, so everything lives at one address.
The endpoint logic is shared: the "shared actions" functions below are the
single source of truth, and both doors (this stdlib server and the Flask
blueprint) are thin wrappers over them.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fleet import asset_store, llm, reply_store, store, supabase, youtube_auth  # noqa: E402  (needs the .env loaded first)
from fleet.supabase import SupabaseError  # noqa: E402

PAGE = Path(__file__).resolve().parent / "dashboard.html"
KANBAN = Path(__file__).resolve().parent / "kanban.html"
FLOW = Path(__file__).resolve().parent / "flow.html"
WEEK = Path(__file__).resolve().parent / "week.html"
REVIEW = Path(__file__).resolve().parent / "review.html"
MAP = Path(__file__).resolve().parent / "fleet_map.html"
FINDERS = Path(__file__).resolve().parent / "finders.html"
PULSE = Path(__file__).resolve().parent / "pulse.html"
CONVERT = Path(__file__).resolve().parent / "convert.html"

# Vendored chart library for the /week Org Chart view — served locally so the
# fleet pages stay self-contained (no CDN, works offline). Loaded lazily by
# week.html only when that tab first opens.
MERMAID = Path(__file__).resolve().parent / "vendor" / "mermaid.min.js"

# Founder check-off state for /week: { "<monday>": { "<step key>": "<done iso>" } }.
FLOW_STATE = Path(__file__).resolve().parent / "flow_state.json"
WEEK_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# Two segments for normal steps ("news/script"), an optional third for
# per-occurrence sub-keys ("decide/apify/run1" — the runDots pips). The pips
# were silently 400-ing for weeks because this only allowed one slash.
KEY_RE = re.compile(r"^[a-z0-9_]+/[a-z0-9_]+(/[a-z0-9_]+)?$")

# Fleet pause switches: { "paused": { "<agent>"|"_fleet": "<paused-since iso>" } }.
# The Railway dispatcher reads this at deploy time (until then it's a standing
# instruction, honestly labeled as such on the /week Fleet view).
FLEET_CONTROLS = Path(__file__).resolve().parent / "fleet_controls.json"
AGENT_RE = re.compile(r"^(_fleet|[a-z][a-z0-9_]{0,31})$")

# Weekly video-idea options for the /week pick-the-story cards:
# { "<monday>": { "<video>": { "ideas": [...], "starred": id, "why_star": str,
#                              "picked": id|null, "picked_at": iso } } }.
# Ideas are drafted in session (from the full digest) and written here; the
# founder picks on the dashboard. Local JSON like flow_state — founder state,
# never Supabase. When idea-gen joins the Sunday run this moves to the ledger.
VIDEO_IDEAS = Path(__file__).resolve().parent / "video_ideas.json"
VIDEO_RE = re.compile(r"^(news|feature|ascent)$")
IDEA_ID_RE = re.compile(r"^[a-z0-9_-]{1,40}$")

# The week's X post batch for the /week distribute card:
# { "<monday>": { "posts": [...], "written_at": iso, "batch_file": str,
#                 "feedback": [{at, text}, ...] } }.
# Posts are written by content/x_run.py; the founder reads them on the dashboard
# and leaves feedback here. Local JSON like flow_state — never Supabase.
X_BATCH = Path(__file__).resolve().parent / "x_batch.json"

# The week's "Notice" email broadcast draft (WEEKLY_RUNBOOK.md Loop 3) for the
# /week distribute card: { "<monday>": { "subject": str, "preview_text": str,
# "pattern_md": str, "around_the_web": [...], "cta": str, "written_at": iso,
# "status": str, "feedback": [{at, text}, ...] } }.
# Drafted in session each week (manual until the list justifies an agent + ESP);
# the founder reads it here and leaves feedback. Send stays by hand.
EMAIL_BROADCAST = Path(__file__).resolve().parent / "email_broadcast.json"

# TikTok/Instagram shorts scheduling state for the /week distribute card.
# Publer's free plan has no API, so this file is the record: whoever schedules
# a batch (founder or agent) writes the counts here at scheduling time. The
# YouTube side needs no file — the coverage endpoint reads it LIVE from the
# channel credential. Local JSON like flow_state — never Supabase.
SHORTS_SCHEDULE = Path(__file__).resolve().parent / "shorts_schedule.json"

# Visual-aid decks (videos/aids/*.html) served at /aids/<name>.html so the /week
# deck card can embed them without leaving the dashboard. Read-only, name-
# whitelisted (no traversal), same-origin — the deck's own JS keyboard nav works.
AIDS_DIR = ROOT / "videos" / "aids"
AID_RE = re.compile(r"^/aids/([a-z0-9_-]+\.html)$")

# Every table/view the page may read. Anything else 404s — the dashboard can't
# become an accidental generic proxy.
ALLOWED = {
    "research_runs", "videos", "decisions", "experiments", "outcome_checks",
    "reply_candidates", "reply_drafts", "reply_ledger", "content_calendar",
    "agent_execution_logs",
    # attribution (the /convert tab reads arrivals from these)
    "short_links", "link_clicks",
    # views
    "agent_heartbeat", "video_lineage", "decision_calibration",
}


# ── shared actions ──────────────────────────────────────────────────────────
# The request-independent core of every endpoint, used by BOTH doors: the
# stdlib Handler below (python -m fleet.dashboard, port 8765) and the Flask
# blueprint in fleet/routes.py that mounts the same surfaces inside the main
# app.py dashboard (port 5000). All logic lives here so the two doors can't
# drift. Functions raise ValueError/KeyError on bad input (-> HTTP 400) and
# let SupabaseError bubble (-> HTTP 502).

# The five founder-state JSON files, by API name: GET /api/<name> returns the
# whole file; the POST actions below validate and append/toggle.
STATE_FILES = {
    "flow_state": FLOW_STATE,
    "fleet_controls": FLEET_CONTROLS,
    "video_ideas": VIDEO_IDEAS,
    "x_batch": X_BATCH,
    "email_broadcast": EMAIL_BROADCAST,
}


def read_state(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def write_state(path, data):
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)
    _mirror_state(path, data)


def _mirror_state(path, data):
    """Off-PC drain v1: every local save of the two workflow states mirrors
    up to marketing.fleet_state so the cloud drain job can act with the PC
    off. Best-effort by design - fleet_state.push never raises."""
    name = {"flow_state.json": "flow_state",
            "video_ideas.json": "video_ideas"}.get(path.name)
    if name:
        from fleet import fleet_state
        fleet_state.push(name, data)


def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def post_flow_state(body):
    week, key, done = body["week"], body["key"], bool(body["done"])
    if not (WEEK_RE.match(week) and KEY_RE.match(key)):
        raise ValueError("bad week/key")
    state = read_state(FLOW_STATE)
    steps = state.setdefault(week, {})
    if done:
        steps[key] = _now()
    else:
        steps.pop(key, None)
    write_state(FLOW_STATE, state)
    if done and key == "research/qa":
        spawn_x_batch(week)
        spawn_email_draft(week)
        # Wire 10 (founder-directed 2026-08-24): the news story options draft
        # themselves too - same go-signal, since they need only the digest
        # (plus the Q&A answers this click just finished, the topic-pick signal).
        spawn_news_ideas(week)
    if done and key == "dist/xbatch":
        spawn_x_schedule(week)
    # Wire 6 (founder-directed 2026-08-17): the email done-click IS the
    # approval, same as the X batch — the send schedules itself for Thu 9am MT.
    if done and key == "dist/email":
        spawn_email_schedule(week)
    video, _, step = key.partition("/")
    # Wire 5: the video landing on YouTube unblocks its long-form X post —
    # whichever lands second (approve click or publish stamp) fires the
    # scheduler, so the ordering never matters.
    if done and step == "publish" and video in ("news", "feature", "ascent"):
        spawn_xpost_schedule(week, video)
    # Wire 8 (founder-approved build 2026-08-22): the script drafts itself the
    # moment its inputs land — news off the verified facts, feature off the
    # approved worked example. The founder's pass on the script card stays the
    # gate for everything downstream.
    if done and key == "news/facts":
        spawn_script_run(week, "news")
    if done and key == "feature/example":
        spawn_script_run(week, "feature")
    # Wire 9 (the deliberate last agent, built 2026-08-22): picking the topic
    # fires the facts agent — Exa searches + a sources-locked verify report,
    # landing on the facts card as a draft. The founder's pass on THAT card
    # is what fires wire 8, so a bad report can never script itself.
    if done and key == "news/topic":
        spawn_facts_run(week)
    # decide/build done IS "the feature is live" — one event, two cards
    # (founder call 2026-08-11: the feature workflow's first card was asking
    # him to re-confirm a thing the board already knew). Stamp both.
    if done and key == "decide/build" and "feature/live" not in steps:
        steps["feature/live"] = _now()
        write_state(FLOW_STATE, state)
    # Wire 12 (founder-reported gap 2026-08-24): the build done-click also
    # wakes the worked-example agent - the tool is live, so the demo run can
    # propose itself. The founder's pass on the example card stays the gate
    # that fires the script agent (wire 8).
    if done and key == "decide/build":
        spawn_example_run(week)
    # The founder's script pass is the gate: news/feature approve via the
    # script card itself; ascent has a separate script_ok card because the
    # outline card is the agent's own step (fixed 2026-08-13 — firing on
    # ascent/script would have generated artifacts from an unapproved draft).
    if done and (step == "script" and video in ("news", "feature")
                 or key == "ascent/script_ok"):
        spawn_video_artifacts(week, video)
    return steps


# ── mark-done triggers (the drain pattern, v0) ──────────────────────────────
# The founder's done-click IS an agent's go signal: marking a step done on
# /week wakes whatever agent that step unblocks, so nothing waits on someone
# remembering to ask in session. Wire 1: "research/qa" done -> the X weekly
# batch drafts itself (content/x_run.py — digest-only, no video pick needed).
# The same click also drafts the week's "Notice" email (content/email_run.py
# — digest-only too, so both quick wins land together). Wire 2: "dist/xbatch"
# done -> the approved batch schedules itself into Typefully
# (content/x_schedule.py — the done-click IS the approval, so this is the one
# wire that publishes outward; un-marking done never unschedules). Wire 6:
# "dist/email" done -> the issue self-approves and schedules for Thu 9am MT
# (fleet/email_send.py --schedule; founder-directed 2026-08-17). Wire 5:
# a long-form xpost approval (or its video's publish stamp, whichever lands
# second) -> the post schedules itself for the video's go-live day and the
# {video}/xpost step stamps itself done (content/xpost_schedule.py). Future
# artifact agents (deck, facts, package...) follow this same shape: the click
# writes state here, the unblocked agent runs on it.
X_RUN_LOG = Path(__file__).resolve().parent / "logs" / "x_run_auto.log"
NEWS_IDEAS_LOG = Path(__file__).resolve().parent / "logs" / "news_ideas_auto.log"
EXAMPLE_RUN_LOG = Path(__file__).resolve().parent / "logs" / "example_run_auto.log"
EMAIL_SCHEDULE_LOG = Path(__file__).resolve().parent / "logs" / "email_schedule_auto.log"
X_SCHEDULE_LOG = Path(__file__).resolve().parent / "logs" / "x_schedule_auto.log"
EMAIL_RUN_LOG = Path(__file__).resolve().parent / "logs" / "email_run_auto.log"
VIDEO_ARTIFACTS_LOG = Path(__file__).resolve().parent / "logs" / "video_artifacts_auto.log"
XPOST_SCHEDULE_LOG = Path(__file__).resolve().parent / "logs" / "xpost_schedule_auto.log"


def _spawn_module(module, log_path, args=()):
    """Fire-and-forget `python -m <module> [args...]` from the repo root,
    output to a log the /week overlays can point at when something looks stuck."""
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("ab") as log:
        subprocess.Popen([sys.executable, "-m", module, *args],
                         cwd=str(ROOT), stdout=log, stderr=subprocess.STDOUT)


def _marker_fresh(stamp, minutes=15):
    """True if an in-flight marker is recent enough to still be trusted;
    anything older counts as a dead run and gets retried."""
    if not stamp:
        return False
    try:
        return datetime.now().astimezone() - datetime.fromisoformat(stamp) < timedelta(minutes=minutes)
    except ValueError:
        return False


def spawn_x_batch(week):
    """Draft the week's X batch in the background, once. Skips if the slot
    already has posts (regenerating after feedback stays a deliberate,
    in-session act) or if a draft is already in flight. On success, x_run's
    own slot write replaces the marker with the posts."""
    state = read_state(X_BATCH)
    slot = state.get(week) or {}
    if slot.get("posts") or _marker_fresh(slot.get("generating_since")):
        return
    slot["generating_since"] = _now()
    state[week] = slot
    write_state(X_BATCH, state)
    _spawn_module("content.x_run", X_RUN_LOG)


def spawn_example_run(week):
    """Propose the feature video's worked example in the background, once
    (wire 12, founder-reported gap 2026-08-24). Skips if the card already has
    a run (regenerating after feedback stays a deliberate, in-session act) or
    a draft is in flight. example_run's own slot write replaces the marker."""
    state = read_state(VIDEO_IDEAS)
    slot = (state.get(week) or {}).get("feature") or {}
    if ((slot.get("example") or {}).get("example_md")
            or _marker_fresh(slot.get("example_generating_since"))):
        return
    slot["example_generating_since"] = _now()
    state.setdefault(week, {})["feature"] = slot
    write_state(VIDEO_IDEAS, state)
    _spawn_module("content.example_run", EXAMPLE_RUN_LOG, args=("--week", week))


def spawn_news_ideas(week):
    """Draft the news video's three story options in the background, once
    (wire 10, founder-directed 2026-08-24 - the last un-agented step at the
    top of the news lane). Skips if the slot already has options (regenerating
    after feedback stays a deliberate, in-session act) or a draft is in
    flight. news_ideas_run's own slot write replaces the marker."""
    state = read_state(VIDEO_IDEAS)
    slot = (state.get(week) or {}).get("news") or {}
    if slot.get("ideas") or _marker_fresh(slot.get("ideas_generating_since")):
        return
    slot["ideas_generating_since"] = _now()
    state.setdefault(week, {})["news"] = slot
    write_state(VIDEO_IDEAS, state)
    _spawn_module("content.news_ideas_run", NEWS_IDEAS_LOG)


def spawn_email_draft(week):
    """Draft the week's Notice email in the background, once. Skips if the
    slot already has an issue (revising after feedback stays a deliberate,
    in-session act) or a draft is in flight. email_run's own slot write
    replaces the marker with the issue."""
    state = read_state(EMAIL_BROADCAST)
    slot = state.get(week) or {}
    if slot.get("pattern_md") or _marker_fresh(slot.get("generating_since")):
        return
    slot["generating_since"] = _now()
    state[week] = slot
    write_state(EMAIL_BROADCAST, state)
    _spawn_module("content.email_run", EMAIL_RUN_LOG)


def spawn_email_schedule(week):
    """Schedule the approved Notice email for Thu 9am MT in the background,
    once (wire 6, founder-directed 2026-08-17 — the ritual used to be Claude
    running --schedule in session after the done-click). The done-click IS the
    approval, so this stamps status "approved" before spawning; email_send's
    own approved-gate still protects every other path to --schedule. Skips if
    there's no drafted issue, it already carries a broadcast_id (scheduled or
    sent), or a run is in flight."""
    state = read_state(EMAIL_BROADCAST)
    slot = state.get(week) or {}
    if not slot.get("pattern_md") or slot.get("broadcast_id"):
        return
    if _marker_fresh(slot.get("send_scheduling_since")):
        return
    if slot.get("status") != "approved":
        slot["status"] = "approved"
        slot["approved_at"] = _now()
    slot["send_scheduling_since"] = _now()
    state[week] = slot
    write_state(EMAIL_BROADCAST, state)
    _spawn_module("fleet.email_send", EMAIL_SCHEDULE_LOG, args=("--schedule",))


def spawn_video_artifacts(week, video):
    """The script-done fan-out (founder call 2026-08-11): approving a video's
    script generates EVERYTHING that depends only on script + facts — deck,
    thumbnail prompts, package, and the long-form X post draft — in one batch.
    One subprocess, parts sequential inside (they share one state file); the
    module itself skips parts already on the card, so this is safe to re-fire."""
    state = read_state(VIDEO_IDEAS)
    slot = (state.get(week) or {}).get(video) or {}
    if not (slot.get("script") or {}).get("script_md"):
        return
    have = ((slot.get("deck") or {}).get("file"),
            (slot.get("thumbs") or {}).get("prompts"),
            (slot.get("pkg") or {}).get("description"))
    if all(have):
        return
    if _marker_fresh(slot.get("artifacts_generating_since")):
        return
    slot["artifacts_generating_since"] = _now()
    state.setdefault(week, {})[video] = slot
    write_state(VIDEO_IDEAS, state)
    _spawn_module("content.video_artifacts_run", VIDEO_ARTIFACTS_LOG,
                  args=("--video", video, "--week", week))


def spawn_x_schedule(week):
    """Schedule the approved batch into Typefully in the background, once.
    Skips if there's nothing to do (no posts, all already carrying a
    typefully_draft_id) or a run is in flight. x_schedule itself re-checks
    per post, refuses >280, and verifies every draft by reading it back."""
    state = read_state(X_BATCH)
    slot = state.get(week) or {}
    posts = slot.get("posts") or []
    if not posts or all(p.get("typefully_draft_id") for p in posts):
        return
    if _marker_fresh(slot.get("scheduling_since")):
        return
    slot["scheduling_since"] = _now()
    write_state(X_BATCH, state)
    _spawn_module("content.x_schedule", X_SCHEDULE_LOG, args=("--week", week))


def post_x_schedule_run(body):
    """The xbatch overlay's "schedule the rest" button: re-fire the Typefully
    scheduler for a week whose batch scheduled only partially (posts over 280
    are refused, never truncated — after a trim, this picks up the stragglers).
    Safe to spam: spawn_x_schedule skips when nothing is owed or a run is
    already in flight, and x_schedule itself skips posts already carrying a
    typefully_draft_id."""
    week = body["week"]
    if not WEEK_RE.match(week):
        raise ValueError("bad week")
    posts = (read_state(X_BATCH).get(week) or {}).get("posts") or []
    owed = sum(1 for p in posts if not p.get("typefully_draft_id"))
    spawn_x_schedule(week)
    return {"owed": owed}


FACTS_RUN_LOG = Path(__file__).resolve().parent / "logs" / "facts_run_auto.log"


def spawn_facts_run(week):
    """Verify the picked news story in the background, once (wire 9). Skips
    if the facts card already carries a report (regenerating after feedback
    stays in-session) or a run is in flight. content/facts_run.py writes the
    report as status 'draft'; the founder's pass on the facts card fires the
    script agent."""
    state = read_state(VIDEO_IDEAS)
    slot = (state.get(week) or {}).get("news")
    if not slot or not slot.get("picked"):
        return
    if (slot.get("facts") or {}).get("report_md"):
        return
    if _marker_fresh(slot.get("facts_generating_since")):
        return
    slot["facts_generating_since"] = _now()
    write_state(VIDEO_IDEAS, state)
    _spawn_module("content.facts_run", FACTS_RUN_LOG, args=("--week", week))


SCRIPT_RUN_LOG = Path(__file__).resolve().parent / "logs" / "script_run_auto.log"


def spawn_script_run(week, video):
    """Draft the lane's video script in the background, once (wire 8). Skips
    if the slot already has a script (regenerating after feedback stays a
    deliberate in-session act) or a draft is in flight. content/script_run.py
    writes the draft onto the script card as status 'draft' — the founder's
    pass stays the gate."""
    state = read_state(VIDEO_IDEAS)
    slot = (state.get(week) or {}).get(video)
    if not slot:
        return
    if (slot.get("script") or {}).get("script_md"):
        return
    if _marker_fresh(slot.get("script_generating_since")):
        return
    slot["script_generating_since"] = _now()
    write_state(VIDEO_IDEAS, state)
    _spawn_module("content.script_run", SCRIPT_RUN_LOG,
                  args=("--week", week, "--video", video))


def spawn_xpost_schedule(week, video):
    """Schedule the video's approved long-form X post in the background, once
    (wire 5, founder call 2026-08-13 — the approve click used to wait for a
    session to notice it). Skips if there's nothing owed (not approved yet, or
    already carrying a typefully_draft_id) or a run is in flight. The module
    itself resolves the video's go-live from marketing.videos, notices a
    founder-posted-by-hand copy instead of double-posting, verifies the draft
    by reading it back, and stamps the {video}/xpost flow step done."""
    state = read_state(VIDEO_IDEAS)
    xp = ((state.get(week) or {}).get(video) or {}).get("xpost") or {}
    if xp.get("status") != "approved" or xp.get("typefully_draft_id"):
        return
    if _marker_fresh(xp.get("scheduling_since")):
        return
    xp["scheduling_since"] = _now()
    write_state(VIDEO_IDEAS, state)
    _spawn_module("content.xpost_schedule", XPOST_SCHEDULE_LOG,
                  args=("--week", week, "--video", video))


# ── the off-page drain runner (founder-directed 2026-08-22) ─────────────────
# The publish auto-notice and wire 5 used to fire only on a page open or a
# card click, so a video scheduled in Studio with /week closed sat unlogged
# until someone looked (hit twice the week of 08-17). While the server runs,
# this daemon thread sweeps every DRAIN_INTERVAL_S: any lane with a recorded
# video but no publish stamp gets the YouTube scan, and any approved
# long-form xpost still owed a schedule gets its spawner poked (both are
# self-guarded and idempotent). The full off-PC version belongs to the
# Railway dispatcher once click-state moves to Supabase.
DRAIN_RUNNER_LOG = Path(__file__).resolve().parent / "logs" / "drain_runner.log"
VIDEO_OUTCOMES_LOG = Path(__file__).resolve().parent / "logs" / "video_outcomes.log"
VIDEO_OUTCOMES_STAMP = Path(__file__).resolve().parent / "logs" / "video_outcomes.stamp"
DRAIN_INTERVAL_S = 600
VIDEO_LANES = ("news", "feature", "ascent")


def _apply_cloud_results():
    """Merge the cloud drain's outbox into the local truth (off-PC drain v1):
    xpost deltas first (so a publish stamp's wire-5 spawn can never
    double-schedule what the cloud already scheduled), then publish stamps.
    Applied deltas are cleared from the outbox; local writes re-mirror."""
    from fleet import fleet_state
    results = fleet_state.pull("cloud_results") or {}
    if not results:
        return []
    actions, applied = [], []
    ordered = sorted(results.items(),
                     key=lambda kv: kv[1].get("kind") != "xpost")
    for key, d in ordered:
        week, video = d.get("week"), d.get("video")
        try:
            if d.get("kind") == "xpost":
                state = read_state(VIDEO_IDEAS)
                slot = (state.get(week) or {}).get(video) or {}
                xp = slot.get("xpost") or {}
                if not xp.get("typefully_draft_id") and xp.get("status") != "posted":
                    for field in ("link_reply", "typefully_draft_id",
                                  "typefully_status", "scheduled_for",
                                  "posted_url"):
                        if d.get(field) is not None:
                            xp[field] = d[field]
                    if d.get("status") == "posted":
                        xp["status"] = "posted"
                    xp["scheduled_by"] = "fleet.fleet_state (cloud drain)"
                    xp.pop("scheduling_since", None)
                    slot["xpost"] = xp
                    state.setdefault(week, {})[video] = slot
                    write_state(VIDEO_IDEAS, state)
                post_flow_state({"week": week, "key": f"{video}/xpost",
                                 "done": True})
                actions.append(f"merged cloud xpost for {week} {video}")
            elif d.get("kind") == "publish":
                for step in (f"{video}/thumb", f"{video}/desc",
                             f"{video}/publish"):
                    post_flow_state({"week": week, "key": step, "done": True})
                actions.append(f"merged cloud publish for {week} {video} "
                               f"({d.get('youtube_video_id')})")
            applied.append(key)
        except Exception as e:  # noqa: BLE001 - a bad delta must not stick
            actions.append(f"cloud delta {key} FAILED to apply: {e}")
    if applied:
        for key in applied:
            results.pop(key, None)
        fleet_state.push("cloud_results", results)
    return actions


def drain_pass():
    """One sweep; returns the actions taken (empty = nothing was owed)."""
    week = str(date.today() - timedelta(days=date.today().weekday()))
    actions = []
    try:
        actions.extend(_apply_cloud_results())
    except Exception as e:  # noqa: BLE001
        actions.append(f"cloud-results merge FAILED: {e}")
    steps = read_state(FLOW_STATE).get(week, {})
    for lane in VIDEO_LANES:
        if f"{lane}/publish" in steps:
            continue
        if not any(f"{lane}/{s}" in steps for s in ("record", "edit")):
            continue  # nothing recorded — the scan would refuse anyway
        try:
            out = post_week_publish_scan({"week": week, "video": lane})
            if out.get("matched"):
                actions.append(f"publish auto-logged {lane}: "
                               f"{out.get('title') or out.get('slug')}")
        except Exception as e:  # noqa: BLE001 — the sweep must never die
            actions.append(f"publish scan {lane} FAILED: {e}")
    for lane in VIDEO_LANES:
        try:
            spawn_xpost_schedule(week, lane)
        except Exception as e:  # noqa: BLE001
            actions.append(f"xpost spawn {lane} FAILED: {e}")
    # CTA comment watcher (ported from the personal-brand workflow
    # 2026-08-23): a scheduled publish goes live with nobody at the desk;
    # this posts the pinned-comment CTA within a sweep of it appearing.
    try:
        from fleet import cta_comment
        actions.extend(cta_comment.run_pass())
    except Exception as e:  # noqa: BLE001
        actions.append(f"cta pass FAILED: {e}")
    # Video outcomes daily leg: dispatch owns it in the cloud, but the
    # YouTube credential lives on this PC, so the drain runner fires it
    # once a day here (stamp-gated; the agent itself is idempotent).
    try:
        stamp = VIDEO_OUTCOMES_STAMP
        stale = (not stamp.exists()
                 or time.time() - stamp.stat().st_mtime > 24 * 3600)
        if stale:
            stamp.parent.mkdir(exist_ok=True)
            stamp.touch()
            _spawn_module("fleet.video_outcomes", VIDEO_OUTCOMES_LOG)
            actions.append("video_outcomes daily pass spawned")
    except Exception as e:  # noqa: BLE001
        actions.append(f"video_outcomes spawn FAILED: {e}")
    return actions


def _drain_runner():
    while True:
        try:
            actions = drain_pass()
            if actions:
                DRAIN_RUNNER_LOG.parent.mkdir(exist_ok=True)
                with DRAIN_RUNNER_LOG.open("a", encoding="utf-8") as log:
                    for a in actions:
                        log.write(f"{_now()} {a}\n")
        except Exception:  # noqa: BLE001
            pass
        time.sleep(DRAIN_INTERVAL_S)


def start_drain_runner():
    threading.Thread(target=_drain_runner, daemon=True,
                     name="drain-runner").start()


def post_fleet_controls(body):
    agent, paused = body["agent"], bool(body["paused"])
    if not AGENT_RE.match(agent):
        raise ValueError("bad agent name")
    state = read_state(FLEET_CONTROLS)
    switches = state.setdefault("paused", {})
    if paused:
        switches[agent] = _now()
    else:
        switches.pop(agent, None)
    write_state(FLEET_CONTROLS, state)
    return state


# Production repo (the faceless chain) — narration + rendered assets live there.
# Default: the sibling "Fiboprana Marketing" repo; override with PRODUCTION_REPO.
PRODUCTION_ASSETS = Path(os.environ.get(
    "PRODUCTION_REPO",
    str(Path(__file__).resolve().parent.parent.parent / "Fiboprana Marketing"),
)) / "content" / "videos" / "assets"


def _production_dir(week, video):
    """The production assets dir for this week's lane. The slug is derived
    from the script card's file name (videos/scripts/<slug>.md), so these
    surfaces light up the moment a script lands."""
    if not (WEEK_RE.match(week) and VIDEO_RE.match(video)):
        raise ValueError("bad week/video")
    slot = read_state(VIDEO_IDEAS).get(week, {}).get(video) or {}
    file = (slot.get("script") or {}).get("file") or ""
    slug = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", Path(file).stem)
    if not slug:
        raise LookupError("no script on the card yet, so no production slug to look under")
    return slug, PRODUCTION_ASSETS / slug


def narration_audio(week, video):
    """(filename, mp3 bytes) of the LATEST narration render for this week's
    lane (founder ask 2026-08-28: listen from the card, not the filesystem)."""
    slug, d = _production_dir(week, video)
    versions = list(d.glob("narration-v*.mp3")) if d.is_dir() else []
    if not versions:
        raise LookupError(f"no narration rendered yet for '{slug}'")
    versions.sort(key=lambda p: int(re.search(r"v(\d+)$", p.stem).group(1)))
    latest = versions[-1]
    return latest.name, latest.read_bytes()


_STILL_FILE_RE = re.compile(r"^beat-[A-Za-z0-9_-]+\.png$")
_RENDER_FILE_RE = re.compile(r"^(master-v\d+|short-s\d+)\.mp4$")


def renders_listing(week, video):
    """The rendered master(s) + shorts for this week's lane, newest master
    first — feeds the card's watch overlay (founder ask 2026-08-28)."""
    slug, d = _production_dir(week, video)
    masters = sorted((p.name for p in d.glob("master-v*.mp4")),
                     key=lambda n: int(re.search(r"v(\d+)", n).group(1)),
                     reverse=True)
    shorts = sorted(p.name for p in d.glob("short-s*.mp4"))
    return {"slug": slug, "masters": masters, "shorts": shorts}


def render_video_path(week, video, filename):
    """Absolute path of one rendered mp4, filename whitelisted (no traversal).
    Returned as a path (not bytes): renders are hundreds of MB, so both doors
    stream them with Range support instead of loading into memory."""
    if not _RENDER_FILE_RE.match(filename or ""):
        raise ValueError("bad render filename")
    _slug, d = _production_dir(week, video)
    f = d / filename
    if not f.is_file():
        raise LookupError(f"no such render: {filename}")
    return f


def stills_listing(week, video):
    """The generated beat stills for this week's lane, for the card's review
    grid (founder ask 2026-08-28: approve the images from the card). Also
    reports which clips exist so the same overlay works after animate."""
    slug, d = _production_dir(week, video)
    stills_dir = d / "stills"
    clips_dir = d / "clips"
    stills = sorted(p.name for p in stills_dir.glob("beat-*.png")) if stills_dir.is_dir() else []
    clips = sorted(p.name for p in clips_dir.glob("beat-*.mp4")) if clips_dir.is_dir() else []
    return {"slug": slug, "stills": stills, "clips": clips}


def still_image(week, video, filename):
    """One still's PNG bytes, filename whitelisted (no traversal)."""
    if not _STILL_FILE_RE.match(filename or ""):
        raise ValueError("bad still filename")
    _slug, d = _production_dir(week, video)
    f = d / "stills" / filename
    if not f.is_file():
        raise LookupError(f"no such still: {filename}")
    return f.read_bytes()


def post_video_ideas(body):
    """Two founder writes on the week's video state: pick an idea, or leave
    script feedback. The ideas/facts/script themselves are drafted in session
    and written to the file directly."""
    week, video = body["week"], body["video"]
    if not (WEEK_RE.match(week) and VIDEO_RE.match(video)):
        raise ValueError("bad week/video")
    state = read_state(VIDEO_IDEAS)
    slot = state.get(week, {}).get(video)
    if not slot:
        raise ValueError("nothing drafted for that week/video")

    if "picked" in body:
        picked = body["picked"]
        if not (IDEA_ID_RE.match(picked)
                and any(i.get("id") == picked for i in slot.get("ideas", []))):
            raise ValueError("no such idea drafted for that week/video")
        slot["picked"] = picked
        slot["picked_at"] = _now()
    elif "passed" in body:
        # Founder passes on a drafted idea (founder call 2026-08-28): unpicked,
        # un-passed ideas CARRY OVER into next week's slate; a pass drops one
        # from the carry-over. body.undo brings a passed idea back.
        target = body["passed"]
        if not (IDEA_ID_RE.match(target)
                and any(i.get("id") == target for i in slot.get("ideas", []))):
            raise ValueError("no such idea drafted for that week/video")
        passed = set(slot.get("passed") or [])
        if body.get("undo"):
            passed.discard(target)
        else:
            passed.add(target)
        slot["passed"] = sorted(passed)
    elif "thumb_pick" in body:
        # Which thumbnail prompt won the render round — recorded at decision
        # time (founder call 2026-08-11). Studio only ever sees the ONE
        # uploaded thumbnail, so the candidates' win/lose record exists only
        # here; it is what the style ledger learns from.
        pick = body["thumb_pick"]
        thumbs = slot.get("thumbs") or {}
        if not (IDEA_ID_RE.match(pick)
                and any(p.get("id") == pick for p in thumbs.get("prompts", []))):
            raise ValueError("no such thumbnail prompt for that week/video")
        thumbs["picked"] = pick
        thumbs["picked_at"] = _now()
        note = (body.get("note") or "").strip()
        if note:
            thumbs["picked_note"] = note[:1000]
    elif "feedback" in body:
        text = (body["feedback"] or "").strip()
        if not (text and len(text) <= 4000):
            raise ValueError("feedback must be 1-4000 chars")
        target = body.get("target", "script")
        if target not in ("script", "deck", "pkg", "example", "xpost"):
            raise ValueError("target must be script | deck | pkg | example | xpost")
        if target not in slot:
            raise ValueError(f"no {target} drafted yet")
        slot[target].setdefault("feedback", []).append({"at": _now(), "text": text})
    elif "approve" in body:
        # The founder's "ready to schedule" click on the long-form X post —
        # the gate the scheduling agent acts behind.
        #
        # CONTRACT (founder-directed 2026-08-07; automated 2026-08-13 as
        # content/xpost_schedule.py — this click spawns it, and so does the
        # video's publish stamp, whichever lands second): find any video_ideas
        # slot where xpost.status == "approved" and "typefully_draft_id" is
        # absent, then
        #   1. schedule ONE Typefully X draft: xpost.text + the link as a
        #      self-reply thread, timed to the video's go-live day at 23:00Z
        #      (5 PM MT slot); go-live comes from the video row's published_at.
        #   2. write back typefully_draft_id / typefully_url / scheduled_for /
        #      scheduled_at / scheduled_by onto the xpost slot (idempotency
        #      key: a slot with typefully_draft_id is DONE, never reschedule),
        #      and mark the week's {video}/xpost flow step done.
        # Never schedule while status is "draft" — the click is the whole gate.
        if body["approve"] != "xpost":
            raise ValueError("only xpost approval is supported")
        if "xpost" not in slot:
            raise ValueError("no xpost drafted yet")
        # Approval is also the tracking stamp: the link reply leaves here as
        # a fiboprana.com/r/<code> short link (campaign = the video), so
        # the scheduling agent threads an already-tracked URL. Idempotent
        # (re-approving reuses the same link) and fail-open (a Supabase
        # hiccup approves with the bare URL rather than blocking the click).
        if slot["xpost"].get("link_reply"):
            from attribution import autolink
            slot["xpost"]["link_reply"] = autolink.rewrite_urls(
                slot["xpost"]["link_reply"], source="x", medium="post",
                campaign=f"video-{video}", content="longform-xpost",
                post_text=slot["xpost"].get("text"))
        slot["xpost"]["status"] = "approved"
        slot["xpost"]["approved_at"] = _now()
    else:
        raise ValueError("need picked, feedback, or approve")

    write_state(VIDEO_IDEAS, state)
    if body.get("approve") == "xpost":
        spawn_xpost_schedule(week, video)
        slot = read_state(VIDEO_IDEAS).get(week, {}).get(video, slot)
    # The reply omits the big blocks; the page re-fetches when it needs them.
    return {k: v for k, v in slot.items() if k not in ("script", "deck", "facts", "pkg")}


def post_x_batch(body):
    """Two founder writes on the week's X batch. With post_index + text: edit
    that post in place — the agent's draft is kept on the post as
    original_text, so the draft→final diff is a voice-learning example, same
    as the reply ledger. Otherwise: append a batch-level feedback note (kill
    a post, pillar mix, day swaps); the agent reads those in session and
    regenerates. The posts themselves are written by content/x_run.py."""
    week = body["week"]
    if not WEEK_RE.match(week):
        raise ValueError("bad week")
    state = read_state(X_BATCH)
    slot = state.get(week)
    if not slot:
        raise ValueError("no batch generated for that week yet")
    if "post_index" in body:
        posts = slot.get("posts") or []
        idx = body["post_index"]
        if not (isinstance(idx, int) and 0 <= idx < len(posts)):
            raise ValueError("bad post_index")
        text = (body.get("text") or "").strip()
        if not (text and len(text) <= 2000):
            raise ValueError("text must be 1-2000 chars")
        post = posts[idx]
        if text != post["text"]:
            post.setdefault("original_text", post["text"])
            post["text"] = text
            post["edited_at"] = _now()
            write_state(X_BATCH, state)
        return post
    text = (body.get("feedback") or "").strip()
    if not (text and len(text) <= 4000):
        raise ValueError("feedback must be 1-4000 chars")
    slot.setdefault("feedback", []).append({"at": _now(), "text": text})
    write_state(X_BATCH, state)
    return {k: v for k, v in slot.items() if k != "posts"}


EMAIL_EDITABLE = ("subject", "preview_text", "pattern_md", "cta")


def post_email_broadcast(body):
    """Two founder writes on the week's email draft. With field + text: edit
    that piece in place — the agent's draft is kept under originals[field],
    same draft→final learning shape as the X batch. Otherwise: append a
    feedback note; the agent reads those and revises."""
    week = body["week"]
    if not WEEK_RE.match(week):
        raise ValueError("bad week")
    state = read_state(EMAIL_BROADCAST)
    slot = state.get(week)
    if not slot:
        raise ValueError("no email drafted for that week yet")
    if "field" in body:
        field = body["field"]
        if field not in EMAIL_EDITABLE:
            raise ValueError("bad field")
        text = (body.get("text") or "").strip()
        if not (text and len(text) <= 8000):
            raise ValueError("text must be 1-8000 chars")
        if text != (slot.get(field) or ""):
            slot.setdefault("originals", {}).setdefault(field, slot.get(field) or "")
            slot[field] = text
            slot["edited_at"] = _now()
            write_state(EMAIL_BROADCAST, state)
        return {"field": field, "text": slot[field]}
    text = (body.get("feedback") or "").strip()
    if not (text and len(text) <= 4000):
        raise ValueError("feedback must be 1-4000 chars")
    slot.setdefault("feedback", []).append({"at": _now(), "text": text})
    write_state(EMAIL_BROADCAST, state)
    return {k: v for k, v in slot.items() if k not in ("pattern_md", "around_the_web")}


def post_log_sent(body):
    """One reply_ledger row from the /review page — same store call as the CLI.
    The diff vs the chosen draft is computed inside log_sent_reply; edit_type
    is auto-labeled here (none = sent verbatim, voice = founder edited) and
    can be refined later in session."""
    final = (body["final_text"] or "").strip()
    if not final:
        raise ValueError("final_text is empty")
    candidate_id = body["candidate_id"]
    draft_id = body.get("draft_id") or None
    draft = reply_store.drafts_for_candidate(candidate_id) if draft_id else []
    draft_text = next((d.get("draft_text") for d in draft if d.get("id") == draft_id), None)
    edit_type = "none" if (draft_text or "").strip() == final else "voice"
    row = reply_store.log_sent_reply(
        final_sent=final,
        candidate_id=candidate_id,
        draft_id=draft_id,
        platform=body.get("platform"),
        edit_type=edit_type,
        intent_preserved=True if edit_type == "voice" else None,
        style_notes="logged via /review",
    )
    return {"ledger_id": row.get("id"), "edit_type": edit_type,
            "edit_ratio": row.get("edit_ratio")}


def post_dismiss(body):
    reply_store.dismiss_candidate(body["candidate_id"],
                                  reason=body.get("reason") or "founder pass via /review")
    return {"ok": True}


# ── founder-gate actions (third exception; see module docstring) ────────────

def gate_research_verdict(body):
    """Grade a research run strong/ok/off — store.update_outcome, the same
    call the Sunday-close CLI makes."""
    verdict = body["verdict"]
    if verdict not in ("strong", "ok", "off"):
        raise ValueError("verdict must be strong | ok | off")
    rows = store.update_outcome(body["run_id"], verdict=verdict,
                                note=(body.get("note") or "").strip() or None)
    if not rows:
        raise ValueError("run not found / nothing updated")
    return {"ok": True}


def gate_approve_packaging(body):
    """packaged_draft -> packaging_approved. Status-guarded so a stale page
    can't approve something that already moved on."""
    video = asset_store.get_video(body["video_id"])
    if not video:
        raise ValueError("video not found")
    if video.get("status") != "packaged_draft":
        raise ValueError(f"video is '{video.get('status')}', not packaged_draft — refresh")
    asset_store.update_video(body["video_id"], status="packaging_approved")
    return {"ok": True}


def gate_mark_published(body):
    """Record the publish facts after the hand upload. mark_published also
    opens the 24h/7d/28d outcome checks — this is what arms the clock."""
    yt_id = (body["youtube_video_id"] or "").strip()
    if not yt_id:
        raise ValueError("youtube_video_id is empty")
    video = asset_store.get_video(body["video_id"])
    if not video:
        raise ValueError("video not found")
    if video.get("status") not in ("packaging_approved", "packaged_draft"):
        raise ValueError(f"video is '{video.get('status')}' — refresh")
    out = asset_store.mark_published(body["video_id"], youtube_video_id=yt_id)
    return {"ok": True, "checks_opened": out.get("checks_opened")}


YT_ID_RE = re.compile(r"(?:v=|youtu\.be/|shorts/|embed/|live/)([\w-]{11})")


def _slugify(text, week):
    """First few words of the title + the week's Monday as m<dd> — matches the
    hand-made slug convention already in the table (ai-advice-wave-720)."""
    words = re.sub(r"[^a-z0-9]+", " ", text.lower()).split()[:5]
    monday = datetime.strptime(week, "%Y-%m-%d")
    return "-".join(words + [f"{monday.month}{monday.day:02d}"])


_DURATION_RE = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def _youtube_facts(yt_id):
    """What the channel credential already knows about this video: the go-live
    moment (status.publishAt for scheduled videos, snippet.publishedAt for
    publish-now ones), the live title, and the duration in seconds. Empty dict
    on any miss or API failure, so the card degrades to its old fully-manual
    behavior."""
    try:
        items = youtube_auth.api_get(
            "videos", part="status,snippet,contentDetails", id=yt_id).get("items", [])
        if not items:
            return {}
        snip, status = items[0]["snippet"], items[0]["status"]
        facts = {"title": snip.get("title")}
        m = _DURATION_RE.fullmatch(items[0]["contentDetails"].get("duration", ""))
        if m:
            h, mn, s = (int(x or 0) for x in m.groups())
            facts["duration_s"] = h * 3600 + mn * 60 + s
        stamp = status.get("publishAt") or snip.get("publishedAt")
        if stamp:
            facts["published_at"] = datetime.fromisoformat(
                stamp.replace("Z", "+00:00"))
        return facts
    except Exception:
        return {}


def post_week_publish(body):
    """The /week 'Publish + log it' card: paste the YouTube URL, one click.
    Finds this week's video row (same pillar + created-this-week match the page
    uses) or creates it — the row often doesn't exist yet because idea->row
    logging is still by hand — then records the publish facts via
    asset_store.mark_published (which opens the 24h/7d/28d outcome checks) and
    stamps the flow_state step done. The go-live moment is read from the
    YouTube API when the field is left blank, and publishing also closes the
    thumb/desc steps it implies (founder 2026-08-07: publish means the
    thumbnail + description were already applied). Idempotent: re-posting the
    same URL just re-runs mark_published, which never duplicates checks."""
    week, video = body["week"], body["video"]
    if not (WEEK_RE.match(week) and VIDEO_RE.match(video)):
        raise ValueError("bad week/video")
    raw = (body.get("youtube") or "").strip()
    m = YT_ID_RE.search(raw)
    yt_id = m.group(1) if m else raw
    if not re.fullmatch(r"[\w-]{11}", yt_id):
        raise ValueError("that doesn't look like a YouTube URL or 11-char ID")

    # Scheduled-ahead videos: the go-live moment anchors the outcome-check
    # clock, and due_at is write-once by grant — get it right at logging time.
    live = (body.get("live") or "").strip()
    published_at = None
    if live:
        try:
            published_at = datetime.fromisoformat(live)
        except ValueError:
            raise ValueError("go-live must be ISO format like 2026-08-06T16:30")
        if published_at.tzinfo is None:
            published_at = published_at.astimezone()
    facts = _youtube_facts(yt_id)
    if published_at is None:
        published_at = facts.get("published_at")

    # Long-form guard (added after the 2026-08-07 mislog, when a 39s short got
    # logged as the news video): every lane here is an 8-14 min video, so a
    # shorts-length ID is a wrong paste — fail loud, log nothing.
    if facts.get("duration_s", 10**9) < 240:
        raise ValueError(
            f"that video is only {facts['duration_s']}s long "
            f"({facts.get('title')}) — looks like a short, not the {video} "
            "video; double-check the URL")

    rows = supabase.select("videos", params={
        "youtube_video_id": f"eq.{yt_id}", "limit": 1,
    }) or supabase.select("videos", params={
        "pillar": f"eq.{video}", "created_at": f"gte.{week}",
        "order": "created_at.desc", "limit": 1,
    })
    if rows:
        row = rows[0]
    else:
        # Title: what the founder typed on the card, else the live YouTube
        # title, else the week's picked idea.
        title = (body.get("title") or "").strip() or facts.get("title")
        if not title:
            slot = read_state(VIDEO_IDEAS).get(week, {}).get(video, {})
            title = next((i.get("title") for i in slot.get("ideas", [])
                          if i.get("id") == slot.get("picked")), None)
        if not title:
            raise ValueError("no video row this week and no title to create one — "
                             "add the video title and retry")
        row = asset_store.create_video(
            slug=_slugify(title, week), pillar=video, title_final=title,
            notes="logged via /week publish card")

    out = asset_store.mark_published(row["id"], youtube_video_id=yt_id,
                                     published_at=published_at)
    # Publishing implies the thumbnail + description were applied — close
    # those steps too so the card is one click, not three scattered Dones.
    # Already-done steps are skipped to keep their original stamps.
    already = read_state(FLOW_STATE).get(week, {})
    steps = already
    for key in (f"{video}/thumb", f"{video}/desc", f"{video}/publish"):
        if key not in already:
            steps = post_flow_state({"week": week, "key": key, "done": True})
    vid_row = out.get("video") or {}
    return {"ok": True, "slug": row.get("slug"),
            "title": vid_row.get("title_final") or row.get("title_final"),
            "live": vid_row.get("published_at"),
            "checks_opened": out.get("checks_opened"), "steps": steps}


def post_week_publish_scan(body):
    """Auto-notice the publish (founder 2026-08-12): the channel credential
    can already see the upload, so an open publish card shouldn't wait for a
    paste. Scan the channel's recent uploads for a long-form video going live
    this week that isn't logged yet; a clean match funnels into
    post_week_publish, so the row find-or-create, the shorts guard, the
    outcome checks and the step stamps are identical to the manual click.
    Refuses to guess: several candidates with no clear title match (or any
    YouTube API miss) returns matched:False and the card stays a paste box."""
    from zoneinfo import ZoneInfo

    week, video = body["week"], body["video"]
    if not (WEEK_RE.match(week) and VIDEO_RE.match(video)):
        raise ValueError("bad week/video")
    if f"{video}/publish" in read_state(FLOW_STATE).get(week, {}):
        return {"matched": False, "why": "step already done"}

    # This lane's row may already be logged with its video id (chat-session
    # logging, another surface) before this card was ever opened — the row is
    # then the truth and the only thing missing is the step stamp. Funnel its
    # id through post_week_publish (idempotent) instead of refusing it as
    # "already logged" (founder hit 2026-08-14: feature video published and
    # logged in session, card stayed a paste box). A row whose id fails the
    # long-form guard falls through to the normal channel scan.
    rows = supabase.select("videos", params={
        "pillar": f"eq.{video}", "created_at": f"gte.{week}",
        "order": "created_at.desc", "limit": 1})
    if rows and rows[0].get("youtube_video_id"):
        try:
            out = post_week_publish({"week": week, "video": video,
                                     "youtube": rows[0]["youtube_video_id"]})
            out["matched"] = True
            return out
        except ValueError:
            pass

    # A lane that never produced a video this week must not claim one from the
    # channel scan. Without this, two open publish cards scanning in parallel
    # can race: the skipped lane sees the other lane's still-unlogged upload as
    # "the one candidate" and stamps itself published (hit 2026-08-22: ascent
    # claimed the feature video when the founder made only two videos).
    steps = read_state(FLOW_STATE).get(week, {})
    if not any(f"{video}/{s}" in steps for s in ("record", "edit")):
        return {"matched": False,
                "why": "lane has no recorded video this week — not scanning"}

    # The week key IS its Monday; the window opens Monday 00:00 MT.
    monday = datetime.strptime(week, "%Y-%m-%d").replace(
        tzinfo=ZoneInfo("America/Denver"))

    try:
        ch = youtube_auth.api_get("channels", part="contentDetails", mine="true")
        uploads_pl = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        pl = youtube_auth.api_get("playlistItems", part="contentDetails",
                                  playlistId=uploads_pl, maxResults=15)
        ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
        vids = (youtube_auth.api_get("videos", part="snippet,status,contentDetails",
                                     id=",".join(ids)) if ids else {"items": []})
    except Exception as e:  # noqa: BLE001 — scan is best-effort, paste still works
        return {"matched": False, "why": f"YouTube read failed: {e}"}

    fresh = []
    for v in vids.get("items", []):
        m = _DURATION_RE.fullmatch(v["contentDetails"].get("duration", ""))
        h, mn, s = (int(x or 0) for x in m.groups()) if m else (0, 0, 0)
        if h * 3600 + mn * 60 + s < 240:
            continue  # shorts-length — same floor as the manual path's guard
        stamp = v["status"].get("publishAt") or v["snippet"].get("publishedAt")
        if not stamp:
            continue  # private draft with no go-live moment yet
        if datetime.fromisoformat(stamp.replace("Z", "+00:00")) < monday:
            continue  # an earlier week's video
        fresh.append(v)
    if not fresh:
        return {"matched": False, "why": "no long-form upload going live this week"}

    # Drop uploads already in marketing.videos (another lane's video, rescans).
    logged = supabase.select("videos", params={
        "select": "youtube_video_id",
        "youtube_video_id": f"in.({','.join(v['id'] for v in fresh)})"})
    seen = {r["youtube_video_id"] for r in logged}
    fresh = [v for v in fresh if v["id"] not in seen]
    if not fresh:
        return {"matched": False, "why": "this week's uploads are all logged"}

    # One unlogged candidate = the video he just published. Several = only a
    # clear title match against this lane's picked idea may decide.
    pick = fresh[0] if len(fresh) == 1 else None
    if pick is None:
        slot = read_state(VIDEO_IDEAS).get(week, {}).get(video, {})
        title = next((i.get("title") for i in slot.get("ideas", [])
                      if i.get("id") == slot.get("picked")), "") or ""
        want = set(re.sub(r"[^a-z0-9]+", " ", title.lower()).split())

        def overlap(v):
            got = set(re.sub(r"[^a-z0-9]+", " ",
                             v["snippet"]["title"].lower()).split())
            return len(want & got) / max(1, min(len(want), len(got)))

        scored = sorted(((overlap(v), v) for v in fresh),
                        key=lambda x: x[0], reverse=True)
        if want and scored[0][0] >= 0.5 and scored[0][0] > scored[1][0]:
            pick = scored[0][1]
    if pick is None:
        return {"matched": False,
                "why": f"{len(fresh)} unlogged uploads and no clear title match"}

    out = post_week_publish({"week": week, "video": video, "youtube": pick["id"]})
    out["matched"] = True
    return out


def gate_close_check(body):
    """Close an outcome check with real numbers. result must be a non-empty
    object; the page builds it from k=v pairs or wraps free text as a note."""
    result = body["result"]
    if not isinstance(result, dict) or not result:
        raise ValueError("result must be a non-empty object")
    source = body.get("source") or "manual"
    if source not in ("manual", "yt_api", "screenshot"):
        raise ValueError("source must be manual | yt_api | screenshot")
    rows = asset_store.close_check(body["check_id"], result=result, source=source)
    if not rows:
        raise ValueError("check not found / nothing updated")
    return {"ok": True}


def gate_skip_check(body):
    rows = asset_store.skip_check(body["check_id"],
                                  reason=(body.get("reason") or "").strip() or None)
    if not rows:
        raise ValueError("check not found / nothing updated")
    return {"ok": True}


def eval_results():
    """fleet/eval_results.jsonl as a list — the eval harness's history for
    the Fleet view's model scorecard. Local file, read-only, like flow_state."""
    path = Path(__file__).resolve().parent / "eval_results.jsonl"
    try:
        return [json.loads(line) for line in
                path.read_text(encoding="utf-8").strip().splitlines() if line]
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def synth_eval_results():
    """fleet/synth_eval_results.jsonl slimmed for the wire: aggregates only —
    the per-case rows (with full draft text) stay local. Pairs with
    eval_results as the Fleet view's second quality signal."""
    path = Path(__file__).resolve().parent / "synth_eval_results.jsonl"
    try:
        records = [json.loads(line) for line in
                   path.read_text(encoding="utf-8").strip().splitlines() if line]
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    slim = []
    for r in records:
        s = {k: r.get(k) for k in ("ran_at", "guide_version", "drafter_model",
                                   "judge_model", "overall", "by_category")}
        s["gate_failures"] = len(r.get("gate_failures") or [])
        slim.append(s)
    return slim


def gate_candidates():
    """Feature-gate read for the /week gate overlay: open candidates from the
    ideas registry (no build_stage yet, not rejected) newest first, plus
    anything mid-build from earlier weeks. Reads the capture SQLite read-only;
    all writes stay on the idea's own page (/ideas/<id> -> Build gate)."""
    db = ROOT / "capture" / "conversations.db"
    if not db.is_file():
        return {"candidates": [], "in_build": []}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        candidates = [dict(r) for r in conn.execute(
            "SELECT id, title, description, source, status, tags, proposed_by, created_at "
            "FROM product_ideas WHERE build_stage IS NULL AND status != 'rejected' "
            "ORDER BY id DESC LIMIT 12")]
        in_build = [dict(r) for r in conn.execute(
            "SELECT id, title, build_stage, gate_week, target_query, app_url, "
            "created_at, updated_at "
            "FROM product_ideas WHERE build_stage IS NOT NULL AND build_stage NOT IN "
            "('live', 'content-instead') ORDER BY id DESC LIMIT 6")]
        # Recent decided rows (any stage) so the overlay can recognize a seed
        # that's already been picked and show its status instead of re-offering it.
        decided = [dict(r) for r in conn.execute(
            "SELECT id, title, build_stage, gate_week, app_url "
            "FROM product_ideas WHERE build_stage IS NOT NULL "
            "ORDER BY id DESC LIMIT 12")]
        # THIS week's gate decisions, every status — the overlay leads with
        # these so a made call is unmissable (founder confusion 2026-08-11:
        # a live pick dropped out of mid-build and the view showed a world
        # where no call had happened). Rejected rows appear here on purpose:
        # "we looked at it and said no" is part of the record.
        monday = str(date.today() - timedelta(days=date.today().weekday()))
        this_week = [dict(r) for r in conn.execute(
            "SELECT id, title, status, build_stage, gate_week, app_url, quality_notes "
            "FROM product_ideas WHERE gate_week = :wk ORDER BY status = 'rejected', id",
            {"wk": monday})]
    finally:
        conn.close()
    return {"candidates": candidates, "in_build": in_build, "decided": decided,
            "this_week": this_week}


def gate_capture(body):
    """Capture the research run's feature seed into the ideas registry so it can
    be gated like any other candidate (fourth exception; see module docstring).
    Idempotent on exact title: re-clicking returns the existing open row instead
    of inserting a duplicate. Only creates the row — the build call itself is
    recorded on the idea's page, same as every candidate."""
    title = (body.get("title") or "").strip()
    if not (title and len(title) <= 200):
        raise ValueError("title must be 1-200 chars")
    description = (body.get("description") or "").strip() or None
    db = ROOT / "capture" / "conversations.db"
    if not db.is_file():
        raise ValueError("ideas registry database not found")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id FROM product_ideas WHERE title = :t AND build_stage IS NULL "
            "AND status != 'rejected' ORDER BY id DESC LIMIT 1", {"t": title}).fetchone()
        if row:
            return {"id": row["id"], "existing": True}
        try:
            cur = conn.execute(
                "INSERT INTO product_ideas (title, description, source, status, tags, proposed_by) "
                "VALUES (:t, :d, :s, 'idea', 'research-seed', 'ai')",
                {"t": title, "d": description,
                 "s": (body.get("source") or "research feature seed").strip()[:200]})
            conn.commit()
        except sqlite3.IntegrityError as e:
            raise ValueError(f"registry rejected the row: {e}") from e
        return {"id": cur.lastrowid, "existing": False}
    finally:
        conn.close()


def roster():
    """fleet/roster.json — the ONE fleet roster the /week Fleet cards and Org
    Chart build from. Hand-maintained; `python -m fleet.roster` checks it
    against the dispatcher schedule so it can't silently drift."""
    from fleet.roster import load_roster
    return load_roster()


def finder_trials():
    """Tool-trial read for /finders: runs + per-tool results + candidates +
    overlap from finder_trial/trials.db (RSS vs Apify vs OpenCLI, written by
    python -m finder_trial.run). Read-only, same pattern as gate_candidates;
    overlap is computed here so the page stays presentation-only."""
    trials = ROOT / "finder_trial" / "trials.db"
    if not trials.is_file():
        return {"runs": []}
    conn = sqlite3.connect(f"file:{trials}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        runs = [dict(r) for r in conn.execute(
            "SELECT * FROM trial_runs ORDER BY ran_at DESC LIMIT 15")]
        for run in runs:
            run["results"] = [dict(r) for r in conn.execute(
                "SELECT * FROM trial_results WHERE run_id = ? ORDER BY tool",
                (run["id"],))]
            cands = [dict(r) for r in conn.execute(
                "SELECT * FROM trial_candidates WHERE run_id = ? "
                "ORDER BY score DESC, id", (run["id"],))]
            keys = {}
            for c in cands:
                keys.setdefault(c["tool"], set()).add(c["thread_key"])
            union_all = set().union(*keys.values()) if keys else set()
            shared = set.intersection(*keys.values()) if len(keys) > 1 else set()
            run["overlap"] = {
                "distinct_threads": len(union_all),
                "in_all": len(shared) if len(keys) > 1 else None,
                "unique": {
                    t: len(ks - set().union(*(v for u, v in keys.items() if u != t)))
                    for t, ks in keys.items()
                },
                "shared_keys": sorted(k for k in shared if k),
            }
            run["candidates"] = cands[:150]
    finally:
        conn.close()
    return {"runs": runs}


def pulse():
    """Account-level metric snapshots for the /pulse page: the last 35 days of
    youtube_channel / x_account / app_funnel rows, oldest first, grouped by
    kind. Deltas and sparklines are the page's job — this stays a thin read
    over what the daily metrics agent already wrote."""
    from datetime import timedelta, timezone

    since = (datetime.now(timezone.utc) - timedelta(days=35)).date().isoformat()
    rows = supabase.select("metric_snapshots", params={
        "select": "snapshot_date,entity_kind,entity_key,metrics",
        "entity_kind": "in.(youtube_channel,x_account,app_funnel)",
        "snapshot_date": f"gte.{since}",
        "order": "snapshot_date.asc", "limit": 500})
    out = {}
    for r in rows:
        out.setdefault(r["entity_kind"], []).append(r)
    out["x_fresh"] = _x_fresh_impressions()
    return out


def _x_fresh_impressions(days=14):
    """Impressions grouped by the day the post was CREATED (latest snapshot
    per post), for the last `days` days. This is the honest freshness read:
    the x_account panel above it is a TRAILING 28-DAY window, so a single old
    viral reply aging out looks like a crash (founder scare 2026-08-22).
    Fresh-by-post-day cannot lie that way."""
    from datetime import timezone

    snap_since = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    rows = supabase.select("metric_snapshots", params={
        "select": "snapshot_date,entity_key,metrics",
        "entity_kind": "eq.x_post",
        "snapshot_date": f"gte.{snap_since}",
        "order": "snapshot_date.asc", "limit": 400})
    latest = {}
    for r in rows:  # ascending — later snapshots overwrite earlier ones
        latest[r["entity_key"]] = r["metrics"]
    floor = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    by_day = {}
    for m in latest.values():
        created = (m.get("created_at") or "")[:10]
        if created < floor:
            continue
        imps = ((m.get("metrics") or {}).get("impressions")) or 0
        day = by_day.setdefault(created, {"posts": 0, "impressions": 0})
        day["posts"] += 1
        day["impressions"] += imps
    return [{"day": d, **v} for d, v in sorted(by_day.items())]


def shorts_coverage():
    """Per-platform shorts coverage for the /week distribute card.

    The YouTube column is LIVE truth: every recent upload that is
    shorts-length (<= 190s) with its go-live moment, read via the channel
    credential. TikTok/Instagram ride Publer (free plan, no API), so their
    side comes from fleet/shorts_schedule.json — counts written at
    scheduling time, never invented per-day data."""
    from zoneinfo import ZoneInfo

    from fleet import youtube_auth

    mt = ZoneInfo("America/Denver")
    out = {"youtube": [], "publer": read_state(SHORTS_SCHEDULE)}
    try:
        ch = youtube_auth.api_get("channels", part="contentDetails", mine="true")
        uploads = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
        pl = youtube_auth.api_get("playlistItems", part="contentDetails",
                                  playlistId=uploads, maxResults=30)
        ids = [i["contentDetails"]["videoId"] for i in pl.get("items", [])]
        vids = (youtube_auth.api_get("videos", part="snippet,status,contentDetails",
                                     id=",".join(ids)) if ids else {"items": []})
        for v in vids["items"]:
            m = _DURATION_RE.fullmatch(v["contentDetails"].get("duration", ""))
            if not m:
                continue
            h, mn, s = (int(x or 0) for x in m.groups())
            if h * 3600 + mn * 60 + s > 190:
                continue  # long-form, not a short
            stamp = v["status"].get("publishAt") or v["snippet"].get("publishedAt")
            if not stamp:
                continue
            when = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone(mt)
            out["youtube"].append({
                "id": v["id"], "title": v["snippet"]["title"],
                "date": when.strftime("%Y-%m-%d"), "time": when.strftime("%H:%M"),
                "privacy": v["status"].get("privacyStatus"),
            })
        out["youtube"].sort(key=lambda x: (x["date"], x["time"]))
    except Exception as e:  # noqa: BLE001 — coverage view must render either way
        out["youtube_error"] = str(e)
    return out


def week_close_report():
    """Everything the /week close-the-week overlay needs (founder call
    2026-08-14: the last card was a bare mark-done pointing at a chat ritual —
    now the verdicts are recorded on the card itself). One read: the latest
    research run's current verdict, plus every video that belongs to this
    week — made this week (created_at) or gone live this week (published_at)
    — each with its current verdict/note and its freshest closed check so the
    judgment happens with the numbers in view."""
    monday = date.today() - timedelta(days=date.today().weekday())
    nxt = monday + timedelta(days=7)
    out = {"week": monday.isoformat(), "run": None, "videos": []}
    try:
        runs = supabase.select("research_runs", params={
            "select": "id,created_at,outcome_verdict",
            "order": "created_at.desc", "limit": 1})
        if runs:
            out["run"] = runs[0]
    except SupabaseError:
        pass
    sel = ("id,slug,pillar,title_final,status,published_at,"
           "outcome_verdict,outcome_note,created_at")
    vids = {}
    try:
        for r in supabase.select("videos", params={
                "select": sel, "created_at": f"gte.{monday.isoformat()}",
                "limit": 10}):
            vids[r["id"]] = r
        for r in supabase.select("videos", params={
                "select": sel,
                "and": f"(published_at.gte.{monday.isoformat()},"
                       f"published_at.lt.{nxt.isoformat()})",
                "limit": 10}):
            vids.setdefault(r["id"], r)
        if vids:
            checks = supabase.select("outcome_checks", params={
                "select": "entity_id,window_label,result,checked_at",
                "entity_table": "eq.videos", "status": "eq.done",
                "entity_id": f"in.({','.join(vids)})",
                "order": "checked_at.desc", "limit": 40})
            for c in checks:
                v = vids.get(c["entity_id"])
                if v is not None and "latest_check" not in v:
                    v["latest_check"] = {"window": c["window_label"],
                                         "result": c.get("result")}
    except SupabaseError:
        pass
    out["videos"] = sorted(vids.values(),
                           key=lambda v: str(v.get("published_at") or "~"))
    return out


def post_video_verdict(body):
    """Close-the-week: the founder's L1 verdict on one video — strong/ok/off
    + one sentence onto the video row (next Sunday's research reads these).
    Re-saving revises: the verdict is the founder's current read, and a
    post-numbers revision is the point of the two-layer outcome design."""
    verdict = body["verdict"]
    if verdict not in ("strong", "ok", "off"):
        raise ValueError("verdict must be strong | ok | off")
    from datetime import timezone

    rows = asset_store.update_video(
        body["video_id"], outcome_verdict=verdict,
        outcome_note=(body.get("note") or "").strip() or None,
        outcome_checked_at=datetime.now(timezone.utc))
    if not rows:
        raise ValueError("video not found / nothing updated")
    return {"ok": True}


def checks_health():
    """Exception monitor behind the /week measure card (founder call
    2026-08-14: the card is not a step — agents close checks, the numbers
    live on /pulse — so all it owes the founder is a warning when a check is
    STUCK, i.e. still open well past its closer's own cycle). Grace rules:

      * videos       — the dispatch checks job runs daily; > 2 days past due
                       means the job itself is failing on that video.
      * reply_ledger — X replies close Saturdays via Typefully; > 8 days past
                       due means a full weekly cycle went by without a close.
                       (The engine's reddit-frozen carve-out is removed for
                       Fiboprana — all platforms warn; reddit_frozen stays in
                       the payload shape as 0 for the /week strip.)
    """
    from datetime import timezone

    try:
        rows = supabase.select("outcome_checks", params={
            "select": "entity_table,entity_id,due_at",
            "status": "eq.open", "order": "due_at.asc", "limit": 200})
    except SupabaseError:
        return {"open": None, "video_stuck": 0, "reply_stuck": 0,
                "reddit_frozen": 0}
    now = datetime.now(timezone.utc)

    def days_past(r):
        due = datetime.fromisoformat(str(r["due_at"]).replace("Z", "+00:00"))
        return (now - due).total_seconds() / 86400
    video_stuck = [r for r in rows
                   if r["entity_table"] == "videos" and days_past(r) > 2]
    reply_stuck = [r for r in rows
                   if r["entity_table"] == "reply_ledger" and days_past(r) > 8]
    return {"open": len(rows),
            "video_stuck": len(video_stuck),
            "reply_stuck": len(reply_stuck),
            "reddit_frozen": 0}


def pain_report():
    """Evidence pack for the /week "name the week's pain" card — assembled
    from data that already exists, no LLM call: the latest research run's
    Reddit themes (the machine's pre-week hypothesis), this week's sends and
    the judge's top of the queue (what the founder actually heard), and the
    recorded call if one exists (the week_pain slug line on the run's
    outcome_note — the same note next Sunday's run reads). Supabase misses
    degrade to empty sections; the overlay must render either way."""
    out = {"run": None, "themes": [], "call": None, "sent": [], "queue": []}
    try:
        runs = supabase.select("research_runs", params={
            "select": "id,created_at,content_pull,outcome_note",
            "order": "created_at.desc", "limit": 1})
    except SupabaseError:
        runs = []
    if runs:
        run = runs[0]
        out["run"] = {"id": run["id"], "created_at": run["created_at"]}
        pull = run.get("content_pull") or {}
        themes = pull.get("reddit_themes") if isinstance(pull, dict) else None
        if isinstance(themes, list):
            out["themes"] = [str(t) for t in themes]
        elif themes:
            out["themes"] = [str(themes)]
        out["call"] = _week_pain_of(run.get("outcome_note"))
    monday = date.today() - timedelta(days=date.today().weekday())
    try:
        out["sent"] = supabase.select("reply_ledger", params={
            "select": "replied_at,platform,community,post_title,post_url",
            "replied_at": f"gte.{monday.isoformat()}",
            "order": "replied_at.desc", "limit": 50})
    except SupabaseError:
        pass
    try:
        out["queue"] = supabase.select("reply_candidates", params={
            "select": "platform,community,post_title,judge_rationale,judge_score,post_url",
            "status": "in.(judged,drafted)",
            "order": "judge_score.desc.nullslast", "limit": 25})
    except SupabaseError:
        pass
    return out


def _week_pain_of(note):
    for line in (note or "").split("\n"):
        if line.startswith("week_pain:"):
            return line[len("week_pain:"):].strip() or None
    return None


def post_pain_call(body):
    """Record the founder's named pain for the week ONTO the research run —
    merged into outcome_note as a "week_pain:" slug line beside the Q&A
    answers, so next Sunday's run reads the ratified pain, not just the
    hypothesis. Then stamp the decide/pain step (recording the call IS the
    done-click, same one-event-one-record rule as decide/build)."""
    run_id = body["run_id"]
    pain = " ".join(str(body["pain"]).split())
    week = body["week"]
    if not pain:
        raise ValueError("pain text is empty")
    if not WEEK_RE.match(week):
        raise ValueError("bad week")
    rows = supabase.select("research_runs", params={
        "select": "outcome_note", "id": f"eq.{run_id}", "limit": 1})
    if not rows:
        raise ValueError("run not found")
    note = rows[0].get("outcome_note") or ""
    kept = [ln for ln in note.split("\n")
            if ln.strip() and not ln.startswith("week_pain:")]
    kept.append(f"week_pain: {pain}")
    store.update_outcome(run_id, note="\n".join(kept))
    state = read_state(FLOW_STATE)
    steps = state.setdefault(week, {})
    steps["decide/pain"] = _now()
    write_state(FLOW_STATE, state)
    return {"ok": True, "pain": pain, "steps": steps}


def post_gate_record(body):
    """Wire 7 (founder call 2026-08-19): the weekly build-or-content call is
    ONE click on the /week gate overlay. Sets build_stage on the idea row
    (stamping gate_week once, like /ideas/<id>/build does), then stamps
    decide/gate — recording the call IS the done-click, same rule as
    decide/pain. The founder only makes the call; the gate paperwork
    (registry match, target query, differentiation angle, quality notes) is
    filled by Claude in-session after the click (a fleet agent later). The
    idea's own page stays the place to browse or amend, never a required
    weekly step."""
    idea_id = int(body["idea_id"])
    week = body["week"]
    stage = body["stage"]
    if not WEEK_RE.match(week):
        raise ValueError("bad week")
    if stage not in ("gated", "content-instead"):
        raise ValueError("stage must be 'gated' or 'content-instead'")
    db = ROOT / "capture" / "conversations.db"
    if not db.is_file():
        raise ValueError("ideas registry database not found")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT id, title FROM product_ideas WHERE id = :id",
                           {"id": idea_id}).fetchone()
        if row is None:
            raise ValueError("idea not found")
        conn.execute(
            "UPDATE product_ideas SET build_stage = :st, "
            "gate_week = COALESCE(gate_week, :wk), updated_at = datetime('now') "
            "WHERE id = :id",
            {"st": stage, "wk": week, "id": idea_id})
        conn.commit()
        title = row["title"]
    finally:
        conn.close()
    state = read_state(FLOW_STATE)
    steps = state.setdefault(week, {})
    steps["decide/gate"] = _now()
    write_state(FLOW_STATE, state)
    return {"ok": True, "id": idea_id, "title": title, "stage": stage,
            "steps": steps}


def reply_days():
    """Per-channel, per-day reply activity for the /week daily strip.

    The reply ledger is the truth: a day/channel cell lights because a reply
    was actually logged as sent (the /review "Log as sent" click writes the
    row at act time), never because someone ticked a box. Channel rows come
    from config + data, not a hardcoded list — the REPLY_CHANNELS adapters,
    plus any platform holding queue candidates or sends this week — so a new
    channel appears on the board the day its agent does. Supabase misses
    degrade to empty sections; the strip must render either way."""
    from fleet.channels import enabled_adapters

    today = date.today()
    monday = today - timedelta(days=today.weekday())

    sent = {}
    try:
        rows = supabase.select("reply_ledger", params={
            "select": "replied_at,platform",
            "replied_at": f"gte.{monday.isoformat()}",
            "limit": 500})
    except SupabaseError:
        rows = []
    for r in rows:
        if not r.get("replied_at"):
            continue
        stamp = str(r["replied_at"]).replace("Z", "+00:00")
        day = datetime.fromisoformat(stamp).astimezone().date().isoformat()
        ch = sent.setdefault(r.get("platform") or "reddit", {})
        ch[day] = ch.get(day, 0) + 1

    names = []
    try:
        names += [a.name for a in enabled_adapters()]
    except ValueError:
        pass  # a typo in REPLY_CHANNELS shouldn't blank the strip
    try:
        queue = supabase.select("reply_candidates", params={
            "select": "platform", "status": "in.(judged,drafted)", "limit": 500})
        names += [c.get("platform") or "reddit" for c in queue]
    except SupabaseError:
        pass
    names += list(sent)
    preferred = ["x", "reddit", "youtube"]
    ordered = ([n for n in preferred if n in names]
               + sorted(set(names) - set(preferred)))

    # Hand-stated sending freezes — no API exposes account standing, so this
    # map is the honest record (same trust tier as hard_limits "stated").
    # Add an entry when a channel freezes; delete it when the situation ends.
    notes = {}
    return {"week": monday.isoformat(),
            "channels": [{"name": n, "note": notes.get(n)} for n in ordered],
            "sent": sent}


def hard_limits():
    """Every hard limit the fleet runs under, for the /week limits card.

    Three trust tiers, labeled honestly so the display never overclaims:
      * code   — read LIVE from the enforcing module/env at request time;
                 what you see is what the next run will enforce.
      * data   — read from today's metric snapshot (platform-reported).
      * stated — account-level backstops set in vendor dashboards; code
                 can't see them, so each carries when it was set and its
                 verification status instead of pretending to be live.
    """
    from fleet import llm

    code_limits = [
        {"name": "LLM spend per agent run", "value": f"${llm.RUN_MAX_COST_USD:.2f}",
         "detail": "circuit breaker in fleet/llm.py — complete() refuses calls past this",
         "env": "LLM_RUN_MAX_COST_USD"},
        {"name": "LLM calls per agent run", "value": str(llm.RUN_MAX_CALLS),
         "detail": "same breaker; synth-eval runs raise it to 100 for the 80-case suite",
         "env": "LLM_RUN_MAX_CALLS"},
        {"name": "Reply candidates per run", "value": "10",
         "detail": "finder keeps only the top-ranked 10 before any LLM spend (dispatch)"},
        {"name": "Pairwise eval pairs per run", "value": "12",
         "detail": "reply_eval_agent MAX_PAIRS"},
        {"name": "Supabase request timeout", "value": "20s",
         "detail": "fleet/supabase.py — a stalled PostgREST call never hangs a cron"},
    ]

    quota = None
    try:
        rows = supabase.select("metric_snapshots", params={
            "select": "snapshot_date,metrics", "entity_kind": "eq.x_account",
            "order": "snapshot_date.desc", "limit": 1})
        if rows:
            quota = {"as_of": rows[0]["snapshot_date"],
                     **(rows[0]["metrics"].get("publishing_quota") or {})}
    except SupabaseError:
        pass  # card renders without the quota row rather than erroring

    stated = [
        {"name": "Railway compute spend", "value": "$10 alert / $20 hard stop",
         "set_at": "2026-08-02", "status": "set in Railway dashboard (browser-ops session)"},
        {"name": "Railway agent spend", "value": "$5",
         "set_at": "2026-08-02", "status": "set in Railway dashboard"},
        {"name": "OpenRouter + other API dashboards", "value": "caps unverified",
         "set_at": None, "status": "NOT yet confirmed — #2 on the browser-ops queue"},
    ]

    policy = [
        {"name": "Nothing auto-posts", "detail": "standing — agents draft, founder posts"},
        {"name": "~5 replies/day", "detail": "off the ranked queue (current experiment)"},
        {"name": "2 X posts/day", "detail": "the 14-post weekly batch cadence"},
        {"name": "One marketing experiment at a time", "detail": "standing decision"},
    ]
    return {"code": code_limits, "typefully_quota": quota,
            "stated": stated, "policy": policy}


# Local read-only GET endpoints beyond the five state files, by API name.
# ── the Models panel (founder-directed 2026-08-22) ──────────────────────────
# One screen to see and retune which model every agent runs on. Reads resolve
# through llm.model_for (env var > fleet/models.json > default); the panel
# writes fleet/models.json, and because every agent runs as a fresh
# subprocess, an edit applies to the very next run. With OPENROUTER_API_KEY
# set, any OpenRouter slug is a valid value ("google/gemini-2.5-flash").
MODELS_REGISTRY = [
    ("radar_digest", "Research digest (weekly)", "claude-sonnet-4-6"),
    ("radar_topic_summary", "Radar topic summaries", "claude-haiku-4-5-20251001"),
    ("content_pull", "Research content pull", "claude-sonnet-4-6"),
    ("research_verify", "Research verifier", "claude-sonnet-4-6"),
    ("judge", "Reply judge", "claude-sonnet-4-6"),
    ("drafter", "Reply drafter", "claude-sonnet-4-6"),
    ("distiller", "Style distiller (Sat)", "claude-sonnet-4-6"),
    ("eval_judge", "Eval judge (blind grader)", "claude-sonnet-4-6"),
    ("repurposer", "Clip repurposer", "claude-sonnet-4-6"),
    ("news_ideas", "News story options", "claude-sonnet-4-6"),
    ("worked_example", "Worked example (feature demo)", "claude-sonnet-4-6"),
    ("facts_verify", "Facts agent (verify the story)", "claude-sonnet-4-6"),
    ("script_agent", "Video script agent", "claude-sonnet-4-6"),
    ("video_artifacts", "Deck + thumbs + package fan-out", "claude-sonnet-4-6"),
    ("x_batch", "X weekly batch", "claude-sonnet-4-6"),
    ("email_notice", "Notice email", "claude-sonnet-4-6"),
    ("shorts_judge", "Shorts batch judge", "claude-sonnet-4-6"),
    ("synthesis", "Observation synthesis", "claude-sonnet-4-6"),
    ("swipe_triage", "Swipe-file triage", "claude-haiku-4-5-20251001"),
]
MODEL_OPTIONS = [
    "claude-sonnet-4-6", "claude-haiku-4-5-20251001", "claude-opus-4-8",
    "anthropic/claude-sonnet-4.6", "google/gemini-2.5-flash",
    "openai/gpt-5.2", "openrouter/auto",
]
MODEL_VALUE_RE = re.compile(r"^[\w.:/-]{1,80}$")
MODELS_FILE_PATH = Path(llm.MODELS_FILE)


def get_models():
    try:
        overrides = json.loads(MODELS_FILE_PATH.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        overrides = {}
    rows = []
    for key, label, default in MODELS_REGISTRY:
        env = os.environ.get(f"MODEL_{key.upper()}")
        current = llm.model_for(key, default)
        source = ("env" if env else
                  "panel" if overrides.get(key) else "default")
        rows.append({"key": key, "label": label, "default": default,
                     "current": current, "source": source})
    return {"models": rows, "options": MODEL_OPTIONS,
            "openrouter": bool(os.environ.get("OPENROUTER_API_KEY"))}


def post_model_set(body):
    """Set (or clear, with an empty value) one agent's model override."""
    key = body.get("key")
    if key not in {k for k, _, _ in MODELS_REGISTRY}:
        raise ValueError("unknown agent key")
    value = (body.get("model") or "").strip()
    if value and not MODEL_VALUE_RE.match(value):
        raise ValueError("that doesn't look like a model id or OpenRouter slug")
    try:
        overrides = json.loads(MODELS_FILE_PATH.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError):
        overrides = {}
    if value:
        overrides[key] = value
    else:
        overrides.pop(key, None)
    tmp = MODELS_FILE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(overrides, indent=2), encoding="utf-8")
    tmp.replace(MODELS_FILE_PATH)
    return get_models()


_catalog_cache = {"at": 0.0, "data": None}


def model_catalog():
    """The live OpenRouter catalog for the Models panel: every routable slug
    with its price per million tokens and context size. Public endpoint, no
    auth, cached an hour (prices and promos move, agents don't need it
    fresher). The panel's datalist is built from this, so anything OpenRouter
    can serve is one click away on /map."""
    if _catalog_cache["data"] and time.time() - _catalog_cache["at"] < 3600:
        return _catalog_cache["data"]
    try:
        req = urllib.request.Request("https://openrouter.ai/api/v1/models",
                                     headers={"User-Agent": "fiboprana-fleet/1.0"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = json.loads(resp.read())
    except Exception as e:  # noqa: BLE001 — panel falls back to presets
        return {"models": [], "count": 0, "error": str(e)}

    def per_m(v):
        try:
            return round(float(v) * 1e6, 2)
        except (TypeError, ValueError):
            return None

    models = []
    for m in raw.get("data", []):
        pricing = m.get("pricing") or {}
        models.append({"id": m.get("id"), "name": m.get("name"),
                       "in": per_m(pricing.get("prompt")),
                       "out": per_m(pricing.get("completion")),
                       "ctx": m.get("context_length")})
    models.sort(key=lambda x: x["id"] or "")
    out = {"models": models, "count": len(models)}
    _catalog_cache.update(at=time.time(), data=out)
    return out


def agent_runs():
    """Per-agent scorecards from the last 30 days of heartbeats (personal-
    brand port 2026-08-23: its fleet report cards, on data we already had -
    every agent's heartbeat carries model/tokens/cost/duration via llm's
    meter). Read-only aggregation; pairs with the Models panel so a cheaper
    model's success rate is visible where the model gets picked."""
    since = (datetime.now().astimezone() - timedelta(days=30)).isoformat()
    rows = supabase.select("agent_execution_logs", params={
        "select": "agent_name,status,model,cost_usd,duration_ms,message,created_at",
        "event": f"eq.{store.HEARTBEAT_EVENT}",
        "created_at": f"gte.{since}",
        "order": "created_at.desc",
        "limit": 1000,
    })
    cards = {}
    for r in rows:
        c = cards.setdefault(r["agent_name"], {
            "agent": r["agent_name"], "runs": 0, "failures": 0,
            "cost_usd": 0.0, "duration_ms": [], "models": {},
            "last_run_at": r["created_at"], "last_status": r["status"],
            "last_message": (r.get("message") or "")[:160]})
        c["runs"] += 1
        if r["status"] != "success":
            c["failures"] += 1
        c["cost_usd"] += float(r.get("cost_usd") or 0)
        if r.get("duration_ms"):
            c["duration_ms"].append(r["duration_ms"])
        if r.get("model"):
            c["models"][r["model"]] = c["models"].get(r["model"], 0) + 1
    out = []
    for c in cards.values():
        durs = c.pop("duration_ms")
        models = c.pop("models")
        c["cost_usd"] = round(c["cost_usd"], 4)
        c["avg_duration_s"] = round(sum(durs) / len(durs) / 1000, 1) if durs else None
        c["dominant_model"] = max(models, key=models.get) if models else None
        c["success_rate"] = round((c["runs"] - c["failures"]) / c["runs"] * 100)
        out.append(c)
    out.sort(key=lambda c: c["last_run_at"], reverse=True)
    return {"cards": out, "window_days": 30}


GETTERS = {
    "hard_limits": hard_limits,
    "models": get_models,
    "agent_runs": agent_runs,
    "model_catalog": model_catalog,
    "pulse": pulse,
    "shorts_coverage": shorts_coverage,
    "eval_results": eval_results,
    "synth_eval_results": synth_eval_results,
    "gate_candidates": gate_candidates,
    "roster": roster,
    "finder_trials": finder_trials,
    "reply_days": reply_days,
    "pain_report": pain_report,
    "checks_health": checks_health,
    "week_close_report": week_close_report,
}


def api_select(table, params):
    """The read-only Supabase proxy: whitelisted tables only, query params
    forwarded verbatim as PostgREST params (select, order, limit, eq...)."""
    if table not in ALLOWED:
        raise LookupError("unknown table")
    return supabase.select(table, params=params or None)


# POST /api/<name> -> action. One dispatch table shared by both doors.
POST_ACTIONS = {
    "model_set": post_model_set,
    "flow_state": post_flow_state,
    "fleet_controls": post_fleet_controls,
    "video_ideas": post_video_ideas,
    "x_batch": post_x_batch,
    "x_schedule_run": post_x_schedule_run,
    "week_publish": post_week_publish,
    "week_publish_scan": post_week_publish_scan,
    "email_broadcast": post_email_broadcast,
    "log_sent": post_log_sent,
    "dismiss": post_dismiss,
    "pain_call": post_pain_call,
    "gate_record": post_gate_record,
    "video_verdict": post_video_verdict,
    "gate_capture": gate_capture,
    "gate/research_verdict": gate_research_verdict,
    "gate/approve_packaging": gate_approve_packaging,
    "gate/mark_published": gate_mark_published,
    "gate/close_check": gate_close_check,
    "gate/skip_check": gate_skip_check,
}

PAGES = {
    "/": PAGE, "/index.html": PAGE, "/kanban": KANBAN, "/flow": FLOW,
    "/week": WEEK, "/review": REVIEW, "/map": MAP, "/finders": FINDERS,
    "/pulse": PULSE, "/convert": CONVERT,
    # /fleet = the same ledger page as /: pages link to it by that name because
    # inside app.py (the merged door) / belongs to the modules home.
    "/fleet": PAGE,
}


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):  # noqa: N802 (stdlib naming)
        parsed = urllib.parse.urlparse(self.path)
        page = PAGES.get(parsed.path)
        if page:
            return self._send(200, page.read_bytes(), "text/html; charset=utf-8")
        if parsed.path == "/vendor/mermaid.min.js" and MERMAID.is_file():
            return self._send(200, MERMAID.read_bytes(), "application/javascript")
        m = AID_RE.match(parsed.path)
        if m:
            deck = AIDS_DIR / m.group(1)
            if deck.is_file():
                return self._send(200, deck.read_bytes(), "text/html; charset=utf-8")
            return self._send(404, b"no such deck", "text/plain")
        if parsed.path == "/api/narration":
            q = dict(urllib.parse.parse_qsl(parsed.query))
            try:
                _name, data = narration_audio(q.get("week", ""), q.get("video", ""))
                return self._send(200, data, "audio/mpeg")
            except (ValueError, LookupError) as e:
                return self._json(404, {"error": str(e)})
        if parsed.path == "/api/stills":
            q = dict(urllib.parse.parse_qsl(parsed.query))
            try:
                return self._json(200, stills_listing(q.get("week", ""), q.get("video", "")))
            except (ValueError, LookupError) as e:
                return self._json(404, {"error": str(e)})
        if parsed.path == "/api/still":
            q = dict(urllib.parse.parse_qsl(parsed.query))
            try:
                data = still_image(q.get("week", ""), q.get("video", ""), q.get("file", ""))
                return self._send(200, data, "image/png")
            except (ValueError, LookupError) as e:
                return self._json(404, {"error": str(e)})
        if parsed.path == "/api/renders":
            q = dict(urllib.parse.parse_qsl(parsed.query))
            try:
                return self._json(200, renders_listing(q.get("week", ""), q.get("video", "")))
            except (ValueError, LookupError) as e:
                return self._json(404, {"error": str(e)})
        if parsed.path == "/api/render":
            q = dict(urllib.parse.parse_qsl(parsed.query))
            try:
                f = render_video_path(q.get("week", ""), q.get("video", ""), q.get("file", ""))
            except (ValueError, LookupError) as e:
                return self._json(404, {"error": str(e)})
            return self._send_file_range(f, "video/mp4")
        if parsed.path.startswith("/api/"):
            name = parsed.path[len("/api/"):].strip("/")
            if name in STATE_FILES:
                return self._json(200, read_state(STATE_FILES[name]))
            if name in GETTERS:
                return self._json(200, GETTERS[name]())
            try:
                return self._json(200, api_select(
                    name, dict(urllib.parse.parse_qsl(parsed.query))))
            except LookupError:
                return self._json(404, {"error": "unknown table"})
            except SupabaseError as e:
                return self._json(502, {"error": str(e)})
        return self._send(404, b"not found", "text/plain")

    def do_POST(self):  # noqa: N802 (stdlib naming)
        path = urllib.parse.urlparse(self.path).path
        action = POST_ACTIONS.get(path[len("/api/"):]) if path.startswith("/api/") else None
        if action is None:
            return self._send(404, b"not found", "text/plain")
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length))
            return self._json(200, action(body))
        except (ValueError, KeyError, json.JSONDecodeError) as e:
            return self._json(400, {"error": str(e)})
        except SupabaseError as e:
            return self._json(502, {"error": str(e)})

    def _json(self, code, data):
        return self._send(code, json.dumps(data, default=str).encode("utf-8"),
                          "application/json")

    def _send_file_range(self, path, ctype):
        """Stream a large file with HTTP Range support so <video> can seek —
        renders are hundreds of MB and never belong in memory whole."""
        size = path.stat().st_size
        start, end = 0, size - 1
        m = re.match(r"bytes=(\d*)-(\d*)$", self.headers.get("Range", "") or "")
        partial = bool(m and (m.group(1) or m.group(2)))
        if partial:
            if m.group(1):
                start = int(m.group(1))
                if m.group(2):
                    end = min(int(m.group(2)), size - 1)
            else:  # suffix range: last N bytes
                start = max(0, size - int(m.group(2)))
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(206 if partial else 200)
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if partial:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as fh:
            fh.seek(start)
            remaining = length
            while remaining > 0:
                chunk = fh.read(min(1024 * 512, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionError, BrokenPipeError):
                    return  # player closed / seeked away — normal
                remaining -= len(chunk)

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")  # always live data
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # keep the terminal quiet; errors surface as HTTP responses


def main():
    parser = argparse.ArgumentParser(description="Marketing OS dashboard (local, read-only).")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true",
                        help="don't auto-open the browser")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    start_drain_runner()
    print(f"Marketing OS dashboard -> {url}   (Ctrl+C to stop)")
    print(f"drain runner: sweeping publish/xpost every {DRAIN_INTERVAL_S // 60} min "
          f"-> {DRAIN_RUNNER_LOG.name}")
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
