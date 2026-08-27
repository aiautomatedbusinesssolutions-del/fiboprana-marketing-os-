"""The outcome checker — the fleet's first measure agent (weekly, mechanical).

Closes the loop the founder has been closing by hand: for every sent reply
still missing an outcome, go read the numbers and write them to the ledger.

  * Reddit — free via the official API with a one-time script-app credential
    (reddit.com/prefs/apps -> "script" app; REDDIT_CLIENT_ID / SECRET /
    USERNAME / PASSWORD in .env). The anonymous /.json mirror is bot-walled
    as of 2026-07-22 (www + api 403, old.reddit serves an HTML wall; RSS
    still works but carries no scores), so the API is the honest door.
    A row with no reply_url isn't stuck: the checker fetches the POST's
    thread, finds our comment by matching final_sent, saves the recovered
    permalink back to the ledger, and proceeds — zero manual steps.
  * X — free via Typefully's analytics API, which reports the whole X
    account's posts AND replies (hand-posted ones too, not just drafts it
    published) with impressions / likes / replies / profile_clicks and the
    live tweet URL. So one read covers metrics, led_to_profile, and missing
    reply_url recovery — no pay-per-use X API involved. Uses TYPEFULLY_API_KEY
    (+ TYPEFULLY_SOCIAL_SET_ID) from the env, falling back to the Typefully
    CLI's global config (~/.config/typefully/config.json) locally.
    ZERO manual steps by design — never asks for X permalinks.

The agent writes raw facts only — outcome_metrics (score/replies/likes...) and
op_replied, source-labeled in the metrics — and lets update_outcome stamp
outcome_checked_at. Verdicts (outcome_score, strong/ok/off) stay the founder's:
same L1/L2 split as the research runs. Matching open outcome_checks queue rows
are closed with the same numbers so the nag list stays honest.

No LLM calls — pure mechanics, so a run costs ~nothing and the heartbeat's
token/cost columns stay honestly empty.

    python -m fleet.outcome_checker_agent            # check + write
    python -m fleet.outcome_checker_agent --dry-run  # read + report, write nothing

Scheduled: Saturdays via fleet/dispatch.py — the day before the Sunday research
run, so next week's research reads fresh outcomes.
"""

import argparse
import difflib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # local dev; in the cloud the platform injects env vars

from radar.sources.rss import USER_AGENT  # noqa: E402 — the UA reddit accepts
from fleet import asset_store, reply_store, supabase  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402

AGENT_NAME = "outcome_checker"
MIN_AGE_HOURS = 48          # let a reply breathe before reading its numbers
TYPEFULLY_API = "https://api.typefully.com/v2"
MATCH_RATIO = 0.90          # difflib floor for final_sent <-> posted text
MAX_LOOKBACK_DAYS = 90      # analytics window cap (endpoint pages at 100 posts)


def _fetch_json(url, headers=None):
    # No Accept header: old.reddit.com 404s requests that send
    # "Accept: application/json" (verified + reproducible, 2026-07-22).
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


# ── Reddit: the official API (script app, password grant) ────────────────────
def _reddit_token():
    """OAuth token from the founder's script app, or None if creds aren't set.
    Note: password grant doesn't work with 2FA-enabled accounts."""
    cid = os.environ.get("REDDIT_CLIENT_ID")
    secret = os.environ.get("REDDIT_CLIENT_SECRET")
    user = os.environ.get("REDDIT_USERNAME")
    password = os.environ.get("REDDIT_PASSWORD")
    if not all([cid, secret, user, password]):
        return None
    import base64
    body = urllib.parse.urlencode({"grant_type": "password", "username": user,
                                   "password": password}).encode("utf-8")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    req = urllib.request.Request("https://www.reddit.com/api/v1/access_token",
                                 data=body,
                                 headers={"User-Agent": USER_AGENT,
                                          "Authorization": f"Basic {basic}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8")).get("access_token")


def _reddit_json(url, token):
    """A thread/comment page as JSON via oauth.reddit.com (same [post, comments]
    payload the old public /.json mirror returned)."""
    path = urllib.parse.urlparse(url.split("?")[0].rstrip("/")).path
    return _fetch_json(f"https://oauth.reddit.com{path}?raw_json=1",
                       headers={"Authorization": f"Bearer {token}"})


def _norm(text):
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _comment_outcome(comment, row):
    """(metrics, op_replied) from one reddit comment node."""
    replies = comment.get("replies")
    children = (replies["data"]["children"] if isinstance(replies, dict) else [])
    direct = [c["data"] for c in children if c.get("kind") == "t1"]
    op = (row.get("post_author") or "").lstrip("u/").lower()
    op_replied = "yes" if any((d.get("author") or "").lower() == op and op
                              for d in direct) else "no"
    metrics = {"score": comment.get("score"), "replies": len(direct),
               "source": "reddit_json", "checked_by": AGENT_NAME}
    return metrics, op_replied


