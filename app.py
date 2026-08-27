"""Local Flask dashboard for Fiboprana marketing tooling.

Primary interface for the capture module. The CLI scripts in capture/ still
work for quick terminal ops. By default binds to 127.0.0.1 with debug=True;
pass --network to bind to 0.0.0.0 (reachable from phones on the same WiFi),
which forces debug=False so the Werkzeug debugger isn't exposed on the LAN.

Launch:
    pip install -r requirements.txt
    python app.py              # localhost only, debug on
    python app.py --network    # LAN-accessible, debug off
"""

import argparse
import json
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
import sys

# When run directly (python app.py) this file is loaded as __main__. The
# per-module blueprint files do `from app import ...`, which would otherwise
# cause Python to load app.py a SECOND time under the name 'app' and hit a
# circular import on the bottom-of-file blueprint registrations. Aliasing
# __main__ -> app in sys.modules keeps them as one module.
if __name__ == "__main__" and "app" not in sys.modules:
    sys.modules["app"] = sys.modules["__main__"]

from dotenv import load_dotenv

# Load .env before anything else reads os.environ. Silent no-op if the file
# is missing; extraction will fail gracefully later with a clear message.
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

# Make capture/ importable so we can reuse db.py and export.py as-is
# (they use `from db import ...` internally, so adding the folder to sys.path
# keeps them untouched while letting app.py import them too).
sys.path.insert(0, str(ROOT / "capture"))

from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for  # noqa: E402

from db import MODULE_DIR, ensure_schema, get_connection  # noqa: E402
from export import write_csv, write_markdown  # noqa: E402
import export_observations  # noqa: E402
from extraction_prompt import EXTRACTION_SYSTEM_PROMPT  # noqa: E402

from sourcing import db as sourcing_db  # noqa: E402
from sourcing.config import load_config as load_sourcing_config  # noqa: E402
from sourcing.reddit_client import SourcingConfigError, get_reddit_client  # noqa: E402
from sourcing import scanner as sourcing_scanner  # noqa: E402

from sourcing_web import db as sourcing_web_db  # noqa: E402
from sourcing_web.config import load_config as load_sourcing_web_config  # noqa: E402
from sourcing_web.exa_client import SourcingWebConfigError, get_exa_client  # noqa: E402
from sourcing_web import scanner as sourcing_web_scanner  # noqa: E402
from sourcing_web import extractor as sourcing_web_extractor  # noqa: E402

from radar import db as radar_db  # noqa: E402
from radar.config import load_config as load_radar_config  # noqa: E402
from radar.exa_client import RadarConfigError, get_exa_client as get_radar_exa_client  # noqa: E402
from radar import scanner as radar_scanner  # noqa: E402
from radar.topic_summary_prompt import RADAR_TOPIC_SUMMARY_SYSTEM_PROMPT  # noqa: E402

from content.x_post_prompt import X_POST_SYSTEM_PROMPT  # noqa: E402
from content import utils as content_utils  # noqa: E402
from content.idea_proposal_prompt import IDEA_PROPOSAL_SYSTEM_PROMPT  # noqa: E402
from content.guardrails_check import check_idea_against_guardrails  # noqa: E402
from fleet import llm  # noqa: E402
from content.image_prompt import IMAGE_PROMPT_SYSTEM_PROMPT  # noqa: E402
from content.tiktok_post_prompt import TIKTOK_POST_SYSTEM_PROMPT  # noqa: E402
from content.tiktok_image_prompt import TIKTOK_IMAGE_PROMPT_SYSTEM_PROMPT  # noqa: E402
from content.linkedin_post_prompt import LINKEDIN_POST_SYSTEM_PROMPT  # noqa: E402
from content.linkedin_image_prompt import LINKEDIN_IMAGE_PROMPT_SYSTEM_PROMPT  # noqa: E402


from synthesis import db as synthesis_db  # noqa: E402
from synthesis import analyzer as synthesis_analyzer  # noqa: E402
from synthesis import synthesizer as synthesis_runner  # noqa: E402


app = Flask(__name__)


# Jinja global so capture/observation forms can render existing-tag chips
# without every render_template call having to pass the list. Called lazily
# from the templates that need it (capture_new.html, observations_new.html).
app.jinja_env.globals["existing_tags"] = lambda: collect_existing_tags()

# ── App-module models: CONFIG, not code (model-agnostic rule, 2026-07-22) ──
# Every LLM call in the app modules goes through fleet/llm.py (OpenRouter
# first if OPENROUTER_API_KEY is set, direct Anthropic as the fallback).
# These constants are the config strings the routes pass to llm.complete().
EXTRACTION_MODEL = "claude-sonnet-4-6"
X_POST_MODEL = "claude-sonnet-4-6"
TIKTOK_POST_MODEL = "claude-sonnet-4-6"
LINKEDIN_POST_MODEL = "claude-sonnet-4-6"
IDEA_PROPOSAL_MODEL = "claude-sonnet-4-6"
GUARDRAILS_CHECK_MODEL = "claude-sonnet-4-6"
IMAGE_PROMPT_MODEL = "claude-sonnet-4-6"
TIKTOK_IMAGE_PROMPT_MODEL = "claude-sonnet-4-6"
LINKEDIN_IMAGE_PROMPT_MODEL = "claude-sonnet-4-6"
# ── Fleet-agent models: CONFIG, not code (model-agnostic rule, 2026-07-22) ──
# These constants are the config for everything the fleet agents call via
# fleet/llm.py. Swap a model by editing the default here OR setting the env
# var — with OPENROUTER_API_KEY set, any OpenRouter slug works (e.g.
# "google/gemini-2.5-flash", "openrouter/auto"); bare Claude ids work on
# both the OpenRouter and direct-Anthropic paths.
# Haiku is the right tier for radar topic summaries: short input (titles +
# blurbs), short output (≤4 bullets), and called once per topic on demand.
# 2026-08-22: the defaults below now resolve through llm.model_for, so the
# dashboard's Models panel (fleet/models.json) can retune any of them without
# a code change. Precedence: env var > models.json > the default named here.
# The Flask app reads these at startup; the cron/spawned agents re-resolve on
# every run.
RADAR_TOPIC_SUMMARY_MODEL = llm.model_for("radar_topic_summary",
                                          "claude-haiku-4-5-20251001")
