"""The metrics agent — the fleet's daily snapshot collector (mechanical).

The deterministic-toolbox rule, applied to COLLECTION: this agent reads real
numbers from real APIs and writes them down verbatim, every day, whether or not
anything needs them yet. Snapshots are the one dataset you can't backfill —
if today's impressions weren't recorded today, that point on the curve is gone
forever. Storage is free; hindsight isn't.

What it snapshots into marketing.metric_snapshots (one row per entity per UTC
day; a same-day re-run merges, yesterday is never touched):

  * x_post        — EVERY post and reply on the account from Typefully's
                    analytics (the same free endpoint the outcome checker uses,
                    which was fetching this whole payload and keeping only the
                    matched reply rows). Raw payload verbatim — including
                    link_clicks, which nothing captured before.
  * x_account     — a rollup of that window (post count, impression/engagement
                    sums, publishing quota). NO follower count: Typefully's API
                    doesn't expose one (probed 2026-07-25 — /social-sets/{id}
                    has profile + quota only, no analytics/account endpoint).
                    When a deterministic source appears (X API, or a manual
                    weekly entry), it belongs here as another key in metrics.
  * reddit_account / reddit_comment — karma totals and per-comment scores via
                    the official API, when REDDIT_* creds are set (idle while
                    the account suspension appeal is pending; skips honestly).
  * youtube_channel / youtube_video — channel totals (views/subs/video count)
                    and per-video counts via the YouTube Data API using the
                    read-only OAuth in fleet/youtube_auth.py. Skips honestly
                    when the credential is absent — e.g. on Railway until the
                    GOOGLE_OAUTH_* env vars are copied there.
  * app_funnel    — signup-funnel counts (users, waitlist, quiz leads,
                    founding members) via marketing.funnel_counts(), the
                    SECURITY DEFINER counts-only bridge over the app tables'
                    RLS. Granted to the hermes role only, so this skips
                    honestly on plain-anon creds (set SUPABASE_HERMES_JWT).
  * gsc_site      — Search Console clicks/impressions/ctr/position + query
                    footprint, via the read-only OAuth in fleet/gsc_auth.py
                    (its own refresh token — separate from the YouTube write
                    token on purpose). THIS BUSINESS ONLY: snapshots just the
                    GSC_SITES allowlist (default fiboprana.com)
                    even when the credential sees other businesses' sites —
                    each business's fleet reads its own. GSC data lags, so
                    each snapshot covers the day three days back
                    (metrics.data_date); at near-zero traffic, impressions
                    and distinct queries ARE the signal. Skips honestly
                    until GSC_OAUTH_REFRESH_TOKEN is minted.

It also CLOSES due outcome_checks windows (reply_ledger + content_calendar)
whose entity it can match to today's X snapshots — by tweet status id out of
reply_url / external_url, or by Typefully draft id. That's what makes a "7d"
check hold day-7 numbers: this runs daily, so windows close ON their due date
with that day's fresh read, instead of waiting for the Saturday checker.

Division of labor with outcome_checker_agent (Saturdays): the checker stamps
each sent reply's FIRST outcome onto reply_ledger (outcome_metrics +
op_replied + permalink recovery) — the training-row read. This agent owns
history (every entity, every day) and the window queue. Both write raw facts
only; verdicts stay the founder's.

No LLM calls — pure mechanics, so a run costs ~nothing and the heartbeat's
token/cost columns stay honestly empty.

    python -m fleet.metrics_agent            # snapshot + close due windows
    python -m fleet.metrics_agent --dry-run  # read + report, write nothing

Scheduled: daily via fleet/dispatch.py, right after the checks sweep (checks
arms windows; this closes the ones that just came due).
"""

import argparse
import os
import re
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # local dev; in the cloud the platform injects env vars

from fleet import asset_store, supabase  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402
# Shared plumbing lives in the outcome checker (creds discovery, fetch, reddit
# auth) — one source of truth for how those doors open.
from fleet.outcome_checker_agent import (  # noqa: E402
    TYPEFULLY_API, _fetch_json, _reddit_token, _typefully_creds)