def _check_reddit(row, token):
    """Read the reply's numbers off its permalink thread."""
    payload = _reddit_json(row["reply_url"], token)
    # A comment permalink returns [post_listing, comment_listing]; my comment is
    # the first child of the second listing.
    comment = payload[1]["data"]["children"][0]["data"]
    return _comment_outcome(comment, row)


def _walk_comments(children):
    """Yield every t1 comment node in a listing, depth-first."""
    for child in children or []:
        if child.get("kind") != "t1":
            continue
        data = child["data"]
        yield data
        replies = data.get("replies")
        if isinstance(replies, dict):
            yield from _walk_comments(replies["data"]["children"])


def _find_reply_on_post(row, token):
    """No permalink? Recover it: fetch the POST's thread and find our comment by
    matching final_sent. Returns (comment, permalink) or (None, None)."""
    payload = _reddit_json(row["post_url"], token)
    target = _norm(row["final_sent"])
    best, best_ratio = None, 0.0
    for comment in _walk_comments(payload[1]["data"]["children"]):
        ratio = difflib.SequenceMatcher(None, target,
                                        _norm(comment.get("body"))).ratio()
        if ratio > best_ratio:
            best, best_ratio = comment, ratio
    if best is None or best_ratio < 0.85:  # markdown vs typed text tolerance
        return None, None
    return best, f"https://www.reddit.com{best.get('permalink', '')}"


# ── X: read outcomes off Typefully's account analytics ───────────────────────
def _typefully_creds():
    """(api_key, social_set_id) from the env, else the Typefully CLI's global
    config — or (None, None) when neither is set."""
    key = os.environ.get("TYPEFULLY_API_KEY")
    sid = os.environ.get("TYPEFULLY_SOCIAL_SET_ID")
    if not (key and sid):
        cfg_path = Path.home() / ".config" / "typefully" / "config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            key = key or cfg.get("apiKey")
            sid = sid or cfg.get("defaultSocialSetId")
    if not (key and sid):
        return None, None
    return key, str(sid)


def _typefully_posts(days_back):
    """Every X post+reply on the account for the window, with metrics and the
    live tweet URL. Returns None when no Typefully key is configured."""
    key, social_set = _typefully_creds()
    if key is None:
        return None
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=min(days_back, MAX_LOOKBACK_DAYS))
    q = urllib.parse.urlencode({"start_date": start.isoformat(),
                                "end_date": today.isoformat(),
                                "include_replies": "true", "limit": 100})
    url = f"{TYPEFULLY_API}/social-sets/{social_set}/analytics/x/posts?{q}"
    payload = _fetch_json(url, headers={"Authorization": f"Bearer {key}"})
    return payload.get("results") or []


def _strip_handles(text):
    """Normalized text minus @mentions — Typefully previews prefix the reply
    handles, which the ledger's final_sent never contains."""
    return _norm(" ".join(w for w in (text or "").split()
                          if not w.startswith("@")))


def _match_post(final_sent, posts):
    """Best analytics match for a sent reply, or None. Compares handle-stripped
    text truncated to the shorter side (previews are cut off), with autojunk
    OFF — difflib's autojunk heuristic tanks ratios on ~280-char strings."""
    target = _strip_handles(final_sent)
    best, best_ratio = None, 0.0
    for p in posts:
        cand = _strip_handles(p.get("preview_text"))
        n = min(len(target), len(cand))
        if n == 0:
            continue
        ratio = difflib.SequenceMatcher(None, target[:n], cand[:n],
                                        autojunk=False).ratio()
        if ratio > best_ratio:
            best, best_ratio = p, ratio
    return best if best_ratio >= MATCH_RATIO else None


def _typefully_metrics(post):
    m = post.get("metrics") or {}
    e = m.get("engagement") or {}
    return {"impressions": m.get("impressions"), "likes": e.get("likes"),
            "replies": e.get("comments"), "reposts": e.get("shares"),
            "quotes": e.get("quotes"), "saves": e.get("saves"),
            "profile_clicks": e.get("profile_clicks"),
            "source": "typefully", "checked_by": AGENT_NAME,
            "post_id": post.get("post_id")}


