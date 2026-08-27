"""Flask blueprint for the synthesis module."""

from flask import Blueprint, abort, redirect, render_template, request, url_for

from db import ensure_schema, get_connection

from synthesis import db as synthesis_db
from synthesis import analyzer as synthesis_analyzer
from synthesis import synthesizer as synthesis_runner

from app import (
    SYNTHESIS_MODEL,
    MAX_OBSERVATIONS_FOR_SYNTHESIS,
)


bp = Blueprint("synthesis", __name__)

@bp.route("/synthesis")
def synthesis_dashboard():
    capture_conn = get_connection()
    ensure_schema(capture_conn)
    runs_conn = synthesis_db.get_connection()
    synthesis_db.ensure_schema(runs_conn)

    latest = synthesis_runner.latest_run(runs_conn)
    runs = synthesis_runner.list_runs(runs_conn, limit=20)

    obs_count = synthesis_analyzer.observation_count(capture_conn)
    recent_count = synthesis_analyzer.recent_observation_count(capture_conn, days=30)
    # "Drift" = observations added since the last synthesis was generated.
    # 0 means the file is in sync; >0 means the idea proposer is reading
    # slightly-stale synthesis until the user clicks regenerate.
    if latest:
        drift = synthesis_analyzer.days_since_observation_added(
            capture_conn, latest["generated_at"]
        )
    else:
        drift = obs_count

    return render_template(
        "synthesis/dashboard.html",
        latest=latest,
        runs=runs,
        stats={
            "observation_count": obs_count,
            "recent_count":      recent_count,
            "drift":             drift,
        },
        no_observations=(obs_count == 0),
        tags=synthesis_analyzer.tag_frequency(capture_conn, limit=30),
        sources=synthesis_analyzer.source_breakdown(capture_conn),
        product_insights=synthesis_analyzer.product_insight_observations(capture_conn),
        regenerate_error=request.args.get("error"),
        regenerate_success=request.args.get("success"),
    )


@bp.route("/synthesis/regenerate", methods=["POST"])
def synthesis_regenerate():
    capture_conn = get_connection()
    ensure_schema(capture_conn)
    obs_rows = capture_conn.execute(
        "SELECT * FROM observations ORDER BY id ASC LIMIT :n",
        {"n": MAX_OBSERVATIONS_FOR_SYNTHESIS},
    ).fetchall()
    if not obs_rows:
        return redirect(url_for(
            "synthesis.synthesis_dashboard",
            error="No observations to synthesize.",
        ))

    try:
        result = synthesis_runner.run_synthesis(obs_rows, model=SYNTHESIS_MODEL)
    except ValueError as e:
        return redirect(url_for("synthesis.synthesis_dashboard", error=str(e)))
    except Exception as e:  # noqa: BLE001
        return redirect(url_for("synthesis.synthesis_dashboard", error=f"Unexpected synthesis error: {e}"))

    try:
        synthesis_runner.write_synthesis_md(result["synthesis_md"])
    except OSError as e:
        # The DB-side run is still worth storing even if the file write fails —
        # the markdown survives in the runs table and the user can copy it out.
        runs_conn = synthesis_db.get_connection()
        synthesis_db.ensure_schema(runs_conn)
        run_id = synthesis_runner.store_run(runs_conn, result)
        return redirect(url_for(
            "synthesis.synthesis_dashboard",
            error=f"Synthesis generated (saved as run #{run_id}) but couldn't write SYNTHESIS.md: {e}",
        ))

    runs_conn = synthesis_db.get_connection()
    synthesis_db.ensure_schema(runs_conn)
    run_id = synthesis_runner.store_run(runs_conn, result)

    return redirect(url_for(
        "synthesis.synthesis_dashboard",
        success=f"Regenerated from {result['observation_count']} observations (run #{run_id}). "
                "SYNTHESIS.md updated; commit it when ready.",
    ))


@bp.route("/synthesis/runs/<int:run_id>")
def synthesis_run_detail(run_id):
    runs_conn = synthesis_db.get_connection()
    synthesis_db.ensure_schema(runs_conn)
    run = synthesis_runner.get_run(runs_conn, run_id)
    if run is None:
        abort(404)
    return render_template("synthesis/run_detail.html", run=run)