AGENT_NAME = "metrics"
SNAPSHOTS = "metric_snapshots"
WINDOW_DAYS = 28            # matches the longest outcome window (28d)
MAX_PAGES = 5               # pagination cap: 5 x 100 posts is months of headroom
_STATUS_ID_RE = re.compile(r"/status(?:es)?/(\d+)")


# ── X: the full Typefully analytics payload, kept this time ──────────────────
def _typefully_posts_all():
    """Every post+reply in the window, following pagination. Returns
    (posts, social_set_id) or (None, None) when no key is configured."""
    key, social_set = _typefully_creds()
    if key is None:
        return None, None
    today = datetime.now(timezone.utc).date()
    q = urllib.parse.urlencode({"start_date": (today - timedelta(days=WINDOW_DAYS)).isoformat(),
                                "end_date": today.isoformat(),
                                "include_replies": "true", "limit": 100})
    url = f"{TYPEFULLY_API}/social-sets/{social_set}/analytics/x/posts?{q}"
    posts = []
    for _ in range(MAX_PAGES):
        payload = _fetch_json(url, headers={"Authorization": f"Bearer {key}"})
        posts.extend(payload.get("results") or [])
        url = payload.get("next")
        if not url:
            break
    return posts, social_set


def _x_post_rows(posts):
    """One snapshot row per post — the raw analytics object, verbatim."""
    now = datetime.now(timezone.utc).isoformat()
    return [{"source": "typefully", "entity_kind": "x_post",
             "entity_key": str(p["post_id"]), "metrics": p, "captured_at": now}
            for p in posts if p.get("post_id")]


def _x_account_row(posts, social_set):
    """The window rollup + publishing quota. Followers deliberately absent —
    no deterministic free source yet (see module docstring)."""
    sums = {"impressions": 0, "likes": 0, "comments": 0, "shares": 0,
            "quotes": 0, "saves": 0, "profile_clicks": 0, "link_clicks": 0}
    for p in posts:
        m = p.get("metrics") or {}
        sums["impressions"] += m.get("impressions") or 0
        e = m.get("engagement") or {}
        for k in list(sums):
            if k != "impressions":
                sums[k] += e.get(k) or 0
    metrics = {"window_days": WINDOW_DAYS, "posts_in_window": len(posts), **sums}
    key, _ = _typefully_creds()
    try:  # quota is nice-to-have context; its failure shouldn't cost the rollup
        info = _fetch_json(f"{TYPEFULLY_API}/social-sets/{social_set}",
                           headers={"Authorization": f"Bearer {key}"})
        metrics["publishing_quota"] = info.get("publishing_quota")
        handle = ((info.get("platforms") or {}).get("x") or {}).get("username")
    except Exception:  # noqa: BLE001
        handle = None
    return {"source": "typefully", "entity_kind": "x_account",
            "entity_key": handle or "fiboprana", "metrics": metrics,
            "captured_at": datetime.now(timezone.utc).isoformat()}


# ── Reddit: karma + per-comment scores (idle until the account is back) ──────
def _reddit_rows():
    """(rows, note). Skips honestly when creds are missing or auth fails."""
    user = os.environ.get("REDDIT_USERNAME")
    try:
        token = _reddit_token()
    except Exception as e:  # noqa: BLE001
        return [], f"reddit auth failed: {e}"
    if token is None or not user:
        return [], "REDDIT_* creds not set — reddit snapshots inactive"

    headers = {"Authorization": f"Bearer {token}"}
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    me = _fetch_json("https://oauth.reddit.com/api/v1/me", headers=headers)
    rows.append({"source": "reddit_api", "entity_kind": "reddit_account",
                 "entity_key": me.get("name") or user, "captured_at": now,
                 "metrics": {k: me.get(k) for k in
                             ("link_karma", "comment_karma", "total_karma",
                              "created_utc")}})
    listing = _fetch_json(
        f"https://oauth.reddit.com/user/{user}/comments?limit=100&raw_json=1",
        headers=headers)
    for child in (listing.get("data") or {}).get("children") or []:
        d = child.get("data") or {}
        if not d.get("name"):
            continue
        rows.append({"source": "reddit_api", "entity_kind": "reddit_comment",
                     "entity_key": d["name"], "captured_at": now,
                     "metrics": {k: d.get(k) for k in
                                 ("score", "ups", "controversiality",
                                  "subreddit", "link_permalink", "permalink",
                                  "created_utc")}})
    return rows, None