# The weekly trend digest reads the whole week's pile and runs about once per
# week, so it gets the Sonnet tier rather than Haiku — higher-stakes, low-frequency.
RADAR_DIGEST_MODEL = llm.model_for("radar_digest", "claude-sonnet-4-6")
# The content-pull step (research agent) turns the digest into the five
# weekly handoffs + reasoning — low-frequency, higher-stakes, so Sonnet too.
CONTENT_PULL_MODEL = llm.model_for("content_pull", "claude-sonnet-4-6")
# The verification step (fleet/research_verify.py) adversarially re-checks the
# pull's promoted claims against primary sources. Same tier as the researcher
# for now; a separate env var so it can diverge if the verifier ever starts
# inheriting the researcher's blind spots (fresh context does most of the work).
RESEARCH_VERIFY_MODEL = llm.model_for("research_verify", "claude-sonnet-4-6")
# Haiku for swipe-file triage: a short JSON-structuring task, one call per saved item.
SWIPE_TRIAGE_MODEL = llm.model_for("swipe_triage", "claude-haiku-4-5-20251001")
# The reply finder's thin JUDGE scores keyword-prefiltered reply candidates for
# strategic fit — a short classification (verdict + one-line why) over the day's
# top-N posts. Sonnet, matching the other read-text->structured-JSON calls
# (content pull, digest): brand-fit judgment wants nuance, and the volume
# (~10 candidates/day) keeps it cheap. Bump to Opus if the verdicts feel shallow.
JUDGE_MODEL = llm.model_for("judge", "claude-sonnet-4-6")
# The reply DRAFTER writes 2-3 in-voice reply variants per 'engage' candidate —
# voice-sensitive generation, low volume. Sonnet, matching the content generators
# (X / TikTok / LinkedIn posts all use Sonnet); the human edits every draft and the
# system learns voice from the diff, so the bar is "good raw material", not perfect.
DRAFTER_MODEL = llm.model_for("drafter", "claude-sonnet-4-6")
# The DISTILLER reads the week's draft->final diffs and proposes style-guide /
# judge-rubric changes — low-frequency, analysis-heavy, founder-gated. Sonnet.
DISTILLER_MODEL = llm.model_for("distiller", "claude-sonnet-4-6")
# The REPURPOSER packages one clip into platform-native post drafts (YouTube
# Short / X / Instagram / TikTok) — voice-sensitive generation, like the drafter.
REPURPOSER_MODEL = llm.model_for("repurposer", "claude-sonnet-4-6")
# The EVAL JUDGE blindly pairwise-compares fresh drafts vs the founder's sent replies
# (weekly eval harness). Keep it a DIFFERENT knob from the drafter so the model
# under test never has to share config with the model doing the grading.
EVAL_JUDGE_MODEL = llm.model_for("eval_judge", "claude-sonnet-4-6")

# Soft cap on observations passed into the proposal prompt. 14 today, but
# bounding now keeps token cost predictable as the corpus grows. Most-recent-N
# (by id desc) is the simplest selection that still covers the synthesis dates.
MAX_OBSERVATIONS_FOR_PROPOSAL = 50

# Synthesis passes ALL observations (chronological order) up to this cap.
# 200 is well above current corpus size; bumps the ceiling for cost predictability.
# Each observation is ~500-1000 tokens, so 200 fits comfortably in Sonnet's context.
MAX_OBSERVATIONS_FOR_SYNTHESIS = 200
SYNTHESIS_MODEL = llm.model_for("synthesis", "claude-sonnet-4-6")
if not llm.configured():
    print(
        "[warn] No LLM API key configured — AI features disabled. Set "
        "OPENROUTER_API_KEY (one key, any provider) or ANTHROPIC_API_KEY "
        "in .env and restart to enable.",
        file=sys.stderr,
    )

EXPORTS_DIR = MODULE_DIR / "exports"

# Attribution module configuration. The short links built by this dashboard
# point at <ATTRIBUTION_BASE_URL>/r/<code> — set this to a public host (via
# .env) once you're publishing links anywhere that needs to be reachable
# from outside the local machine. Default destination is the canonical
# Fiboprana marketing URL; per-link overrides happen in the create form.


# Dashboard tile metadata.
MODULES = [
    {"name": "capture",        "title": "Capture",        "description": "Log conversations with potential users.",                          "active": True,  "url": "/capture"},
    {"name": "observations",   "title": "Observations",   "description": "Passive research on existing online threads.",                     "active": True,  "url": "/observations"},
    {"name": "sourcing",       "title": "Sourcing",       "description": "Reddit scanner for finding people to talk to.",                    "active": True,  "url": "/sourcing"},
    {"name": "sourcing_web",   "title": "Web Sourcing",   "description": "Exa-powered web search for audience-fit content. Reddit-only in v1.", "active": True,  "url": "/sourcing/web"},
    {"name": "radar",          "title": "Radar",          "description": "Track competitors and the mind-body wellness niche via HN, RSS, and Exa.", "active": True,  "url": "/radar"},
    {"name": "product_ideas",  "title": "Product Ideas",  "description": "Capture and AI-propose features for the Fiboprana app.",           "active": True,  "url": "/ideas"},
    {"name": "outreach",       "title": "Outreach",       "description": "Message templates and send tracking.",                             "active": False},
    {"name": "synthesis",      "title": "Synthesis",      "description": "Pattern aggregation across observations. Regenerates SYNTHESIS.md for the idea proposer.", "active": True,  "url": "/synthesis"},
    {"name": "attribution",    "title": "Attribution",    "description": "UTM link generation and click tracking.",                          "active": True,  "url": "/attribution"},
    {"name": "content",        "title": "Content",        "description": "X post generator from observations. Multi-channel pipeline coming.", "active": False},
]

