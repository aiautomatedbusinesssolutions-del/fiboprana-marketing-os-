"""Flask blueprint for the product ideas module."""

import json
from datetime import date, datetime, timedelta

from flask import Blueprint, abort, redirect, render_template, request, url_for

from db import ensure_schema, get_connection
from fleet import llm

from content import utils as content_utils
from content.idea_proposal_prompt import IDEA_PROPOSAL_SYSTEM_PROMPT
from content.guardrails_check import check_idea_against_guardrails

from app import (
    ROOT,
    IDEA_PROPOSAL_MODEL, GUARDRAILS_CHECK_MODEL,
    MAX_OBSERVATIONS_FOR_PROPOSAL,
    normalize_tags,
    link_idea_to_observations,
    build_ideas_query,
    _run_guardrails_check_and_serialize,
    _parse_gr_result,
    _idea_to_markdown,
)


bp = Blueprint("ideas", __name__)

# WORKFLOW.md Step 4 -> 5 build pipeline. Enforced here (not via a SQL CHECK) so
# the vocabulary can evolve without an SQLite table rebuild. NULL/'' = brainstorm
# only; 'content-instead' is the Step 4.1 outcome where the tool already exists in
# fiboprana-site so we make content rather than build.
BUILD_STAGES = ["captured", "gated", "building", "live", "content-instead"]

# Stages that mean "this idea cleared the Step 4 gate" — the first save that
# moves an idea into one of these stamps gate_week (the Monday of that week,
# matching flow_state.json's week keys). 'captured' is excluded on purpose:
# capture just creates the registry row; the gate call hasn't happened yet.
GATE_PASSED_STAGES = {"gated", "building", "live", "content-instead"}


def current_week_monday():
    """Monday of the current week as YYYY-MM-DD — same week key as /week."""
    today = date.today()
    return (today - timedelta(days=today.weekday())).isoformat()

# Free-form gate/ship fields saved alongside build_stage. Order = form order.
# (column, label, html_kind, help).
BUILD_FIELDS = [
    ("registry_match",        "Registry match",        "text",     "Step 4.1 — existing fiboprana-site tool? 'no', or 'yes: <tool>'"),
    ("target_query",          "Target query",          "text",     "Step 4.2 — the ONE distinct SEO/AEO query (don't overlap an existing tool)"),
    ("differentiation_angle", "Differentiation angle", "textarea", "Step 4.3 — the know-yourself / personalized-style framing"),
    ("quality_notes",         "Quality-bar notes",     "textarea", "Step 4.4 — can you hold the bar? interactive component + on-brand FAQs + quiz/Pro funnel hook"),
    ("app_url",               "App URL",               "text",     "Step 5 — live tool URL in the app, once shipped"),
    ("video_url",             "Feature video URL",     "text",     "Step 5 — the feature video, once shipped"),
]


def _ideas_error(error):
    return render_template("ideas/error.html", error=error)


@bp.route("/ideas")
def ideas_list():
    conn = get_connection()
    ensure_schema(conn)

    sql, params, filters = build_ideas_query(request.args)
    raw_rows = conn.execute(sql, params).fetchall()

    # Convert sqlite3.Row -> dict + add a parsed gr_status for the badge column
    # so the template doesn't have to call json.loads itself.
    rows = []
    for r in raw_rows:
        d = dict(r)
        d["gr_status"] = _parse_gr_result(d.get("guardrails_check_result")).get("status")
        rows.append(d)

    return render_template(
        "ideas/list.html",
        rows=rows,
        filters=filters,
        current_week=current_week_monday(),
        created_id=request.args.get("created"),
        proposed_count=request.args.get("proposed", type=int),
    )


@bp.route("/ideas/new", methods=["GET"])
def ideas_new_form():
    return render_template("ideas/new.html", defaults={}, errors={})


