"""Video outcome sync â€” stamps each published video's first-7-day numbers.

Ported from the personal-brand workflow 2026-08-23 (its outcome-sync +
packaging-ctr-sync agents, both battle-tested there). Two legs, one daily run:

  1. OUTCOMES (YouTube Analytics API): for every published marketing.videos
     row, pull the video's first-7-day window (views, minutes watched, average
     view duration, subs gained, likes, comments) and write it into the row's
     outcome_metrics jsonb. Fixed window published_at .. +6d, capped at
     yesterday (the API lags ~2 days); a partial window is stamped as such and
     re-read daily until complete, then never touched again. The founder's
     outcome_verdict / outcome_note stay his â€” this fills only the numbers.

  2. THUMBNAIL CTR (YouTube Reporting API, report channel_reach_basic_a1):
     daily per-video thumbnail impressions + impressions-CTR. First run
     creates the reporting job (data starts flowing ~2 days later); after
     that, each run downloads the recent daily reports, computes the
     impression-weighted first-7-day CTR per video, merges it into the same
     outcome_metrics, and regenerates videos/templates/thumbnail_ctr.md â€” the
     auto-table the thumbnail style ledger has been waiting on ("CTR pending"
     since round 1). CTR is the missing judge for the style rounds.

Both legs need the 2026-08-23 scope upgrade (yt-analytics.readonly â€” see
fleet/youtube_auth.py). Until the founder re-runs the bootstrap once, this
agent reports the missing grant plainly and exits without guessing.

No LLM calls â€” pure mechanics. Records its own heartbeat.

    python -m fleet.video_outcomes            # measure + stamp + regenerate
    python -m fleet.video_outcomes --dry-run  # read + report, write nothing

Scheduled: daily via fleet/dispatch.py (runs wherever the YouTube OAuth env
vars exist â€” today that's the founder's PC; skips honestly elsewhere).
"""

import argparse
import csv
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from fleet import store, supabase  # noqa: E402
from fleet.supabase import SupabaseError  # noqa: E402

AGENT_NAME = "video_outcomes"
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
REPORTING_URL = "https://youtubereporting.googleapis.com/v1"
REPORT_TYPE = "channel_reach_basic_a1"
JOB_NAME = "fiboprana-reach"
WINDOW_DAYS = 7
LOOKBACK_DAYS = 40   # how far back to download daily reach reports
CTR_TABLE = ROOT / "videos" / "templates" / "thumbnail_ctr.md"
ANALYTICS_METRICS = ("views,estimatedMinutesWatched,averageViewDuration,"
                     "subscribersGained,likes,comments")


class ScopeError(RuntimeError):
    """The refresh token predates the analytics scopes â€” founder re-consents."""


def _api(url, token, body=None):
    headers = {"Authorization": f"Bearer {token}"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:300]
        if e.code == 403 and ("insufficient" in detail.lower()
                              or "PERMISSION_DENIED" in detail):
            raise ScopeError(
                "the YouTube token lacks the analytics scopes - re-run "
                "`python -m fleet.youtube_auth` and consent once "
                "(scope upgrade 2026-08-23)") from None
        raise RuntimeError(f"{url.split('?')[0]} -> HTTP {e.code}: {detail}")
    if url.startswith(REPORTING_URL) and "/media/" in url:
        return raw  # report downloads are CSV bytes, not JSON
    return json.loads(raw or b"{}")


# â”€â”€ leg 1: first-7-day outcomes via the Analytics API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def measurable_videos():
    """Published rows still owed a complete first-7-day stamp."""
    rows = supabase.select("videos", params={
        "select": "id,slug,title_final,youtube_video_id,published_at,outcome_metrics",
        "status": "eq.published",
        "youtube_video_id": "not.is.null",
        "published_at": "not.is.null",
        "order": "published_at.desc",
        "limit": 50,
    })
    now = datetime.now(timezone.utc)
    due = []
    for r in rows:
        if (r.get("outcome_metrics") or {}).get("window_complete"):
            continue
        published = datetime.fromisoformat(r["published_at"].replace("Z", "+00:00"))
        if published > now:
            continue  # scheduled, not yet live
        r["_published"] = published
        due.append(r)
    return due