# The agent-fleet surfaces (fleet/*.html), mounted here by fleet/routes.py so
# the agent workflow lives on this same dashboard instead of a second server.
# They are parallel variations, not a hierarchy — the founder is running
# several to find which fit the workflow best.
FLEET_SURFACES = [
    {"title": "This Week",    "description": "Mission control for the weekly agent workflow: research, videos, distribute, measure.", "url": "/week"},
    {"title": "Reply Review", "description": "The reply queue as cards: read, edit, log what you sent.",                              "url": "/review"},
    {"title": "Fleet Ledger", "description": "Table window over the agents' ledger: runs, videos, decisions, outcome checks.",       "url": "/fleet"},
    {"title": "Fleet Map",    "description": "Agent roster, wiring, dispatcher, and signals.",                                       "url": "/map"},
    {"title": "Reply Kanban", "description": "The reply pipeline as columns.",                                                       "url": "/kanban"},
    {"title": "Flow Graph",   "description": "The weekly loop drawn as a graph.",                                                    "url": "/flow"},
]

# (db_column, form_label, html_kind, placeholder). Order matches the CLI prompts.
FIELDS = [
    ("date",                 "Date",                 "date",     "YYYY-MM-DD"),
    ("source",               "Source",               "text",     "reddit / discord / twitter / in-person / other"),
    ("source_detail",        "Source detail",        "text",     "e.g. r/Meditation, a Discord server"),
    ("handle",               "Handle",               "text",     "their username"),
    ("contact_info",         "Contact info",         "text",     "email / DM link / etc."),
    ("segment",              "Segment",              "text",     "tracker-anxious / practice-curious / practitioner, or free text"),
    ("experience_level",     "Experience level",     "text",     "never-practiced / on-and-off / daily-practice, or free text"),
    ("stated_goal",          "Stated goal",          "textarea", "what they said they want to achieve"),
    ("pain_points",          "Pain points",          "textarea", "frustrations, obstacles, unmet needs"),
    ("quotes",               "Quotes",               "textarea", "verbatim quotes worth preserving"),
    ("feature_implications", "Feature implications", "textarea", "what this suggests for the product"),
    ("downloaded_app",       "Downloaded app",       "text",     "yes / no / unknown, optionally with notes"),
    ("feedback_on_app",      "Feedback on app",      "textarea", ""),
    ("follow_up_status",     "Follow-up status",     "text",     "none / awaiting-reply / converted / lost, or free text"),
    ("notes",                "Notes",                "textarea", "anything else worth remembering"),
    ("tags",                 "Tags",                 "text",     "comma-separated, e.g. reddit,beginner,broker-question"),
]

# Slimmed field list for the paste workflow. raw_transcript is required; the rest mirror
# a subset of FIELDS so the same template loop handles both forms.
PASTE_FIELDS = [
    ("raw_transcript",   "Raw conversation", "textarea-big", "paste the whole thing — include both sides of the exchange"),
    ("date",             "Date",             "date",         "YYYY-MM-DD"),
    ("source",           "Source",           "text",         "reddit / discord / twitter / in-person / other"),
    ("source_detail",    "Source detail",    "text",         "e.g. r/investing, 'Trader Pit' server"),
    ("handle",           "Handle",           "text",         "their username"),
    ("tags",             "Tags",             "text",         "comma-separated, e.g. reddit,beginner"),
    ("follow_up_status", "Follow-up status", "text",         "none / awaiting-reply / converted / lost"),
]

# Observations form fields. source_url + raw_content are the core (threads I'm
# preserving); the rest are my interpretive take on what the thread shows.
OBSERVATION_FIELDS = [
    ("date_observed",        "Date observed",        "date",         "YYYY-MM-DD"),
    ("source",               "Source",               "text",         "reddit / twitter / youtube / forum / other"),
    ("source_detail",        "Source detail",        "text",         "e.g. r/investing, a channel name"),
    ("source_url",           "Source URL",           "text",         "direct link to the thread / post"),
    ("raw_content",          "Raw content",          "textarea-big", "paste the post + any replies worth preserving"),
    ("segment_guess",        "Segment guess",        "text",         "beginner / pre-retirement / young-professional / ..."),
    ("pain_points",          "Pain points",          "textarea",     "what this thread reveals about their struggles"),
    ("notable_quotes",       "Notable quotes",       "textarea",     "verbatim phrases worth keeping for content"),
    ("feature_implications", "Feature implications", "textarea",     "what this means for Fiboprana"),
    ("content_hooks",        "Content hooks",        "textarea",     "TikTok/X hook ideas sparked by this"),
    ("tags",                 "Tags",                 "text",         "comma-separated, e.g. reddit,beginner,broker-fees"),
]


# ---------- helpers ----------

def normalize_tags(raw):
    parts = [t.strip().lower() for t in (raw or "").split(",") if t.strip()]
    return ",".join(parts)


import re  # local import keeps the dependency footprint visible at use site

# "Obs #2", "Observation #11", "Observations #2, #10, #11, #13" — case-insensitive,
# tolerant of stray whitespace between # and digits. The regex matches the
# leading word plus the first id; downstream we re-scan for follow-on "#N"
# tokens so a single phrase like "Observations #2, #10, #11" yields all three.
_OBS_CITATION_RE = re.compile(r"\bObs(?:ervation)?s?\s*#\s*(\d+)", re.IGNORECASE)
_NEXT_HASH_NUM_RE = re.compile(r"#\s*(\d+)")