@bp.route("/ideas/new", methods=["POST"])
def ideas_new_submit():
    errors = {}
    title = (request.form.get("title") or "").strip()
    description = (request.form.get("description") or "").strip()
    source = (request.form.get("source") or "").strip()
    tags = normalize_tags(request.form.get("tags") or "")

    if not title:
        errors["title"] = "Title is required."
    if not description:
        errors["description"] = "Description is required."

    if errors:
        return render_template(
            "ideas/new.html", defaults=request.form, errors=errors
        ), 400

    conn = get_connection()
    ensure_schema(conn)
    cur = conn.execute(
        "INSERT INTO product_ideas (title, description, source, status, tags, proposed_by) "
        "VALUES (:title, :description, :source, 'idea', :tags, 'manual')",
        {"title": title, "description": description, "source": source, "tags": tags},
    )
    idea_id = cur.lastrowid

    gr_json, gr_ts = _run_guardrails_check_and_serialize(title, description)
    conn.execute(
        "UPDATE product_ideas SET guardrails_check_result = :r, "
        "guardrails_checked_at = :t, updated_at = datetime('now') "
        "WHERE id = :id",
        {"r": gr_json, "t": gr_ts, "id": idea_id},
    )
    link_idea_to_observations(conn, idea_id, source)
    conn.commit()

    return redirect(url_for("ideas.ideas_list", created=idea_id))


@bp.route("/ideas/<int:idea_id>")
def ideas_detail(idea_id):
    conn = get_connection()
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM product_ideas WHERE id = :id", {"id": idea_id}
    ).fetchone()
    if row is None:
        abort(404)
    gr_result = _parse_gr_result(row["guardrails_check_result"])
    idea_md = _idea_to_markdown(row, gr_result)
    linked_observations = conn.execute(
        "SELECT o.id, o.date_observed, o.segment_guess, o.pain_points "
        "FROM observations o "
        "JOIN product_idea_observations pio ON pio.observation_id = o.id "
        "WHERE pio.idea_id = :id "
        "ORDER BY o.id ASC",
        {"id": idea_id},
    ).fetchall()
    return render_template(
        "ideas/detail.html",
        row=row,
        gr_result=gr_result,
        idea_md=idea_md,
        linked_observations=linked_observations,
        build_stages=BUILD_STAGES,
        build_fields=BUILD_FIELDS,
        current_week=current_week_monday(),
    )


@bp.route("/ideas/<int:idea_id>/status", methods=["POST"])
def ideas_change_status(idea_id):
    new_status = (request.form.get("status") or "").strip()
    if new_status not in ("idea", "approved", "rejected"):
        abort(400)
    conn = get_connection()
    ensure_schema(conn)
    cur = conn.execute(
        "UPDATE product_ideas SET status = :s, updated_at = datetime('now') WHERE id = :id",
        {"s": new_status, "id": idea_id},
    )
    if cur.rowcount == 0:
        abort(404)
    conn.commit()
    return redirect(request.referrer or url_for("ideas.ideas_detail", idea_id=idea_id))


@bp.route("/ideas/<int:idea_id>/build", methods=["POST"])
def ideas_update_build(idea_id):
    """Save the WORKFLOW.md Step 4 -> 5 build pipeline for an idea.

    One form saves the whole panel at once: build_stage (the state machine,
    constrained to BUILD_STAGES here in Python) plus the free-form gate/ship
    fields in BUILD_FIELDS. The form is always pre-filled with current values,
    so a submit is a full overwrite of these columns — no partial-update
    surprises. An empty build_stage clears it back to brainstorm-only.
    """
    conn = get_connection()
    ensure_schema(conn)
    row = conn.execute(
        "SELECT gate_week FROM product_ideas WHERE id = :id", {"id": idea_id}
    ).fetchone()
    if row is None:
        abort(404)

    stage = (request.form.get("build_stage") or "").strip()
    if stage and stage not in BUILD_STAGES:
        abort(400)

    params = {col: (request.form.get(col) or "").strip() for col, *_ in BUILD_FIELDS}
    params["build_stage"] = stage or None
    params["sitemap_added"] = (request.form.get("sitemap_added") or "").strip()
    # Stamp the gate week exactly once: the first save that moves this idea past
    # the gate binds it to the current week (the week's feature). Later edits —
    # advancing building -> live, filling the video URL — keep the original week.
    params["gate_week"] = row["gate_week"] or (
        current_week_monday() if stage in GATE_PASSED_STAGES else None
    )
    params["id"] = idea_id

    conn.execute(
        "UPDATE product_ideas SET "
        "build_stage = :build_stage, gate_week = :gate_week, "
        "registry_match = :registry_match, "
        "target_query = :target_query, differentiation_angle = :differentiation_angle, "
        "quality_notes = :quality_notes, app_url = :app_url, "
        "sitemap_added = :sitemap_added, video_url = :video_url, "
        "updated_at = datetime('now') WHERE id = :id",
        params,
    )
    conn.commit()
    return redirect(url_for("ideas.ideas_detail", idea_id=idea_id))


