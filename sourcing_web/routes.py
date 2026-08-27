"""Flask blueprint for the sourcing_web module (Exa web triage)."""

import json

from flask import Blueprint, abort, redirect, render_template, request, url_for

from sourcing_web import db as sourcing_web_db
from sourcing_web.config import load_config as load_sourcing_web_config
from sourcing_web.exa_client import SourcingWebConfigError, get_exa_client
from sourcing_web import scanner as sourcing_web_scanner
from sourcing_web import extractor as sourcing_web_extractor


bp = Blueprint("sourcing_web", __name__)

# Module-level scratch for the most-recent scan summary; consumed-and-cleared
# on the next list render so refreshing doesn't keep showing stale numbers.
_LAST_WEB_SCAN_SUMMARY = {"summary": None, "error": None}


def _web_parse_highlights(raw_json):
    """Parse the JSON-encoded highlights column. Returns list[str], [] on failure."""
    if not raw_json:
        return []
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    return [str(x) for x in data]


# Whitelist of ?sort= keys -> column names. Column names can't be parameter-bound
# in SQLite, so the whitelist is what keeps the ORDER BY splice safe.
_WEB_SORT_COLUMNS = {
    "query":     "query_label",
    "subreddit": "subreddit",
    "score":     "exa_score",
    "recency":   "first_seen_at",
}
_WEB_SORT_DEFAULTS = {"query": "asc", "subreddit": "asc", "score": "desc", "recency": "desc"}


def _web_sort_links(current_sort, current_order):
    """Per-column dict {col: {sort, order, indicator}} for the template's headers.
    Click on the currently-sorted column flips direction; click on any other
    column applies that column's natural default direction (alpha asc for text,
    desc for numeric/recency).
    """
    out = {}
    for col, default_dir in _WEB_SORT_DEFAULTS.items():
        if current_sort == col:
            new_dir = "desc" if current_order == "asc" else "asc"
            indicator = "↑" if current_order == "asc" else "↓"
        else:
            new_dir = default_dir
            indicator = ""
        out[col] = {"sort": col, "order": new_dir, "indicator": indicator}
    return out


def _web_rows_with_highlight(conn, status, sort_key=None, order=None):
    if sort_key in _WEB_SORT_COLUMNS:
        col = _WEB_SORT_COLUMNS[sort_key]
        direction = "ASC" if order == "asc" else "DESC"
        order_clause = f"ORDER BY {col} {direction} NULLS LAST, id DESC"
    else:
        order_clause = "ORDER BY first_seen_at DESC, id DESC"

    sql = f"SELECT * FROM web_results WHERE status = :s {order_clause}"
    raw = conn.execute(sql, {"s": status}).fetchall()
    rows = []
    for r in raw:
        d = dict(r)
        hl = _web_parse_highlights(d.get("highlights"))
        d["first_highlight"] = hl[0] if hl else ""
        d["json_for_copy"] = {
            "title": d.get("title"),
            "url": d.get("url"),
            "subreddit": d.get("subreddit"),
            "query": d.get("query"),
            "query_label": d.get("query_label"),
            "exa_score": d.get("exa_score"),
            "highlights": hl,
            "first_seen_at": d.get("first_seen_at"),
        }
        rows.append(d)
    return rows


@bp.route("/sourcing/web")
def sourcing_web_list():
    conn = sourcing_web_db.get_connection()
    sourcing_web_db.ensure_schema(conn)

    sort_key = (request.args.get("sort") or "").strip()
    order = (request.args.get("order") or "").strip()
    rows = _web_rows_with_highlight(conn, "new", sort_key, order)
    sort_links = _web_sort_links(sort_key, order)

    summary = _LAST_WEB_SCAN_SUMMARY.pop("summary", None)
    error = _LAST_WEB_SCAN_SUMMARY.pop("error", None)
    _LAST_WEB_SCAN_SUMMARY["summary"] = None
    _LAST_WEB_SCAN_SUMMARY["error"] = None

    return render_template(
        "sourcing_web/list.html",
        rows=rows,
        scan_summary=summary,
        scan_error=error,
        sort_links=sort_links,
    )