def parse_observation_citations(text):
    """Return a list of observation ids cited in `text`, in source order, deduped.

    Handles 'Obs #2', 'Observation #11', 'Observations #2, #10, #11, #13' (each
    follow-on #N within ~25 chars of the previous match is attributed to the
    same citation group).
    """
    if not text:
        return []
    seen = set()
    ordered = []
    for m in _OBS_CITATION_RE.finditer(text):
        first_id = int(m.group(1))
        if first_id not in seen:
            seen.add(first_id)
            ordered.append(first_id)
        # Look for additional comma/space-separated #N tokens immediately after.
        tail_start = m.end()
        tail_end = min(len(text), tail_start + 80)
        tail = text[tail_start:tail_end]
        # Stop scanning once we hit a sentence break or another word.
        cutoff = re.search(r"[.;:]|\b(?:Obs|Observation|SYNTHESIS|Pattern|Tension)\b", tail, re.IGNORECASE)
        if cutoff:
            tail = tail[:cutoff.start()]
        for nm in _NEXT_HASH_NUM_RE.finditer(tail):
            nid = int(nm.group(1))
            if nid not in seen:
                seen.add(nid)
                ordered.append(nid)
    return ordered


def link_idea_to_observations(conn, idea_id, source_text):
    """Refresh product_idea_observations rows for an idea from its source text.

    Clears existing links first so re-running is idempotent and edits propagate.
    Silently skips observation ids that don't exist (AI sometimes cites ids
    outside the current corpus, or numbers that aren't observation refs).
    """
    ids = parse_observation_citations(source_text)
    conn.execute("DELETE FROM product_idea_observations WHERE idea_id = :id", {"id": idea_id})
    if not ids:
        return 0
    existing = {
        r["id"] for r in conn.execute(
            f"SELECT id FROM observations WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
    }
    rows = [(idea_id, oid) for oid in ids if oid in existing]
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO product_idea_observations (idea_id, observation_id) VALUES (?, ?)",
            rows,
        )
    return len(rows)


def search_snippet(text, query, window=80):
    """Center an HTML-safe snippet around the first case-insensitive match.

    Returns a Markup wrapping the matched phrase in <mark>; rest is escaped.
    Empty Markup when no match. window = chars of context on each side.
    """
    from markupsafe import Markup, escape
    if not text or not query:
        return Markup("")
    lowered = text.lower()
    q = query.lower()
    idx = lowered.find(q)
    if idx < 0:
        return Markup("")
    start = max(0, idx - window)
    end = min(len(text), idx + len(query) + window)
    fragment = text[start:end]
    rel = fragment.lower().find(q)
    parts = [
        Markup("…") if start > 0 else Markup(""),
        escape(fragment[:rel]),
        Markup("<mark>"),
        escape(fragment[rel:rel + len(query)]),
        Markup("</mark>"),
        escape(fragment[rel + len(query):]),
        Markup("…") if end < len(text) else Markup(""),
    ]
    return Markup("").join(parts)


def collect_existing_tags(limit=40):
    """Return [(tag, count)] across conversations + observations, frequency desc.

    Used to render suggestion chips below the tags input on capture/observation
    forms so the same tag gets reused instead of typed as a near-miss (e.g.
    `broker-fees` vs `broker-fee`). Cap at `limit` to keep the chip strip
    scannable.
    """
    conn = get_connection()
    ensure_schema(conn)
    counts = {}
    for table in ("conversations", "observations"):
        rows = conn.execute(
            f"SELECT tags FROM {table} WHERE tags IS NOT NULL AND tags != ''"
        ).fetchall()
        for r in rows:
            for t in (r["tags"] or "").split(","):
                t = t.strip().lower()
                if t:
                    counts[t] = counts.get(t, 0) + 1
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]


# Host substrings -> canonical source value. Match is on a normalized hostname,
# so "old.reddit.com" / "www.reddit.com" / "redd.it" all collapse to 'reddit'.
_SOURCE_HOST_HINTS = [
    ("reddit.com", "reddit"),
    ("redd.it",    "reddit"),
    ("twitter.com", "twitter"),
    ("x.com",       "twitter"),
    ("youtube.com", "youtube"),
    ("youtu.be",    "youtube"),
    ("tiktok.com",  "tiktok"),
    ("linkedin.com", "linkedin"),
    ("discord.com", "discord"),
    ("discord.gg",  "discord"),
]


def infer_source_from_url(url):
    """Return a canonical source string for a URL, or '' if unrecognized.

    Lightweight host-substring match — no URL parsing dep, no validation.
    Used both at write time (so submitted observations get a source even
    when the form field is blank) and for backfill of legacy rows.
    """
    if not url:
        return ""
    lowered = url.lower()
    for hint, canonical in _SOURCE_HOST_HINTS:
        if hint in lowered:
            return canonical
    return ""