# ── YouTube: channel totals + per-video counts (read-only OAuth) ─────────────
def _youtube_rows():
    """(rows, note). Skips honestly when the OAuth credential is missing —
    locally that's .env lines; on Railway the GOOGLE_OAUTH_* vars must be set."""
    try:
        from fleet.youtube_auth import api_get
        chan = api_get("channels", part="statistics,snippet", mine="true")
    except (RuntimeError, OSError, urllib.error.URLError, ValueError, KeyError) as e:
        return [], f"youtube snapshots skipped: {e}"

    now = datetime.now(timezone.utc).isoformat()
    rows = [{"source": "youtube_api", "entity_kind": "youtube_channel",
             "entity_key": c.get("id") or "fiboprana", "captured_at": now,
             "metrics": {"title": (c.get("snippet") or {}).get("title"),
                         **(c.get("statistics") or {})}}
            for c in chan.get("items") or []]

    ids, params = [], {"part": "id", "forMine": "true", "type": "video",
                       "maxResults": 50}
    for _ in range(MAX_PAGES):
        found = api_get("search", **params)
        ids += [i["id"]["videoId"] for i in found.get("items") or []
                if (i.get("id") or {}).get("videoId")]
        params["pageToken"] = found.get("nextPageToken")
        if not params["pageToken"]:
            break
    for start in range(0, len(ids), 50):
        stats = api_get("videos", part="statistics,snippet",
                        id=",".join(ids[start:start + 50]))
        for v in stats.get("items") or []:
            snip = v.get("snippet") or {}
            rows.append({"source": "youtube_api", "entity_kind": "youtube_video",
                         "entity_key": v["id"], "captured_at": now,
                         "metrics": {"title": snip.get("title"),
                                     "published_at": snip.get("publishedAt"),
                                     **(v.get("statistics") or {})}})
    return rows, None


# ── Search Console: search performance, THIS business's properties only ──────
GSC_DATA_LAG_DAYS = 3       # GSC finalizes data ~2-3 days back; query the safe edge
GSC_QUERY_ROW_CAP = 1000    # distinct-query footprint cap (plenty until real traffic)
GSC_TOP_QUERIES = 10
# Per-business boundary: this fleet is Fiboprana's, so it snapshots ONLY the
# sites below even if the Google credential can see more. Other businesses'
# fleets set their own GSC_SITES. Set GSC_SITES in .env once the fiboprana.com
# property is verified in Search Console under the credential gsc_auth.py uses
# (URL-prefix vs sc-domain: match whichever property form was verified).
GSC_SITES_DEFAULT = "https://fiboprana.com/"