def analytics_window(token, video_id, published):
    """(metrics dict, window_days, complete) for the first-7-day window,
    capped at yesterday because Analytics data lags ~2 days."""
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    window_end = min(published + timedelta(days=WINDOW_DAYS - 1), yesterday)
    if window_end < published:
        return None, 0, False  # published today - nothing measurable yet
    window_days = (window_end.date() - published.date()).days + 1
    q = urllib.parse.urlencode({
        "ids": "channel==MINE",
        "startDate": published.date().isoformat(),
        "endDate": window_end.date().isoformat(),
        "metrics": ANALYTICS_METRICS,
        "filters": f"video=={video_id}",
    })
    payload = _api(f"{ANALYTICS_URL}?{q}", token)
    row = (payload.get("rows") or [[0] * 6])[0]
    views, minutes, avg_dur, subs, likes, comments = (
        [float(v or 0) for v in row] + [0] * 6)[:6]
    return {
        "views_7d": int(views),
        "minutes_watched_7d": int(minutes),
        "avg_view_duration_s": int(avg_dur),
        "subs_gained_7d": int(subs),
        "likes_7d": int(likes),
        "comments_7d": int(comments),
    }, window_days, window_days >= WINDOW_DAYS


# â”€â”€ leg 2: thumbnail impressions + CTR via the Reporting API â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def ensure_reach_job(token):
    """Find the channel_reach_basic_a1 reporting job, creating it on first
    run. Returns (job_id, created_now)."""
    jobs = _api(f"{REPORTING_URL}/jobs", token).get("jobs") or []
    for j in jobs:
        if j.get("reportTypeId") == REPORT_TYPE:
            return j["id"], False
    job = _api(f"{REPORTING_URL}/jobs", token,
               body={"reportTypeId": REPORT_TYPE, "name": JOB_NAME})
    return job["id"], True