def build_list_query(args):
    """Return (sql, params, echoed_filters) for the /capture list page."""
    filters = {
        "tag":       (args.get("tag") or "").strip(),
        "source":    (args.get("source") or "").strip(),
        "segment":   (args.get("segment") or "").strip(),
        "follow_up": (args.get("follow_up") or "").strip(),
        "date_from": (args.get("from") or "").strip(),
        "date_to":   (args.get("to") or "").strip(),
        "limit":     (args.get("limit") or "20").strip(),
    }
    try:
        limit = max(1, min(500, int(filters["limit"])))
    except ValueError:
        limit = 20
    filters["limit"] = str(limit)

    where, params = [], {}
    if filters["tag"]:
        where.append("tags LIKE :tag")
        params["tag"] = f"%{filters['tag'].lower()}%"
    if filters["source"]:
        where.append("source = :source")
        params["source"] = filters["source"]
    if filters["segment"]:
        where.append("segment = :segment")
        params["segment"] = filters["segment"]
    if filters["follow_up"]:
        where.append("follow_up_status = :follow_up")
        params["follow_up"] = filters["follow_up"]
    if filters["date_from"]:
        where.append("date >= :date_from")
        params["date_from"] = filters["date_from"]
    if filters["date_to"]:
        where.append("date <= :date_to")
        params["date_to"] = filters["date_to"]

    sql = "SELECT * FROM conversations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY date DESC, id DESC LIMIT {limit}"
    return sql, params, filters


def build_observations_query(args):
    """Return (sql, params, echoed_filters) for the /observations list page."""
    filters = {
        "tag":       (args.get("tag") or "").strip(),
        "source":    (args.get("source") or "").strip(),
        "segment":   (args.get("segment") or "").strip(),
        "date_from": (args.get("from") or "").strip(),
        "date_to":   (args.get("to") or "").strip(),
        "limit":     (args.get("limit") or "20").strip(),
    }
    try:
        limit = max(1, min(500, int(filters["limit"])))
    except ValueError:
        limit = 20
    filters["limit"] = str(limit)

    where, params = [], {}
    if filters["tag"]:
        where.append("tags LIKE :tag")
        params["tag"] = f"%{filters['tag'].lower()}%"
    if filters["source"]:
        where.append("source = :source")
        params["source"] = filters["source"]
    if filters["segment"]:
        where.append("segment_guess = :segment")
        params["segment"] = filters["segment"]
    if filters["date_from"]:
        where.append("date_observed >= :date_from")
        params["date_from"] = filters["date_from"]
    if filters["date_to"]:
        where.append("date_observed <= :date_to")
        params["date_to"] = filters["date_to"]

    sql = "SELECT * FROM observations"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY date_observed DESC, id DESC LIMIT {limit}"
    return sql, params, filters


def build_ideas_query(args):
    """Return (sql, params, echoed_filters) for the /ideas list page."""
    filters = {
        "status":      (args.get("status") or "").strip(),
        "proposed_by": (args.get("proposed_by") or "").strip(),
        "tag":         (args.get("tag") or "").strip(),
        "limit":       (args.get("limit") or "50").strip(),
    }
    try:
        limit = max(1, min(500, int(filters["limit"])))
    except ValueError:
        limit = 50
    filters["limit"] = str(limit)

    where, params = [], {}
    if filters["status"] in ("idea", "approved", "rejected"):
        where.append("status = :status")
        params["status"] = filters["status"]
    if filters["proposed_by"] in ("manual", "ai"):
        where.append("proposed_by = :proposed_by")
        params["proposed_by"] = filters["proposed_by"]
    if filters["tag"]:
        where.append("tags LIKE :tag")
        params["tag"] = f"%{filters['tag'].lower()}%"

    sql = "SELECT * FROM product_ideas"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY created_at DESC, id DESC LIMIT {limit}"
    return sql, params, filters


def _run_guardrails_check_and_serialize(title, description):
    """Run a guardrails check and return (json_string, iso_timestamp).

    Wraps every failure mode into a status='error' result. The caller stores
    the JSON string verbatim in product_ideas.guardrails_check_result.
    """
    result = check_idea_against_guardrails(
        title, description, model=GUARDRAILS_CHECK_MODEL
    )
    return json.dumps(result), datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _parse_gr_result(raw):
    """Best-effort parse of a stored guardrails_check_result JSON string."""
    if not raw:
        return {"status": None, "rules_violated": [], "explanation": ""}
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
    except (json.JSONDecodeError, ValueError):
        return {
            "status": "error",
            "rules_violated": [],
            "explanation": "Stored guardrails result was malformed.",
        }
    return {
        "status": data.get("status"),
        "rules_violated": data.get("rules_violated") or [],
        "explanation": data.get("explanation") or "",
    }


def _idea_to_markdown(row, gr_result):
    """Render an idea row as the copy-as-markdown clipboard format.

    Conditionally omits empty source / tags / explanation lines per spec.
    The Guardrails check line is omitted only when status is None (defensive
    — every saved idea has a result via the routes).
    """
    parts = [f"## {row['title']}", "", row["description"]]

    source = (row["source"] or "").strip()
    if source:
        parts += ["", f"**Source:** {source}"]

    tags = (row["tags"] or "").strip()
    if tags:
        parts += ["", f"**Tags:** {tags}"]

    status = gr_result.get("status")
    if status == "ok":
        parts += ["", "**Guardrails check:** ok"]
    elif status == "conflict":
        rules = ", ".join(gr_result.get("rules_violated") or [])
        parts += ["", f"**Guardrails check:** conflict (rules violated: {rules})"]
        explanation = (gr_result.get("explanation") or "").strip()
        if explanation:
            parts += ["", explanation]
    elif status == "error":
        parts += ["", "**Guardrails check:** error (check did not complete)"]

    return "\n".join(parts)


# ---------- routes ----------

def _safe_external_url(url):
    """Whitelist http(s) URLs for use in <a href> on the dashboard.

    The attention queue surfaces URLs scraped from external sources (Reddit,
    Exa, HN). A malformed or hostile stored value like 'javascript:...' would
    otherwise become a clickable script URL. Anything that isn't http/https
    is dropped to '#'; templates can also detect '#' to suppress the link.
    """
    if not url:
        return "#"
    s = url.strip().lower()
    if s.startswith("http://") or s.startswith("https://"):
        return url
    return "#"