@bp.route("/sourcing/web/saved")
def sourcing_web_saved():
    conn = sourcing_web_db.get_connection()
    sourcing_web_db.ensure_schema(conn)
    rows = _web_rows_with_highlight(conn, "saved_later")
    return render_template("sourcing_web/saved.html", rows=rows)


@bp.route("/sourcing/web/scan", methods=["POST"])
def sourcing_web_scan():
    conn = sourcing_web_db.get_connection()
    sourcing_web_db.ensure_schema(conn)

    try:
        exa = get_exa_client()
    except SourcingWebConfigError as e:
        _LAST_WEB_SCAN_SUMMARY["error"] = str(e)
        return redirect(url_for("sourcing_web.sourcing_web_list"))

    try:
        config = load_sourcing_web_config()
    except Exception as e:  # noqa: BLE001
        _LAST_WEB_SCAN_SUMMARY["error"] = f"Couldn't load sourcing_web/config.yaml: {e}"
        return redirect(url_for("sourcing_web.sourcing_web_list"))

    mode = (request.form.get("mode") or "templates").strip()
    try:
        if mode == "adhoc":
            query = (request.form.get("query") or "").strip()
            if not query:
                _LAST_WEB_SCAN_SUMMARY["error"] = "Ad-hoc query was empty."
                return redirect(url_for("sourcing_web.sourcing_web_list"))
            summary = sourcing_web_scanner.scan_with_query(exa, config, conn, query)
        else:
            summary = sourcing_web_scanner.scan_with_templates(exa, config, conn)
    except Exception as e:  # noqa: BLE001
        _LAST_WEB_SCAN_SUMMARY["error"] = f"Scan failed: {e}"
        return redirect(url_for("sourcing_web.sourcing_web_list"))

    _LAST_WEB_SCAN_SUMMARY["summary"] = summary
    return redirect(url_for("sourcing_web.sourcing_web_list"))


def _update_web_status(result_id, new_status):
    conn = sourcing_web_db.get_connection()
    sourcing_web_db.ensure_schema(conn)
    cur = conn.execute(
        "UPDATE web_results SET status = :status, status_changed_at = datetime('now') "
        "WHERE id = :id",
        {"status": new_status, "id": result_id},
    )
    conn.commit()
    return cur.rowcount


@bp.route("/sourcing/web/<int:result_id>/skip", methods=["POST"])
def sourcing_web_skip(result_id):
    if _update_web_status(result_id, "skipped") == 0:
        abort(404)
    return redirect(request.referrer or url_for("sourcing_web.sourcing_web_list"))


@bp.route("/sourcing/web/<int:result_id>/save-later", methods=["POST"])
def sourcing_web_save_later(result_id):
    if _update_web_status(result_id, "saved_later") == 0:
        abort(404)
    return redirect(request.referrer or url_for("sourcing_web.sourcing_web_list"))


@bp.route("/sourcing/web/<int:result_id>/extract", methods=["GET"])
def sourcing_web_extract(result_id):
    conn = sourcing_web_db.get_connection()
    sourcing_web_db.ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM web_results WHERE id = :id", {"id": result_id}
    ).fetchone()
    if row is None:
        abort(404)

    try:
        exa = get_exa_client()
    except SourcingWebConfigError as e:
        _LAST_WEB_SCAN_SUMMARY["error"] = str(e)
        return redirect(url_for("sourcing_web.sourcing_web_list"))

    try:
        raw_content = sourcing_web_extractor.fetch_content(exa, row["url"])
    except Exception as e:  # noqa: BLE001
        _LAST_WEB_SCAN_SUMMARY["error"] = f"Couldn't fetch content for {row['url']}: {e}"
        return redirect(url_for("sourcing_web.sourcing_web_list"))

    _update_web_status(result_id, "extracted")

    if row["subreddit"]:
        source = "reddit"
        source_detail = f"r/{row['subreddit']}"
    else:
        source = "web"
        source_detail = ""

    return redirect(url_for(
        "observations.observations_new_form",
        raw_content=raw_content,
        source_url=row["url"],
        source=source,
        source_detail=source_detail,
    ))
