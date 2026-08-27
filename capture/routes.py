"""Flask blueprint for the capture module.

All /capture endpoints — list/detail/new (full form + paste)/edit/export.
The capture-only form parser (_parse_capture_form) lives here too. Anything
this file needs from app.py is imported below; the registration in app.py
sits at the bottom of that file so these names are already defined when
this module is loaded.
"""

from datetime import date, datetime

from flask import (
    Blueprint, abort, redirect, render_template, request,
    send_from_directory, url_for,
)

from db import ensure_schema, get_connection
from export import write_csv, write_markdown

from app import (
    FIELDS, PASTE_FIELDS, EXPORTS_DIR,
    normalize_tags, build_list_query,
)


bp = Blueprint("capture", __name__)


@bp.route("/capture")
def capture_list():
    conn = get_connection()
    ensure_schema(conn)

    sql, params, filters = build_list_query(request.args)
    rows = conn.execute(sql, params).fetchall()
    created_id = request.args.get("created")

    return render_template(
        "capture/list.html",
        rows=rows,
        filters=filters,
        created_id=created_id,
    )


def _parse_capture_form(form):
    """Extract + validate capture form fields. Returns (data, errors)."""
    errors = {}
    data = {}

    raw_date = (form.get("date") or "").strip()
    if not raw_date:
        errors["date"] = "Date is required."
    else:
        try:
            datetime.strptime(raw_date, "%Y-%m-%d")
            data["date"] = raw_date
        except ValueError:
            errors["date"] = "Expected format YYYY-MM-DD."

    for name, _label, _kind, _hint in FIELDS:
        if name == "date":
            continue
        raw = form.get(name, "")
        if name == "tags":
            data[name] = normalize_tags(raw)
        else:
            data[name] = raw.strip()
    return data, errors


@bp.route("/capture/new", methods=["GET"])
def capture_new_form():
    defaults = {"date": date.today().isoformat()}
    return render_template(
        "capture/new.html",
        fields=FIELDS,
        defaults=defaults,
        errors={},
        heading="New conversation",
        submit_label="Save entry",
        form_action=url_for("capture.capture_new_submit"),
    )


@bp.route("/capture/new", methods=["POST"])
def capture_new_submit():
    data, errors = _parse_capture_form(request.form)
    if errors:
        return render_template(
            "capture/new.html",
            fields=FIELDS,
            defaults=request.form,
            errors=errors,
            heading="New conversation",
            submit_label="Save entry",
            form_action=url_for("capture.capture_new_submit"),
        ), 400

    # Stamp the follow-up status timestamp on initial entry whenever a status
    # is supplied, so "Xd awaiting reply" reads from a real clock, not from
    # the conversation date.
    if data.get("follow_up_status"):
        data["follow_up_status_changed_at"] = datetime.utcnow().isoformat(timespec="seconds")

    conn = get_connection()
    ensure_schema(conn)
    cols = list(data.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = f"INSERT INTO conversations ({', '.join(cols)}) VALUES ({placeholders})"
    cur = conn.execute(sql, data)
    conn.commit()

    return redirect(url_for("capture.capture_list", created=cur.lastrowid))


@bp.route("/capture/<int:entry_id>/edit", methods=["GET"])
def capture_edit_form(entry_id):
    conn = get_connection()
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM conversations WHERE id = :id", {"id": entry_id}
    ).fetchone()
    if row is None:
        abort(404)
    return render_template(
        "capture/new.html",
        fields=FIELDS,
        defaults=dict(row),
        errors={},
        heading=f"Edit entry #{entry_id}",
        submit_label="Save changes",
        form_action=url_for("capture.capture_edit_submit", entry_id=entry_id),
    )


