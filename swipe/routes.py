"""Flask blueprint for the swipe-file module (content-signal capture)."""

import json

from flask import Blueprint, abort, redirect, render_template, request, url_for

from swipe import db as swipe_db
from swipe.triage_prompt import SWIPE_TRIAGE_SYSTEM_PROMPT, SWIPE_HOOK_TYPES

from app import SWIPE_TRIAGE_MODEL
from fleet import llm


bp = Blueprint("swipe", __name__)


def _parse_lines(blob):
    """One raw item per non-empty line: if the line starts with a URL, that's the
    link and the rest is the why-note; otherwise the whole line is the note."""
    items = []
    for line in (blob or "").splitlines():
        line = line.strip().lstrip("-•*").strip()
        if not line:
            continue
        url, why = "", line
        first, _, rest = line.partition(" ")
        if first.lower().startswith(("http://", "https://")):
            url, why = first, rest.strip(" -–—|").strip()
        items.append({"url": url, "why": why})
    return items


@bp.route("/swipe")
def swipe_index():
    conn = swipe_db.get_connection()
    swipe_db.ensure_schema(conn)
    new_count = conn.execute("SELECT COUNT(*) FROM swipe_raw WHERE status='new'").fetchone()[0]
    signal_count = conn.execute("SELECT COUNT(*) FROM swipe_signals WHERE active=1").fetchone()[0]
    signals = conn.execute(
        "SELECT * FROM swipe_signals WHERE active=1 ORDER BY id DESC LIMIT 25"
    ).fetchall()
    return render_template(
        "swipe/index.html",
        new_count=new_count, signal_count=signal_count, signals=signals,
        added=request.args.get("added", type=int),
        message=request.args.get("message") or "",
        error=request.args.get("error") or "",
    )


@bp.route("/swipe/new", methods=["GET"])
def swipe_new_form():
    return render_template("swipe/new.html", defaults={}, error="")


@bp.route("/swipe/new", methods=["POST"])
def swipe_new_submit():
    platform = (request.form.get("platform") or "").strip()
    items = _parse_lines(request.form.get("items") or "")
    if not items:
        return render_template(
            "swipe/new.html", defaults=request.form,
            error="Add at least one line — a link, or a short note.",
        ), 400
    conn = swipe_db.get_connection()
    swipe_db.ensure_schema(conn)
    conn.executemany(
        "INSERT INTO swipe_raw (url, platform, why_note) VALUES (:url, :platform, :why)",
        [{"url": it["url"], "platform": platform, "why": it["why"]} for it in items],
    )
    conn.commit()
    return redirect(url_for("swipe.swipe_triage", added=len(items)))


def _draft_for(raw):
    """One model call (Haiku tier) to pre-structure one raw item. Returns a dict
    draft or None on any failure — the row just stays un-drafted and the user
    fills it by hand."""
    user_message = (
        f"Platform: {raw['platform'] or 'unknown'}\n"
        f"Link: {raw['url'] or '(none)'}\n"
        f"Why it worked (founder's note): {raw['why_note'] or '(none)'}"
    )
    result, err = llm.complete(model=SWIPE_TRIAGE_MODEL, system=SWIPE_TRIAGE_SYSTEM_PROMPT,
                               user=user_message, max_tokens=400, temperature=0.3)
    if err:
        return None
    text = result.text
    if text.startswith("```"):  # tolerate a stray code fence
        text = text.strip("`")
        text = text.split("\n", 1)[1] if "\n" in text else text
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else None
    except (ValueError, TypeError):
        return None


@bp.route("/swipe/draft", methods=["POST"])
def swipe_draft_pending():
    """Pre-structure every un-drafted 'new' raw item — one Haiku call each."""
    conn = swipe_db.get_connection()
    swipe_db.ensure_schema(conn)
    if not llm.configured():
        return redirect(url_for("swipe.swipe_triage",
                                error="No LLM API key configured (OPENROUTER_API_KEY "
                                      "or ANTHROPIC_API_KEY in .env)."))
    rows = conn.execute(
        "SELECT * FROM swipe_raw WHERE status='new' AND draft_json IS NULL"
    ).fetchall()
    drafted = 0
    for r in rows:
        draft = _draft_for(r)
        if draft is not None:
            conn.execute("UPDATE swipe_raw SET draft_json=:d WHERE id=:id",
                         {"d": json.dumps(draft), "id": r["id"]})
            drafted += 1
    conn.commit()
    return redirect(url_for("swipe.swipe_triage",
                            message=f"Drafted {drafted} of {len(rows)} pending."))


@bp.route("/swipe/triage")
def swipe_triage():
    conn = swipe_db.get_connection()
    swipe_db.ensure_schema(conn)
    raw_rows = conn.execute(
        "SELECT * FROM swipe_raw WHERE status='new' ORDER BY id ASC"
    ).fetchall()
    items, undrafted = [], 0
    for r in raw_rows:
        d = dict(r)
        try:
            d["draft"] = json.loads(r["draft_json"]) if r["draft_json"] else {}
        except (ValueError, TypeError):
            d["draft"] = {}
        if not d["draft"]:
            undrafted += 1
        items.append(d)
    return render_template(
        "swipe/triage.html",
        items=items, hook_types=SWIPE_HOOK_TYPES, undrafted=undrafted,
        added=request.args.get("added", type=int),
        message=request.args.get("message") or "",
        error=request.args.get("error") or "",
    )


@bp.route("/swipe/<int:raw_id>/promote", methods=["POST"])
def swipe_promote(raw_id):
    conn = swipe_db.get_connection()
    swipe_db.ensure_schema(conn)
    raw = conn.execute("SELECT * FROM swipe_raw WHERE id=:id", {"id": raw_id}).fetchone()
    if raw is None:
        abort(404)
    conn.execute(
        "INSERT INTO swipe_signals "
        "(raw_id, platform, hook_type, why_it_landed, nates_angle, recognizable_specific, tags) "
        "VALUES (:raw_id, :platform, :hook_type, :why, :angle, :spec, :tags)",
        {
            "raw_id": raw_id,
            "platform": raw["platform"],
            "hook_type": (request.form.get("hook_type") or "").strip(),
            "why": (request.form.get("why_it_landed") or "").strip(),
            "angle": (request.form.get("nates_angle") or "").strip(),
            "spec": (request.form.get("recognizable_specific") or "").strip(),
            "tags": (request.form.get("tags") or "").strip(),
        },
    )
    conn.execute("UPDATE swipe_raw SET status='promoted' WHERE id=:id", {"id": raw_id})
    conn.commit()
    return redirect(url_for("swipe.swipe_triage"))


@bp.route("/swipe/<int:raw_id>/discard", methods=["POST"])
def swipe_discard(raw_id):
    conn = swipe_db.get_connection()
    swipe_db.ensure_schema(conn)
    cur = conn.execute("UPDATE swipe_raw SET status='discarded' WHERE id=:id", {"id": raw_id})
    if cur.rowcount == 0:
        abort(404)
    conn.commit()
    return redirect(url_for("swipe.swipe_triage"))