def _gsc_rows():
    """(rows, note). One row per property in GSC_SITES (comma-separated env,
    default = Fiboprana's domain property), covering the single day
    GSC_DATA_LAG_DAYS back (snapshot_date stays today — the row records which
    day it covers in metrics.data_date). Notes honestly when the credential
    can't see a wanted site (wrong-account consent) or sees extras (ignored).
    Skips honestly when the GSC refresh token hasn't been minted yet."""
    wanted = [s.strip() for s in
              os.environ.get("GSC_SITES", GSC_SITES_DEFAULT).split(",")
              if s.strip()]
    try:
        from fleet.gsc_auth import search_analytics, sites
        visible = [p["siteUrl"] for p in sites()]
    except (RuntimeError, OSError, urllib.error.URLError, ValueError, KeyError) as e:
        return [], f"gsc snapshots skipped: {e}"

    missing = [s for s in wanted if s not in visible]
    ignored = [s for s in visible if s not in wanted]
    note_bits = []
    if missing:
        note_bits.append(f"gsc: credential can't see {missing} — "
                         "consented with the wrong Google account?")
    if ignored:
        note_bits.append(f"gsc: ignoring out-of-business properties {ignored}")

    day = (datetime.now(timezone.utc).date()
           - timedelta(days=GSC_DATA_LAG_DAYS)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    for site in [s for s in wanted if s in visible]:
        totals = (search_analytics(site, {
            "startDate": day, "endDate": day}).get("rows") or [{}])[0]
        queries = search_analytics(site, {
            "startDate": day, "endDate": day, "dimensions": ["query"],
            "rowLimit": GSC_QUERY_ROW_CAP}).get("rows") or []
        rows.append({
            "source": "gsc_api", "entity_kind": "gsc_site",
            "entity_key": site, "captured_at": now,
            "metrics": {
                "data_date": day,
                "clicks": totals.get("clicks", 0),
                "impressions": totals.get("impressions", 0),
                "ctr": totals.get("ctr", 0),
                "position": totals.get("position"),
                "distinct_queries": len(queries),
                "query_cap_hit": len(queries) >= GSC_QUERY_ROW_CAP,
                "top_queries": [
                    {"query": q["keys"][0], "clicks": q.get("clicks", 0),
                     "impressions": q.get("impressions", 0),
                     "position": q.get("position")}
                    for q in queries[:GSC_TOP_QUERIES]],
            }})
    return rows, ("; ".join(note_bits) or None)


# ── App funnel: signup counts over the RLS boundary, aggregates only ─────────
def _app_funnel_rows():
    """(rows, note). marketing.funnel_counts() is the counts-only SECURITY
    DEFINER bridge over the app tables' RLS; skips honestly until its
    migration is applied to the live project."""
    try:
        counts = supabase.rpc("funnel_counts")
    except SupabaseError as e:
        return [], f"app funnel skipped (funnel_counts migration not applied?): {e}"
    return [{"source": "app_db", "entity_kind": "app_funnel",
             "entity_key": "fiboprana", "metrics": counts,
             "captured_at": datetime.now(timezone.utc).isoformat()}], None


# ── Closing due windows with today's numbers ──────────────────────────────────
def _flat_metrics(post):
    """The flattened shape outcome_checks results use everywhere else."""
    m = post.get("metrics") or {}
    e = m.get("engagement") or {}
    return {"impressions": m.get("impressions"), "likes": e.get("likes"),
            "replies": e.get("comments"), "reposts": e.get("shares"),
            "quotes": e.get("quotes"), "saves": e.get("saves"),
            "profile_clicks": e.get("profile_clicks"),
            "link_clicks": e.get("link_clicks"),
            "source": "typefully", "checked_by": AGENT_NAME,
            "post_id": post.get("post_id")}


def _status_id(url):
    m = _STATUS_ID_RE.search(url or "")
    return m.group(1) if m else None


def _close_due_checks(posts, *, dry_run):
    """Close due open reply_ledger / content_calendar windows whose entity maps
    to a snapshotted tweet (exact status-id or Typefully draft-id match — no
    fuzzy text matching needed once a URL exists). Unmatched checks stay open
    and keep nagging: an honest hole beats a silent close."""
    by_post_id = {str(p["post_id"]): p for p in posts if p.get("post_id")}
    by_draft_id = {str(p["draft_id"]): p for p in posts if p.get("draft_id")}

    closed, details = 0, []
    due = [c for c in asset_store.due_checks(limit=200)
           if c["entity_table"] in ("reply_ledger", "content_calendar")]
    for table in ("reply_ledger", "content_calendar"):
        ids = [c["entity_id"] for c in due if c["entity_table"] == table]
        if not ids:
            continue
        cols = ("id,reply_url" if table == "reply_ledger"
                else "id,external_url,typefully_draft_id")
        rows = {r["id"]: r for r in supabase.select(table, params={
            "select": cols, "id": f"in.({','.join(ids)})"})}
        for check in due:
            if check["entity_table"] != table:
                continue
            row = rows.get(check["entity_id"]) or {}
            post = (by_post_id.get(_status_id(row.get("reply_url")
                                              or row.get("external_url")))
                    or by_draft_id.get(str(row.get("typefully_draft_id") or "")))
            if post is None:
                continue
            if not dry_run:
                asset_store.close_check(check["id"], result=_flat_metrics(post),
                                        source="typefully")
            closed += 1
            details.append(f"CLOSED {check['window_label']} [{table}] "
                           f"{(post.get('preview_text') or '')[:60]}")
    return closed, len(due), details


# ── The run ──────────────────────────────────────────────────────────────────
def run_snapshot(*, dry_run=False):
    """Snapshot everything readable, close what's due. Returns
    {ok, written, closed, due, details[], notes[]}."""
    details, notes = [], []
    rows = []

    posts, social_set = _typefully_posts_all()
    if posts is None:
        notes.append("no Typefully key (TYPEFULLY_API_KEY env or the CLI's "
                     "global config) — X snapshots skipped")
        posts = []
    else:
        rows += _x_post_rows(posts)
        rows.append(_x_account_row(posts, social_set))
        details.append(f"x: {len(posts)} posts in the {WINDOW_DAYS}d window "
                       "+ 1 account rollup")

    try:
        reddit_rows, note = _reddit_rows()
    except (urllib.error.URLError, ValueError, KeyError) as e:
        reddit_rows, note = [], f"reddit snapshot failed: {e}"
    if note:
        notes.append(note)
    if reddit_rows:
        rows += reddit_rows
        details.append(f"reddit: {len(reddit_rows) - 1} comments + karma")

    try:
        yt_rows, note = _youtube_rows()
    except (urllib.error.URLError, ValueError, KeyError) as e:
        yt_rows, note = [], f"youtube snapshot failed: {e}"
    if note:
        notes.append(note)
    if yt_rows:
        rows += yt_rows
        details.append(f"youtube: {len(yt_rows) - 1} videos + channel totals")

    try:
        gsc_rows, note = _gsc_rows()
    except (urllib.error.URLError, ValueError, KeyError) as e:
        gsc_rows, note = [], f"gsc snapshot failed: {e}"
    if note:
        notes.append(note)
    if gsc_rows:
        rows += gsc_rows
        details.append(f"gsc: {len(gsc_rows)} properties "
                       f"(data for {gsc_rows[0]['metrics']['data_date']})")

    funnel_rows, note = _app_funnel_rows()
    if note:
        notes.append(note)
    if funnel_rows:
        rows += funnel_rows
        details.append("app funnel: signup counts")

    written = 0
    if rows and not dry_run:
        try:
            written = len(supabase.upsert(
                SNAPSHOTS, rows,
                on_conflict="source,entity_kind,entity_key,snapshot_date"))
        except SupabaseError as e:
            return {"ok": False, "written": 0, "closed": 0, "due": 0,
                    "details": details, "notes": notes + [f"snapshot write failed: {e}"]}
    elif dry_run:
        written = len(rows)  # what WOULD be written

    try:
        closed, due, close_details = _close_due_checks(posts, dry_run=dry_run)
        details += close_details
    except SupabaseError as e:
        closed, due = 0, -1
        notes.append(f"window close pass failed: {e}")

    return {"ok": True, "written": written, "closed": closed, "due": due,
            "details": details, "notes": notes}


def format_for_chat(result):
    head = (f"Metric snapshots: {result['written']} rows, "
            f"closed {result['closed']} of {result['due']} due windows.")
    lines = [head] + result["details"]
    lines += [f"(note: {n})" for n in result["notes"]]
    if not result["ok"]:
        lines.append("Run failed — nothing written.")
    return "\n".join(lines)


def main():
    import time

    from fleet import store

    parser = argparse.ArgumentParser(description="Daily metric snapshot collector.")
    parser.add_argument("--dry-run", action="store_true",
                        help="read + report, write nothing")
    args = parser.parse_args()

    started = time.monotonic()
    result = run_snapshot(dry_run=args.dry_run)
    print(format_for_chat(result))

    if not args.dry_run:  # a dry run isn't a heartbeat-worthy run
        try:
            store.record_heartbeat(
                agent_name=AGENT_NAME,
                status="success" if result["ok"] else "failure",
                message=(f"{result['written']} snapshots, "
                         f"closed {result['closed']}/{result['due']} due"),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            print(f"(warning: heartbeat write failed: {e})")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