@bp.route("/capture/<int:entry_id>/edit", methods=["POST"])
def capture_edit_submit(entry_id):
    conn = get_connection()
    ensure_schema(conn)
    existing = conn.execute(
        "SELECT follow_up_status FROM conversations WHERE id = :id",
        {"id": entry_id},
    ).fetchone()
    if existing is None:
        abort(404)

    data, errors = _parse_capture_form(request.form)
    if errors:
        return render_template(
            "capture/new.html",
            fields=FIELDS,
            defaults=request.form,
            errors=errors,
            heading=f"Edit entry #{entry_id}",
            submit_label="Save changes",
            form_action=url_for("capture.capture_edit_submit", entry_id=entry_id),
        ), 400

    # Only bump the change timestamp when follow_up_status actually moved,
    # so editing other fields doesn't reset the awaiting-reply clock.
    if data.get("follow_up_status", "") != (existing["follow_up_status"] or ""):
        data["follow_up_status_changed_at"] = datetime.utcnow().isoformat(timespec="seconds")

    set_clause = ", ".join(f"{c} = :{c}" for c in data.keys())
    params = dict(data)
    params["id"] = entry_id
    conn.execute(f"UPDATE conversations SET {set_clause} WHERE id = :id", params)
    conn.commit()
    return redirect(url_for("capture.capture_detail", entry_id=entry_id))


@bp.route("/capture/paste", methods=["GET"])
def capture_paste_form():
    defaults = {"date": date.today().isoformat()}
    return render_template("capture/paste.html", fields=PASTE_FIELDS, defaults=defaults, errors={})


@bp.route("/capture/paste", methods=["POST"])
def capture_paste_submit():
    errors = {}
    data = {}

    raw_transcript = request.form.get("raw_transcript", "").strip()
    if not raw_transcript:
        errors["raw_transcript"] = "Paste a transcript before saving."
    else:
        data["raw_transcript"] = raw_transcript

    raw_date = (request.form.get("date") or "").strip()
    if not raw_date:
        errors["date"] = "Date is required."
    else:
        try:
            datetime.strptime(raw_date, "%Y-%m-%d")
            data["date"] = raw_date
        except ValueError:
            errors["date"] = "Expected format YYYY-MM-DD."

    for name, _label, _kind, _hint in PASTE_FIELDS:
        if name in ("raw_transcript", "date"):
            continue
        raw = request.form.get(name, "")
        if name == "tags":
            data[name] = normalize_tags(raw)
        else:
            data[name] = raw.strip()

    if errors:
        return render_template(
            "capture/paste.html", fields=PASTE_FIELDS, defaults=request.form, errors=errors
        ), 400

    # Mirror capture_new_submit: stamp the follow-up clock at insert time if
    # a status is supplied via the paste form too, so awaiting-reply age is
    # consistent across both create paths.
    if data.get("follow_up_status"):
        data["follow_up_status_changed_at"] = datetime.utcnow().isoformat(timespec="seconds")

    conn = get_connection()
    ensure_schema(conn)
    cols = list(data.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = f"INSERT INTO conversations ({', '.join(cols)}) VALUES ({placeholders})"
    cur = conn.execute(sql, data)
    conn.commit()

    return redirect(url_for("capture.capture_list", created=cur.lastrowid))


@bp.route("/capture/<int:entry_id>")
def capture_detail(entry_id):
    conn = get_connection()
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM conversations WHERE id = :id", {"id": entry_id}
    ).fetchone()
    if row is None:
        abort(404)
    return render_template("capture/detail.html", row=row, fields=FIELDS)


@bp.route("/capture/export")
def capture_export():
    conn = get_connection()
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM conversations ORDER BY date ASC, id ASC"
    ).fetchall()

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    md_path = EXPORTS_DIR / f"conversations_{today}.md"
    csv_path = EXPORTS_DIR / f"conversations_{today}.csv"

    write_markdown(md_path, rows)
    write_csv(csv_path, rows)

    return render_template(
        "capture/export.html",
        count=len(rows),
        md_filename=md_path.name,
        csv_filename=csv_path.name,
    )


@bp.route("/capture/export/download")
def capture_export_download():
    # Reject anything that isn't a bare filename (no traversal, no subpaths).
    filename = request.args.get("file", "")
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        abort(400)
    if not (EXPORTS_DIR / filename).is_file():
        abort(404)
    return send_from_directory(EXPORTS_DIR, filename, as_attachment=True)