# ── The run ──────────────────────────────────────────────────────────────────
def _close_queue_checks(ledger_id, metrics, *, source):
    """Close open outcome_checks rows pointing at this ledger row, with the
    same numbers — one read of reality, two ledgers kept honest. DUE rows only:
    closing a 7d window with day-2 numbers would mislabel the data, and the
    daily metrics agent closes each window on its actual due date anyway."""
    now = datetime.now(timezone.utc).isoformat()
    open_rows = supabase.select(asset_store.OUTCOME_CHECKS, params={
        "select": "id", "entity_table": "eq.reply_ledger",
        "entity_id": f"eq.{ledger_id}", "status": "eq.open",
        "due_at": f"lte.{now}"})
    for check in open_rows:
        asset_store.close_check(check["id"], result=metrics, source=source)
    return len(open_rows)


def run_check_videos():
    """The video leg (added 2026-08-07): close due `videos` outcome checks with
    stats read straight off the YouTube API — views/likes/comments as of the
    check moment, source-labeled yt_api. A window closed late carries
    days_late so a 24h number recorded on day 12 can't masquerade as a real
    24h read. Rows without a youtube_video_id are skipped for a human.
    Zero LLM calls, zero manual steps."""
    from fleet import youtube_auth

    now = datetime.now(timezone.utc)
    try:
        due = [c for c in asset_store.due_checks(limit=100)
               if c["entity_table"] == "videos"]
    except SupabaseError as e:
        return {"ok": False, "closed": 0, "skipped": 0, "details": [],
                "error": f"could not read the checks queue: {e}"}
    if not due:
        return {"ok": True, "closed": 0, "skipped": 0, "details": []}

    vid_ids = sorted({c["entity_id"] for c in due})
    rows = supabase.select("videos", params={
        "select": "id,youtube_video_id,slug",
        "id": f"in.({','.join(vid_ids)})"})
    by_row = {r["id"]: r for r in rows}
    yt_ids = sorted({r["youtube_video_id"] for r in rows
                     if r.get("youtube_video_id")})
    stats = {}
    if yt_ids:
        try:
            got = youtube_auth.api_get("videos", part="statistics",
                                       id=",".join(yt_ids))
            stats = {v["id"]: v.get("statistics", {})
                     for v in got.get("items", [])}
        except Exception as e:  # noqa: BLE001 — API down = skip all, close none
            return {"ok": False, "closed": 0, "skipped": len(due), "details": [],
                    "error": f"YouTube stats fetch failed: {e}"}

    closed, skipped, details = 0, 0, []
    for c in due:
        row = by_row.get(c["entity_id"]) or {}
        name = row.get("slug") or c["entity_id"][:8]
        st = stats.get(row.get("youtube_video_id"))
        if st is None:
            skipped += 1
            details.append(f"skip {name} {c['window_label']}: no youtube id / stats")
            continue
        days_late = round((now - datetime.fromisoformat(
            c["due_at"].replace("Z", "+00:00"))).total_seconds() / 86400, 1)
        result = {"views": int(st.get("viewCount") or 0),
                  "likes": int(st["likeCount"]) if st.get("likeCount") else None,
                  "comments": int(st["commentCount"]) if st.get("commentCount") else None}
        if days_late > 0.25:
            result["days_late"] = days_late
        asset_store.close_check(c["id"], result=result, source="yt_api")
        closed += 1
        late = f" ({days_late}d late)" if days_late > 0.25 else ""
        details.append(f"closed {name} {c['window_label']}: "
                       f"{result['views']} views{late}")
    return {"ok": True, "closed": closed, "skipped": skipped, "details": details}