def reach_by_video(token, job_id):
    """Map video_id -> {date: {imp, clicks}} from the recent daily reports."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)
    reports, page_token = [], None
    while True:
        q = {"pageSize": 60}
        if page_token:
            q["pageToken"] = page_token
        payload = _api(f"{REPORTING_URL}/jobs/{job_id}/reports?"
                       + urllib.parse.urlencode(q), token)
        for r in payload.get("reports") or []:
            start = datetime.fromisoformat(r["startTime"].replace("Z", "+00:00"))
            if start >= cutoff:
                reports.append(r)
        page_token = payload.get("nextPageToken")
        if not page_token:
            break
    data = {}
    for r in reports:
        raw = _api(r["downloadUrl"], token)
        rows = csv.reader(io.StringIO(raw.decode("utf-8", errors="replace")))
        header = next(rows, None) or []
        try:
            i_date = header.index("date")
            i_vid = header.index("video_id")
            i_imp = header.index("video_thumbnail_impressions")
            i_ctr = header.index("video_thumbnail_impressions_ctr")
        except ValueError:
            continue  # unexpected layout - skip this file, never guess columns
        for row in rows:
            if len(row) <= max(i_vid, i_imp, i_ctr):
                continue
            vid, day = row[i_vid], row[i_date]
            imp = float(row[i_imp] or 0)
            per_day = data.setdefault(vid, {}).setdefault(
                day, {"imp": 0.0, "clicks": 0.0})
            per_day["imp"] += imp
            per_day["clicks"] += imp * float(row[i_ctr] or 0)
    return data


def ctr_for_window(reach, video_id, published):
    """(impressions, ctr_pct, days_of_data) over the first-7-day window, or
    None when the video has no reach rows yet."""
    by_day = reach.get(video_id)
    if not by_day:
        return None
    start = published.date()
    end = start + timedelta(days=WINDOW_DAYS - 1)
    imp = clicks = 0.0
    days = 0
    for day, v in by_day.items():
        d = datetime.strptime(day, "%Y%m%d").date()
        if start <= d <= end:
            imp += v["imp"]
            clicks += v["clicks"]
            days += 1
    if not imp:
        return None
    return int(imp), round(clicks / imp * 100, 2), days


def write_ctr_table(rows):
    """Regenerate the auto-table the thumbnail style ledger reads. rows =
    [(title, video_id, published_date, impressions, ctr_pct, note)]."""
    lines = [
        "# Thumbnail CTR â€” auto-generated, do not hand-edit",
        "",
        "> Written daily by `python -m fleet.video_outcomes` from the YouTube",
        "> Reporting API (first-7-day window, impression-weighted). This is the",
        "> click data `thumbnail_styles.md` has been waiting on: join a row to",
        "> its batch by the video, then log the verdict in the ledger as usual.",
        "",
        "| Video | Published | Impressions (7d) | CTR (7d) | Note |",
        "|---|---|---|---|---|",
    ]
    for title, vid, pub, imp, ctr, note in rows:
        lines.append(f"| {title} (`{vid}`) | {pub} | {imp:,} | {ctr}% | {note} |")
    if not rows:
        lines.append("| (no videos with reach data yet) | | | | |")
    CTR_TABLE.write_text("\n".join(lines) + "\n", encoding="utf-8")


# â”€â”€ the run â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()

    try:
        from fleet.youtube_auth import access_token
        token = access_token()
    except Exception as e:  # noqa: BLE001 - absent creds skip honestly
        msg = f"skipped: no YouTube credential here ({e})"
        print(msg)
        if not args.dry_run:
            store.record_heartbeat(agent_name=AGENT_NAME, status="success",
                                   message=msg)
        return 0

    stamped, waiting, notes = 0, [], []
    try:
        due = measurable_videos()

        # The CTR leg degrades to a note instead of killing the outcomes leg
        # (e.g. Reporting API not yet enabled in the Google Cloud project).
        reach = {}
        try:
            job_id, created = ensure_reach_job(token)
            if created:
                notes.append("reach reporting job created - CTR data starts "
                             "flowing in ~2 days")
            else:
                reach = reach_by_video(token, job_id)
        except ScopeError:
            raise
        except RuntimeError as e:
            notes.append(f"CTR leg skipped: {e}")

        ctr_rows = []
        for video in due:
            vid = video["youtube_video_id"]
            published = video["_published"]
            metrics, window_days, complete = analytics_window(
                token, vid, published)
            if metrics is None:
                waiting.append(f"{video['slug']} (published today)")
                continue
            metrics.update({
                "window_days": window_days,
                "window_complete": complete,
                "measured_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"),
                "measured_by": AGENT_NAME,
            })
            ctr = ctr_for_window(reach, vid, published)
            if ctr:
                imp, ctr_pct, days = ctr
                metrics["impressions_7d"] = imp
                metrics["ctr_7d_pct"] = ctr_pct
                metrics["ctr_days_of_data"] = days
                ctr_rows.append((video.get("title_final") or video["slug"], vid,
                                 published.date().isoformat(), imp, ctr_pct,
                                 "complete" if complete
                                 else f"{window_days}d so far"))
            else:
                waiting.append(f"{video['slug']} (no reach data yet)")
            if args.dry_run:
                print(f"would stamp {video['slug']}: {metrics}")
            else:
                supabase.update("videos", {"id": video["id"]},
                                {"outcome_metrics": metrics})
            stamped += 1
        if ctr_rows and not args.dry_run:
            write_ctr_table(ctr_rows)
    except ScopeError as e:
        print(f"blocked: {e}")
        if not args.dry_run:
            store.record_heartbeat(agent_name=AGENT_NAME, status="failure",
                                   message=str(e))
        return 1
    except (SupabaseError, RuntimeError, urllib.error.URLError) as e:
        print(f"failed: {e}")
        if not args.dry_run:
            store.record_heartbeat(agent_name=AGENT_NAME, status="failure",
                                   message=str(e)[:300])
        return 1

    summary = (f"stamped {stamped} video(s)"
               + (f"; waiting on: {', '.join(waiting)}" if waiting else "")
               + ("; " + "; ".join(notes) if notes else ""))
    print(summary)
    if not args.dry_run:
        store.record_heartbeat(
            agent_name=AGENT_NAME, status="success", message=summary,
            duration_ms=int((time.monotonic() - started) * 1000))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