def _build_attention_queue():
    """Sections shown atop the dashboard, one per module with actionable items.

    Each section returns {title, count, url, label?, items[]}. Sections with
    zero items are omitted entirely so the panel only shows live work.
    """
    sections = []

    # Sourcing (Reddit) -- status='new'
    s_conn = sourcing_db.get_connection()
    sourcing_db.ensure_schema(s_conn)
    s_count = s_conn.execute(
        "SELECT COUNT(*) FROM seen_posts WHERE status = 'new'"
    ).fetchone()[0]
    if s_count:
        rows = s_conn.execute(
            "SELECT subreddit, title, url, score FROM seen_posts "
            "WHERE status = 'new' "
            "ORDER BY COALESCE(score, 0) DESC, posted_at DESC LIMIT 3"
        ).fetchall()
        sections.append({
            "title": "Sourcing",
            "count": s_count,
            "url": url_for("sourcing.sourcing_list"),
            "label": "Reddit candidates",
            "entries": [{
                "label": r["title"] or "(no title)",
                "meta": f"r/{r['subreddit']} · {r['score'] or 0}↑",
                "url": _safe_external_url(r["url"]),
            } for r in rows],
        })

    # Web sourcing -- status='new'
    w_conn = sourcing_web_db.get_connection()
    sourcing_web_db.ensure_schema(w_conn)
    w_count = w_conn.execute(
        "SELECT COUNT(*) FROM web_results WHERE status = 'new'"
    ).fetchone()[0]
    if w_count:
        rows = w_conn.execute(
            "SELECT title, url, query_label, query, exa_score FROM web_results "
            "WHERE status = 'new' "
            "ORDER BY COALESCE(exa_score, 0) DESC LIMIT 3"
        ).fetchall()
        sections.append({
            "title": "Web Sourcing",
            "count": w_count,
            "url": url_for("sourcing_web.sourcing_web_list"),
            "label": "Exa results",
            "entries": [{
                "label": r["title"] or "(no title)",
                "meta": r["query_label"] or r["query"] or "",
                "url": _safe_external_url(r["url"]),
            } for r in rows],
        })

    # Radar -- status='new'
    r_conn = radar_db.get_connection()
    radar_db.ensure_schema(r_conn)
    r_count = r_conn.execute(
        "SELECT COUNT(*) FROM radar_items WHERE status = 'new'"
    ).fetchone()[0]
    if r_count:
        rows = r_conn.execute(
            "SELECT title, url, source, source_label FROM radar_items "
            "WHERE status = 'new' "
            "ORDER BY COALESCE(published_at, first_seen_at) DESC LIMIT 3"
        ).fetchall()
        sections.append({
            "title": "Radar",
            "count": r_count,
            "url": url_for("radar.radar_list"),
            "label": "niche signal",
            "entries": [{
                "label": r["title"] or "(no title)",
                "meta": " · ".join(p for p in (r["source"], r["source_label"]) if p),
                "url": _safe_external_url(r["url"]),
            } for r in rows],
        })

    # Capture-db-backed sections (product_ideas + conversations live here).
    c_conn = get_connection()
    ensure_schema(c_conn)

    # Product ideas awaiting review
    i_count = c_conn.execute(
        "SELECT COUNT(*) FROM product_ideas WHERE status = 'idea'"
    ).fetchone()[0]
    if i_count:
        rows = c_conn.execute(
            "SELECT id, title, proposed_by FROM product_ideas "
            "WHERE status = 'idea' ORDER BY created_at DESC LIMIT 3"
        ).fetchall()
        sections.append({
            "title": "Product Ideas",
            "count": i_count,
            "url": url_for("ideas.ideas_list"),
            "label": "awaiting review",
            "entries": [{
                "label": r["title"],
                "meta": "AI proposed" if r["proposed_by"] == "ai" else "manual",
                "url": url_for("ideas.ideas_detail", idea_id=r["id"]),
            } for r in rows],
        })

    # Build queue -- features mid-pipeline (WORKFLOW.md Step 4 -> 5). Only the
    # in-flight stages; 'live' and 'content-instead' are closed outcomes. Most
    # advanced stage first so the nearest-to-shipping work sits on top.
    b_count = c_conn.execute(
        "SELECT COUNT(*) FROM product_ideas "
        "WHERE build_stage IN ('captured', 'gated', 'building')"
    ).fetchone()[0]
    if b_count:
        rows = c_conn.execute(
            "SELECT id, title, build_stage, target_query FROM product_ideas "
            "WHERE build_stage IN ('captured', 'gated', 'building') "
            "ORDER BY CASE build_stage "
            "  WHEN 'building' THEN 0 WHEN 'gated' THEN 1 ELSE 2 END, "
            "updated_at DESC LIMIT 3"
        ).fetchall()
        sections.append({
            "title": "Build Queue",
            "count": b_count,
            "url": url_for("ideas.ideas_list"),
            "label": "features mid-build",
            "entries": [{
                "label": r["title"],
                "meta": " · ".join(p for p in (r["build_stage"], r["target_query"]) if p),
                "url": url_for("ideas.ideas_detail", idea_id=r["id"]),
            } for r in rows],
        })

    # Captures awaiting reply (all, oldest first per user pref)
    a_count = c_conn.execute(
        "SELECT COUNT(*) FROM conversations WHERE follow_up_status = 'awaiting-reply'"
    ).fetchone()[0]
    if a_count:
        rows = c_conn.execute(
            "SELECT id, handle, source, date, follow_up_status_changed_at "
            "FROM conversations WHERE follow_up_status = 'awaiting-reply' "
            "ORDER BY COALESCE(follow_up_status_changed_at, date) ASC, id ASC LIMIT 3"
        ).fetchall()
        today = date.today()
        items = []
        for r in rows:
            # Prefer the status-change timestamp (real clock) over the conversation
            # date (a proxy that doesn't track when the conversation actually went
            # quiet). Fall back to date for legacy rows that predate the column.
            ref = (r["follow_up_status_changed_at"] or "")[:10] or r["date"]
            try:
                age = (today - date.fromisoformat(ref)).days
                age_label = "today" if age <= 0 else f"{age}d ago"
            except (ValueError, TypeError):
                age_label = ref or "?"
            items.append({
                "label": r["handle"] or "(no handle)",
                "meta": " · ".join(p for p in (r["source"], age_label) if p),
                "url": url_for("capture.capture_detail", entry_id=r["id"]),
            })
        sections.append({
            "title": "Captures",
            "count": a_count,
            "url": url_for("capture.capture_list", follow_up="awaiting-reply"),
            "label": "awaiting reply",
            "entries": items,
        })

    # Synthesis staleness -- new observations since last run
    synth_conn = synthesis_db.get_connection()
    synthesis_db.ensure_schema(synth_conn)
    latest = synthesis_runner.latest_run(synth_conn)
    total_obs = synthesis_analyzer.observation_count(c_conn)
    if latest:
        drift = total_obs - latest["observation_count"]
        last_run_date = (latest["generated_at"] or "")[:10]
        stale_label = f"new since last run on {last_run_date}"
    else:
        drift = total_obs
        stale_label = "synthesis never run"
    if drift > 0:
        sections.append({
            "title": "Synthesis",
            "count": drift,
            "url": url_for("synthesis.synthesis_dashboard"),
            "label": stale_label,
            "entries": [],
        })

    return sections


