"""Flask blueprint for the sourcing module (Reddit candidate triage)."""

from flask import Blueprint, abort, redirect, render_template, request, url_for

from sourcing import db as sourcing_db
from sourcing.config import load_config as load_sourcing_config
from sourcing.reddit_client import SourcingConfigError, get_reddit_client
from sourcing import scanner as sourcing_scanner


bp = Blueprint("sourcing", __name__)

# Single-user localhost app: a module-level dict for the last scan summary
# is good enough and avoids needing Flask sessions / secret_key setup.
_LAST_SCAN_SUMMARY = {"summary": None, "error": None}
_LAST_WEB_SCAN_SUMMARY = {"summary": None, "error": None}


@bp.route("/sourcing")
def sourcing_list():
    conn = sourcing_db.get_connection()
    sourcing_db.ensure_schema(conn)

    subreddit_filter = (request.args.get("subreddit") or "").strip()
    where, params = ["status = 'new'"], {}
    if subreddit_filter:
        where.append("subreddit = :subreddit")
        params["subreddit"] = subreddit_filter

    sql = (
        "SELECT * FROM seen_posts WHERE "
        + " AND ".join(where)
        + " ORDER BY score DESC, posted_at DESC"
    )
    rows = conn.execute(sql, params).fetchall()

    # Consume-and-clear the last scan summary so refreshing the page doesn't
    # keep showing stale numbers.
    summary = _LAST_SCAN_SUMMARY.pop("summary", None)
    error = _LAST_SCAN_SUMMARY.pop("error", None)
    _LAST_SCAN_SUMMARY["summary"] = None
    _LAST_SCAN_SUMMARY["error"] = None

    return render_template(
        "sourcing/list.html",
        rows=rows,
        subreddit_filter=subreddit_filter,
        scan_summary=summary,
        scan_error=error,
        view="new",
    )


@bp.route("/sourcing/saved")
def sourcing_saved():
    conn = sourcing_db.get_connection()
    sourcing_db.ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM seen_posts WHERE status = 'saved_later' "
        "ORDER BY score DESC, posted_at DESC"
    ).fetchall()
    return render_template("sourcing/saved.html", rows=rows, view="saved")


@bp.route("/sourcing/scan", methods=["POST"])
def sourcing_scan():
    conn = sourcing_db.get_connection()
    sourcing_db.ensure_schema(conn)

    try:
        reddit = get_reddit_client()
    except SourcingConfigError as e:
        _LAST_SCAN_SUMMARY["error"] = str(e)
        return redirect(url_for("sourcing.sourcing_list"))

    try:
        config = load_sourcing_config()
    except Exception as e:  # noqa: BLE001
        _LAST_SCAN_SUMMARY["error"] = f"Couldn't load sourcing/config.yaml: {e}"
        return redirect(url_for("sourcing.sourcing_list"))

    try:
        summary = sourcing_scanner.scan_subreddits(reddit, config, conn)
    except Exception as e:  # noqa: BLE001
        name = type(e).__name__
        if "TooManyRequests" in name:
            _LAST_SCAN_SUMMARY["error"] = "Reddit rate limit hit — wait a few minutes and try again."
        elif "ResponseException" in name or "401" in str(e) or "403" in str(e):
            _LAST_SCAN_SUMMARY["error"] = f"Reddit credentials rejected — check .env. ({e})"
        else:
            _LAST_SCAN_SUMMARY["error"] = f"Scan failed: {e}"
        return redirect(url_for("sourcing.sourcing_list"))

    _LAST_SCAN_SUMMARY["summary"] = summary
    return redirect(url_for("sourcing.sourcing_list"))


def _update_status(reddit_id, new_status):
    conn = sourcing_db.get_connection()
    sourcing_db.ensure_schema(conn)
    cur = conn.execute(
        "UPDATE seen_posts SET status = :status, status_changed_at = datetime('now') "
        "WHERE reddit_id = :id",
        {"status": new_status, "id": reddit_id},
    )
    conn.commit()
    return cur.rowcount


@bp.route("/sourcing/<reddit_id>/skip", methods=["POST"])
def sourcing_skip(reddit_id):
    if _update_status(reddit_id, "skipped") == 0:
        abort(404)
    return redirect(request.referrer or url_for("sourcing.sourcing_list"))


@bp.route("/sourcing/<reddit_id>/save-later", methods=["POST"])
def sourcing_save_later(reddit_id):
    if _update_status(reddit_id, "saved_later") == 0:
        abort(404)
    return redirect(request.referrer or url_for("sourcing.sourcing_list"))


@bp.route("/sourcing/<reddit_id>/extract", methods=["GET"])
def sourcing_extract(reddit_id):
    conn = sourcing_db.get_connection()
    sourcing_db.ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM seen_posts WHERE reddit_id = :id", {"id": reddit_id}
    ).fetchone()
    if row is None:
        abort(404)

    try:
        reddit = get_reddit_client()
    except SourcingConfigError as e:
        _LAST_SCAN_SUMMARY["error"] = str(e)
        return redirect(url_for("sourcing.sourcing_list"))

    try:
        raw_content = sourcing_scanner.fetch_thread_markdown(reddit, reddit_id, row["subreddit"])
    except Exception as e:  # noqa: BLE001
        _LAST_SCAN_SUMMARY["error"] = f"Couldn't fetch thread {reddit_id}: {e}"
        return redirect(url_for("sourcing.sourcing_list"))

    _update_status(reddit_id, "extracted")

    return redirect(url_for(
        "observations.observations_new_form",
        raw_content=raw_content,
        source_url=row["url"],
        source="reddit",
        source_detail=f"r/{row['subreddit']}",
    ))
