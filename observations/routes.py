"""Flask blueprint for the observations module.

Observations live in the same DB as conversations (capture/conversations.db),
but the form/edit/extract/export flow is distinct enough to warrant its own
blueprint. observations_extract is the AI pre-fill endpoint used by the
JS Extract-with-AI button on the new/edit form.
"""

import json
from datetime import date, datetime

from flask import (
    Blueprint, abort, jsonify, redirect, render_template, request,
    send_from_directory, url_for,
)

from db import ensure_schema, get_connection
import export_observations
from extraction_prompt import EXTRACTION_SYSTEM_PROMPT
from fleet import llm

from app import (
    OBSERVATION_FIELDS, EXPORTS_DIR,
    EXTRACTION_MODEL,
    normalize_tags, infer_source_from_url, build_observations_query,
)


bp = Blueprint("observations", __name__)


@bp.route("/observations")
def observations_list():
    conn = get_connection()
    ensure_schema(conn)

    sql, params, filters = build_observations_query(request.args)
    rows = conn.execute(sql, params).fetchall()
    created_id = request.args.get("created")

    return render_template(
        "observations/list.html",
        rows=rows,
        filters=filters,
        created_id=created_id,
    )


def _parse_observation_form(form):
    """Extract + validate observation form fields. Returns (data, errors)."""
    errors = {}
    data = {}

    raw_date = (form.get("date_observed") or "").strip()
    if not raw_date:
        errors["date_observed"] = "Date is required."
    else:
        try:
            datetime.strptime(raw_date, "%Y-%m-%d")
            data["date_observed"] = raw_date
        except ValueError:
            errors["date_observed"] = "Expected format YYYY-MM-DD."

    for name, _label, _kind, _hint in OBSERVATION_FIELDS:
        if name == "date_observed":
            continue
        raw = form.get(name, "")
        if name == "tags":
            data[name] = normalize_tags(raw)
        elif name == "source":
            data[name] = raw.strip().lower()
        else:
            data[name] = raw.strip()

    if not data.get("source"):
        data["source"] = infer_source_from_url(data.get("source_url", ""))
    return data, errors


@bp.route("/observations/new", methods=["GET"])
def observations_new_form():
    defaults = {"date_observed": date.today().isoformat()}
    for key in ("raw_content", "source_url", "source", "source_detail"):
        if request.args.get(key):
            defaults[key] = request.args[key]
    return render_template(
        "observations/new.html",
        fields=OBSERVATION_FIELDS,
        defaults=defaults,
        errors={},
        heading="New observation",
        submit_label="Save observation",
        form_action=url_for("observations.observations_new_submit"),
    )


@bp.route("/observations/new", methods=["POST"])
def observations_new_submit():
    data, errors = _parse_observation_form(request.form)
    if errors:
        return render_template(
            "observations/new.html",
            fields=OBSERVATION_FIELDS,
            defaults=request.form,
            errors=errors,
            heading="New observation",
            submit_label="Save observation",
            form_action=url_for("observations.observations_new_submit"),
        ), 400

    conn = get_connection()
    ensure_schema(conn)
    cols = list(data.keys())
    placeholders = ", ".join(f":{c}" for c in cols)
    sql = f"INSERT INTO observations ({', '.join(cols)}) VALUES ({placeholders})"
    cur = conn.execute(sql, data)
    conn.commit()

    return redirect(url_for("observations.observations_list", created=cur.lastrowid))


@bp.route("/observations/<int:obs_id>/edit", methods=["GET"])
def observations_edit_form(obs_id):
    conn = get_connection()
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM observations WHERE id = :id", {"id": obs_id}
    ).fetchone()
    if row is None:
        abort(404)
    return render_template(
        "observations/new.html",
        fields=OBSERVATION_FIELDS,
        defaults=dict(row),
        errors={},
        heading=f"Edit observation #{obs_id}",
        submit_label="Save changes",
        form_action=url_for("observations.observations_edit_submit", obs_id=obs_id),
    )


@bp.route("/observations/<int:obs_id>/edit", methods=["POST"])
def observations_edit_submit(obs_id):
    conn = get_connection()
    ensure_schema(conn)
    row = conn.execute(
        "SELECT id FROM observations WHERE id = :id", {"id": obs_id}
    ).fetchone()
    if row is None:
        abort(404)

    data, errors = _parse_observation_form(request.form)
    if errors:
        return render_template(
            "observations/new.html",
            fields=OBSERVATION_FIELDS,
            defaults=request.form,
            errors=errors,
            heading=f"Edit observation #{obs_id}",
            submit_label="Save changes",
            form_action=url_for("observations.observations_edit_submit", obs_id=obs_id),
        ), 400

    set_clause = ", ".join(f"{c} = :{c}" for c in data.keys())
    params = dict(data)
    params["id"] = obs_id
    conn.execute(f"UPDATE observations SET {set_clause} WHERE id = :id", params)
    conn.commit()
    return redirect(url_for("observations.observations_detail", obs_id=obs_id))


@bp.route("/observations/extract", methods=["POST"])
def observations_extract():
    """AI pre-fill for the observations form. Never auto-saves — the frontend
    populates the form fields and the user reviews before submitting."""
    payload = request.get_json(silent=True) or {}
    raw_content = (payload.get("raw_content") or "").strip()
    if not raw_content:
        return jsonify({"error": "raw_content is required."}), 400

    result, err = llm.complete(model=EXTRACTION_MODEL, system=EXTRACTION_SYSTEM_PROMPT,
                               user=raw_content, max_tokens=2000, temperature=0.3)
    if err:
        return jsonify({"error": err}), 502
    text = result.text

    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    try:
        extracted = json.loads(text)
    except json.JSONDecodeError:
        return jsonify({
            "error": "AI response wasn't valid JSON. Edit the fields manually and try again, or re-click Extract.",
        }), 500

    keys = ("segment_guess", "pain_points", "notable_quotes",
            "feature_implications", "content_hooks", "tags")
    return jsonify({k: extracted.get(k, "") for k in keys})


@bp.route("/observations/<int:obs_id>")
def observations_detail(obs_id):
    conn = get_connection()
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM observations WHERE id = :id", {"id": obs_id}
    ).fetchone()
    if row is None:
        abort(404)
    return render_template("observations/detail.html", row=row, fields=OBSERVATION_FIELDS)


@bp.route("/observations/export")
def observations_export():
    conn = get_connection()
    ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM observations ORDER BY date_observed ASC, id ASC"
    ).fetchall()

    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    md_path = EXPORTS_DIR / f"observations_{today}.md"
    csv_path = EXPORTS_DIR / f"observations_{today}.csv"

    export_observations.write_markdown(md_path, rows)
    export_observations.write_csv(csv_path, rows)

    return render_template(
        "observations/export.html",
        count=len(rows),
        md_filename=md_path.name,
        csv_filename=csv_path.name,
    )


@bp.route("/observations/export/download")
def observations_export_download():
    filename = request.args.get("file", "")
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        abort(400)
    if not filename.startswith("observations_"):
        abort(400)
    if not (EXPORTS_DIR / filename).is_file():
        abort(404)
    return send_from_directory(EXPORTS_DIR, filename, as_attachment=True)