@bp.route("/ideas/<int:idea_id>/check-guardrails", methods=["POST"])
def ideas_recheck(idea_id):
    conn = get_connection()
    ensure_schema(conn)
    row = conn.execute(
        "SELECT title, description FROM product_ideas WHERE id = :id", {"id": idea_id}
    ).fetchone()
    if row is None:
        abort(404)

    gr_json, gr_ts = _run_guardrails_check_and_serialize(row["title"], row["description"])
    conn.execute(
        "UPDATE product_ideas SET guardrails_check_result = :r, "
        "guardrails_checked_at = :t, updated_at = datetime('now') "
        "WHERE id = :id",
        {"r": gr_json, "t": gr_ts, "id": idea_id},
    )
    conn.commit()
    return redirect(url_for("ideas.ideas_detail", idea_id=idea_id))


@bp.route("/ideas/propose", methods=["POST"])
def ideas_propose():
    synthesis_path = ROOT / "SYNTHESIS.md"
    if not synthesis_path.is_file():
        return _ideas_error(
            "SYNTHESIS.md not found in project root. Generate or place it before proposing ideas."
        )
    synthesis_text = synthesis_path.read_text(encoding="utf-8")
    if not synthesis_text.strip():
        return _ideas_error("SYNTHESIS.md is empty.")

    conn = get_connection()
    ensure_schema(conn)
    obs_rows = conn.execute(
        "SELECT * FROM observations ORDER BY id DESC LIMIT :n",
        {"n": MAX_OBSERVATIONS_FOR_PROPOSAL},
    ).fetchall()
    if not obs_rows:
        return _ideas_error("No observations in the database to base ideas on.")

    user_message = content_utils.format_observations_and_synthesis_for_idea_prompt(
        obs_rows, synthesis_text
    )

    result, err = llm.complete(model=IDEA_PROPOSAL_MODEL, system=IDEA_PROPOSAL_SYSTEM_PROMPT,
                               user=user_message, max_tokens=2500, temperature=0.7)
    if err:
        return _ideas_error(err)
    raw_text = result.text

    try:
        ideas = content_utils.parse_idea_proposal_response(raw_text)
    except ValueError as e:
        return _ideas_error(f"AI response wasn't valid: {e}")

    saved = 0
    for idea in ideas:
        cur = conn.execute(
            "INSERT INTO product_ideas (title, description, source, status, tags, proposed_by) "
            "VALUES (:title, :description, :source, 'idea', :tags, 'ai')",
            {
                "title": idea["title"].strip(),
                "description": idea["description"].strip(),
                "source": idea["source"].strip(),
                "tags": normalize_tags(idea["tags"]),
            },
        )
        idea_id = cur.lastrowid

        gr_json, gr_ts = _run_guardrails_check_and_serialize(
            idea["title"], idea["description"]
        )
        conn.execute(
            "UPDATE product_ideas SET guardrails_check_result = :r, "
            "guardrails_checked_at = :t, updated_at = datetime('now') "
            "WHERE id = :id",
            {"r": gr_json, "t": gr_ts, "id": idea_id},
        )
        link_idea_to_observations(conn, idea_id, idea["source"])
        saved += 1

    conn.commit()
    return redirect(url_for("ideas.ideas_list", proposed=saved))