def _build_synthesis_snapshot():
    """Latest synthesis run summarized for the dashboard.

    Returns {run_id, generated_date, observation_count, patterns[]} or None.
    Patterns are parsed from the run's stored markdown: ### entries that
    fall under the '## Top recurring patterns' h2 section. Each pattern
    keeps its title and the 'N of M' frequency line if present.
    """
    conn = synthesis_db.get_connection()
    synthesis_db.ensure_schema(conn)
    latest = synthesis_runner.latest_run(conn)
    if not latest:
        return None

    md = latest["synthesis_md"] or ""
    patterns = []
    in_patterns_section = False
    current = None
    for line in md.splitlines():
        if line.startswith("## "):
            in_patterns_section = (line.strip().lower() == "## top recurring patterns")
            if current:
                patterns.append(current)
                current = None
            continue
        if not in_patterns_section:
            continue
        if line.startswith("### "):
            if current:
                patterns.append(current)
            title = line[4:].strip()
            # Strip leading numbering like "1. " or "1) "
            if title[:1].isdigit():
                for sep in (". ", ") "):
                    if sep in title[:4]:
                        title = title.split(sep, 1)[1].strip()
                        break
            current = {"title": title, "frequency": None}
        elif current and current["frequency"] is None and line.lstrip().startswith("**Frequency:**"):
            # Pull the bolded count: "**Frequency:** Appears in **12 of 18** observations."
            text = line.split("**Frequency:**", 1)[1].strip()
            current["frequency"] = text.rstrip(".")
    if current:
        patterns.append(current)

    return {
        "run_id": latest["id"],
        "generated_date": (latest["generated_at"] or "")[:10],
        "observation_count": latest["observation_count"],
        "patterns": patterns[:5],
        "total_patterns": len(patterns),
    }


@app.route("/")
def dashboard():
    queue = _build_attention_queue()
    synthesis = _build_synthesis_snapshot()
    return render_template(
        "dashboard.html", modules=MODULES, queue=queue, synthesis=synthesis,
        fleet_surfaces=FLEET_SURFACES,
    )


@app.route("/weekly")
def weekly_hub():
    """Hub for the weekly content system — a separate section from the existing
    per-module marketing tools, which stay exactly as they are. Lanes link out
    from here as they're built."""
    return render_template("weekly.html")


@app.route("/runbook")
def runbook():
    """Render WEEKLY_RUNBOOK.md as the in-dashboard operating procedure.

    Preformatted (no markdown-to-HTML dependency) — the same stdlib-friendly
    choice as the synthesis view. A missing file is a soft error, not a crash.
    """
    try:
        content = (ROOT / "WEEKLY_RUNBOOK.md").read_text(encoding="utf-8")
        error = None
    except OSError as e:
        content, error = "", f"Couldn't read WEEKLY_RUNBOOK.md: {e}"
    return render_template("runbook.html", content=content, error=error)


# Columns scanned per table for /search. First column with a hit becomes the
# snippet source, so order them by usefulness (richer narrative first, tags last).
_SEARCH_OBS_COLS = [
    "raw_content", "pain_points", "notable_quotes", "feature_implications",
    "content_hooks", "segment_guess", "tags",
]
_SEARCH_CONV_COLS = [
    "stated_goal", "pain_points", "quotes", "feature_implications",
    "feedback_on_app", "notes", "raw_transcript", "handle", "tags",
]
_SEARCH_IDEA_COLS = ["description", "title", "source", "tags"]


def _search_table(conn, table, cols, primary_cols, query, like_pattern):
    """Run a LIKE-OR scan against `cols`, return rows with all `primary_cols`
    plus everything in `cols` (so snippet-building can iterate). The caller
    picks the snippet column."""
    where = " OR ".join(f"{c} LIKE :q" for c in cols)
    select_cols = list(dict.fromkeys(primary_cols + cols))  # de-dupe, preserve order
    sql = (
        f"SELECT {', '.join(select_cols)} FROM {table} "
        f"WHERE {where} ORDER BY id DESC"
    )
    return conn.execute(sql, {"q": like_pattern}).fetchall()


def _first_snippet(row, cols, query):
    """First non-empty highlighted snippet across `cols`, or empty Markup."""
    for c in cols:
        s = search_snippet(row[c], query)
        if s:
            return s
    from markupsafe import Markup
    return Markup("")