def run_check_outcomes(*, dry_run=False, min_age_hours=MIN_AGE_HOURS):
    """Check every awaiting reply it can, write what it read, report the rest.
    Returns {ok, checked, skipped, details[], error}."""
    try:
        rows = reply_store.awaiting_outcomes(min_age_hours=min_age_hours)
    except SupabaseError as e:
        return {"ok": False, "checked": 0, "skipped": 0, "details": [],
                "error": f"could not read the ledger: {e}"}

    x_rows = [r for r in rows if r["platform"] == "x"]
    posts, x_error = None, None
    if x_rows:
        try:
            oldest = min(datetime.fromisoformat(r["replied_at"])
                         for r in x_rows)
            days_back = (datetime.now(timezone.utc) - oldest).days + 2
            posts = _typefully_posts(days_back)
        except Exception as e:  # noqa: BLE001 — Typefully down must not block Reddit
            x_error = f"Typefully analytics fetch failed: {e}"

    reddit_token, reddit_error = None, None
    if any(r["platform"] == "reddit" for r in rows):
        try:
            reddit_token = _reddit_token()
        except Exception as e:  # noqa: BLE001 — bad creds must not block X
            reddit_error = f"Reddit auth failed: {e}"

    checked = skipped = 0
    details = []
    for row in rows:
        label = f"[{row['platform']}] {(row.get('post_title') or row['final_sent'])[:60]}"
        try:
            if row["platform"] == "reddit":
                if reddit_token is None:
                    skipped += 1
                    details.append(f"SKIP {label} — "
                                   + (reddit_error or "REDDIT_* creds not set "
                                      "(free script app — see module docstring)"))
                    continue
                recovered = None
                if row.get("reply_url"):
                    metrics, op_replied = _check_reddit(row, reddit_token)
                else:
                    comment, recovered = _find_reply_on_post(row, reddit_token)
                    if comment is None:
                        skipped += 1
                        details.append(f"SKIP {label} — no permalink, and no "
                                       "matching comment found on the post")
                        continue
                    metrics, op_replied = _comment_outcome(comment, row)
                if not dry_run:
                    if recovered:
                        reply_store.set_reply_url(row["id"], recovered)
                    reply_store.update_outcome(row["id"], op_replied=op_replied,
                                               outcome_metrics=metrics)
                    _close_queue_checks(row["id"], metrics, source="reddit_json")
                checked += 1
                details.append(f"OK   {label} — score {metrics['score']}, "
                               f"{metrics['replies']} replies, OP replied: {op_replied}"
                               + (" (permalink recovered)" if recovered else ""))
            elif row["platform"] == "x":
                if x_error:
                    skipped += 1
                    details.append(f"SKIP {label} — {x_error}")
                    continue
                if posts is None:
                    skipped += 1
                    details.append(f"SKIP {label} — no Typefully key "
                                   "(TYPEFULLY_API_KEY env or the CLI's "
                                   "global config)")
                    continue
                post = _match_post(row["final_sent"], posts)
                if post is None:
                    skipped += 1
                    details.append(f"SKIP {label} — no match among "
                                   f"{len(posts)} analytics posts")
                    continue
                metrics = _typefully_metrics(post)
                clicks = metrics.get("profile_clicks")
                if not dry_run:
                    if not row.get("reply_url") and post.get("url"):
                        reply_store.set_reply_url(row["id"], post["url"])
                    reply_store.update_outcome(
                        row["id"], outcome_metrics=metrics,
                        led_to_profile=(None if clicks is None
                                        else "yes" if clicks else "no"))
                    _close_queue_checks(row["id"], metrics, source="typefully")
                checked += 1
                details.append(f"OK   {label} — {metrics.get('impressions') or 0} "
                               f"impressions, {metrics.get('likes') or 0} likes, "
                               f"{metrics.get('replies') or 0} replies, "
                               f"{clicks or 0} profile clicks")
            else:
                skipped += 1
                details.append(f"SKIP {label} — no checker for this platform yet")
        except (urllib.error.URLError, SupabaseError, KeyError, IndexError,
                ValueError) as e:
            skipped += 1
            details.append(f"SKIP {label} — {e}")

    return {"ok": True, "checked": checked, "skipped": skipped,
            "details": details, "error": x_error}


def format_for_chat(result):
    lines = [f"Outcome check: {result['checked']} checked, "
             f"{result['skipped']} skipped."]
    lines += result["details"]
    if result.get("error"):
        lines.append(f"(note: {result['error']})")
    if not result["ok"]:
        lines.append(f"Run failed: {result.get('error')}")
    return "\n".join(lines)


def main():
    import time

    from fleet import store

    parser = argparse.ArgumentParser(description="Weekly reply-outcome checker.")
    parser.add_argument("--dry-run", action="store_true",
                        help="read + report, write nothing")
    args = parser.parse_args()

    started = time.monotonic()
    result = run_check_outcomes(dry_run=args.dry_run)
    print(format_for_chat(result))
    videos = {"ok": True, "closed": 0, "skipped": 0}
    if not args.dry_run:
        videos = run_check_videos()
        print(f"videos: closed {videos['closed']}, skipped {videos['skipped']}"
              + (f" | {videos['error']}" if videos.get("error") else ""))
        for line in videos.get("details", []):
            print(" ", line)

    if not args.dry_run:  # a dry run isn't a heartbeat-worthy run
        try:
            store.record_heartbeat(
                agent_name=AGENT_NAME,
                status="success" if (result["ok"] and videos["ok"]) else "failure",
                message=(f"checked {result['checked']}, skipped {result['skipped']}"
                         f" | videos closed {videos['closed']}"),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        except Exception as e:  # noqa: BLE001
            print(f"(warning: heartbeat write failed: {e})")
    return 0 if (result["ok"] and videos["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