@app.route("/tags/<tag>")
def tag_detail(tag):
    """Drill-down: every observation / capture / idea using a given tag.

    Word-boundary match is done in SQL by padding tags with commas on both
    sides and comparing ',<tag>,' — keeps 'broker' from matching 'broker-fees'.
    """
    tag = (tag or "").strip().lower()
    if not tag:
        abort(404)
    # Escape SQL LIKE wildcards so a tag of '%' or '_' can't overmatch every
    # row. The ESCAPE clause below tells SQLite to treat backslash as a
    # literal escape character.
    escaped = tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%,{escaped},%"
    conn = get_connection()
    ensure_schema(conn)
    observations = conn.execute(
        "SELECT id, date_observed, source, segment_guess, pain_points "
        "FROM observations WHERE ',' || COALESCE(tags, '') || ',' LIKE :p ESCAPE '\\' "
        "ORDER BY id DESC",
        {"p": pattern},
    ).fetchall()
    captures = conn.execute(
        "SELECT id, date, source, handle, stated_goal, pain_points "
        "FROM conversations WHERE ',' || COALESCE(tags, '') || ',' LIKE :p ESCAPE '\\' "
        "ORDER BY id DESC",
        {"p": pattern},
    ).fetchall()
    ideas = conn.execute(
        "SELECT id, title, status, proposed_by, created_at "
        "FROM product_ideas WHERE ',' || COALESCE(tags, '') || ',' LIKE :p ESCAPE '\\' "
        "ORDER BY id DESC",
        {"p": pattern},
    ).fetchall()
    total = len(observations) + len(captures) + len(ideas)
    return render_template(
        "tag_detail.html",
        tag=tag,
        observations=observations,
        captures=captures,
        ideas=ideas,
        total=total,
    )


@app.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    results = []
    counts = {"observation": 0, "capture": 0, "idea": 0}

    if q:
        conn = get_connection()
        ensure_schema(conn)
        like = f"%{q}%"

        for row in _search_table(
            conn, "observations", _SEARCH_OBS_COLS,
            ["id", "date_observed"], q, like,
        ):
            title = f"Obs #{row['id']}"
            if row["segment_guess"]:
                title += f" — {row['segment_guess']}"
            results.append({
                "kind": "observation",
                "url": url_for("observations.observations_detail", obs_id=row["id"]),
                "title": title,
                "date": row["date_observed"],
                "snippet": _first_snippet(row, _SEARCH_OBS_COLS, q),
            })

        for row in _search_table(
            conn, "conversations", _SEARCH_CONV_COLS,
            ["id", "date", "source"], q, like,
        ):
            title = f"Capture #{row['id']}"
            if row["handle"]:
                title += f" — {row['handle']}"
            results.append({
                "kind": "capture",
                "url": url_for("capture.capture_detail", entry_id=row["id"]),
                "title": title,
                "date": row["date"],
                "snippet": _first_snippet(row, _SEARCH_CONV_COLS, q),
            })

        for row in _search_table(
            conn, "product_ideas", _SEARCH_IDEA_COLS,
            ["id", "created_at"], q, like,
        ):
            results.append({
                "kind": "idea",
                "url": url_for("ideas.ideas_detail", idea_id=row["id"]),
                "title": f"Idea #{row['id']} — {row['title']}",
                "date": (row["created_at"] or "")[:10],
                "snippet": _first_snippet(row, _SEARCH_IDEA_COLS, q),
            })

        for r in results:
            counts[r["kind"]] += 1

    return render_template("search.html", q=q, results=results, counts=counts)


# ---------- observations routes ----------

# ---------- blueprint registrations ----------
# Per-module routes live in <module>/routes.py. Importing here (at the
# bottom of app.py) means every helper/constant above is already defined
# by the time routes.py tries to `from app import ...`.
from attribution.routes import bp as attribution_bp  # noqa: E402
from capture.routes import bp as capture_bp  # noqa: E402

app.register_blueprint(attribution_bp)
app.register_blueprint(capture_bp)
from sourcing.routes      import bp as sourcing_bp        # noqa: E402
from sourcing_web.routes  import bp as sourcing_web_bp    # noqa: E402
from content.routes       import bp as content_bp         # noqa: E402
from ideas.routes         import bp as ideas_bp           # noqa: E402
from radar.routes         import bp as radar_bp           # noqa: E402
from synthesis.routes     import bp as synthesis_bp       # noqa: E402
from observations.routes  import bp as observations_bp    # noqa: E402
from swipe.routes         import bp as swipe_bp           # noqa: E402
from fleet.routes         import bp as fleet_bp           # noqa: E402

app.register_blueprint(sourcing_bp)
app.register_blueprint(sourcing_web_bp)
app.register_blueprint(content_bp)
app.register_blueprint(ideas_bp)
app.register_blueprint(radar_bp)
app.register_blueprint(synthesis_bp)
app.register_blueprint(observations_bp)
app.register_blueprint(swipe_bp)
app.register_blueprint(fleet_bp)



if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fiboprana marketing dashboard.")
    parser.add_argument(
        "--network",
        action="store_true",
        help="Bind to 0.0.0.0 so phones on the same WiFi can reach the app. "
             "Disables debug mode for safety. Default: bind to 127.0.0.1 only.",
    )
    args = parser.parse_args()

    # debug=True + host=0.0.0.0 would expose the Werkzeug in-browser debugger
    # (arbitrary code execution) to anyone on the LAN. Tie them together so the
    # two states are localhost+debug (dev) or LAN+no-debug (phone access).
    host = "0.0.0.0" if args.network else "127.0.0.1"
    debug = not args.network
    app.run(host=host, port=5000, debug=debug)
