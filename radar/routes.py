"""Flask blueprint for the radar module."""

import json
import re

from flask import Blueprint, abort, redirect, render_template, request, url_for

from radar import db as radar_db
from radar.config import load_config as load_radar_config
from radar.exa_client import RadarConfigError, get_exa_client as get_radar_exa_client
from radar import scanner as radar_scanner
from radar.topic_summary_prompt import RADAR_TOPIC_SUMMARY_SYSTEM_PROMPT
from radar.trend_digest_prompt import TREND_DIGEST_SYSTEM_PROMPT

from fleet import llm
from app import (
    RADAR_TOPIC_SUMMARY_MODEL,
    RADAR_DIGEST_MODEL,
)


bp = Blueprint("radar", __name__)

_LAST_RADAR_SCAN_SUMMARY = {"summary": None, "error": None}


def _group_radar_rows(rows):
    """Bucket radar rows by (source, source_label). Each bucket carries the
    items plus a few aggregates the template uses for the collapsed header
    (count, max score, latest published date)."""
    groups = {}
    for r in rows:
        key = (r["source"], r["source_label"] or "(unlabeled)")
        g = groups.setdefault(key, {
            "source": r["source"],
            "source_label": r["source_label"] or "(unlabeled)",
            "entries": [],
            "max_score": None,
            "latest_published": None,
        })
        g["entries"].append(r)
        score = r["score"]
        if score is not None and (g["max_score"] is None or score > g["max_score"]):
            g["max_score"] = score
        pub = r["published_at"]
        if pub and (g["latest_published"] is None or pub > g["latest_published"]):
            g["latest_published"] = pub

    # Most-recent activity first; ties broken by larger groups first.
    return sorted(
        groups.values(),
        key=lambda g: (g["latest_published"] or "", len(g["entries"])),
        reverse=True,
    )


@bp.route("/radar")
def radar_list():
    conn = radar_db.get_connection()
    radar_db.ensure_schema(conn)

    rows = conn.execute(
        "SELECT * FROM radar_items WHERE status = 'new' "
        "ORDER BY published_at DESC NULLS LAST, first_seen_at DESC, id DESC"
    ).fetchall()

    groups = _group_radar_rows(rows)

    # Attach any cached topic summary to its group. We key the lookup the
    # same way the UI groups: (source, source_label). topic_key is also a
    # stable string the template can compare against ?opened=... to keep a
    # specific dropdown expanded after a summarize-and-redirect.
    cached = conn.execute(
        "SELECT source, source_label, summary, item_count, generated_at "
        "FROM radar_topic_summaries"
    ).fetchall()
    summaries = {(c["source"], c["source_label"]): dict(c) for c in cached}
    for g in groups:
        s = summaries.get((g["source"], g["source_label"]))
        g["topic_key"] = f"{g['source']}::{g['source_label']}"
        g["summary"] = s
        g["summary_stale"] = bool(s and s["item_count"] != len(g["entries"]))

    summary = _LAST_RADAR_SCAN_SUMMARY.pop("summary", None)
    error = _LAST_RADAR_SCAN_SUMMARY.pop("error", None)
    _LAST_RADAR_SCAN_SUMMARY["summary"] = None
    _LAST_RADAR_SCAN_SUMMARY["error"] = None

    return render_template(
        "radar/list.html",
        groups=groups,
        total_items=len(rows),
        scan_summary=summary,
        scan_error=error,
        opened_key=request.args.get("opened") or "",
        topic_error=request.args.get("topic_error") or "",
        bulk_summary_message=request.args.get("bulk_summary_message") or "",
    )


@bp.route("/radar/saved")
def radar_saved():
    conn = radar_db.get_connection()
    radar_db.ensure_schema(conn)
    rows = conn.execute(
        "SELECT * FROM radar_items WHERE status = 'saved' "
        "ORDER BY status_changed_at DESC, id DESC"
    ).fetchall()
    return render_template("radar/saved.html", rows=rows)


@bp.route("/radar/scan", methods=["POST"])
def radar_scan():
    conn = radar_db.get_connection()
    radar_db.ensure_schema(conn)

    try:
        config = load_radar_config()
    except Exception as e:  # noqa: BLE001
        _LAST_RADAR_SCAN_SUMMARY["error"] = f"Couldn't load radar/config.yaml: {e}"
        return redirect(url_for("radar.radar_list"))

    # Exa is optional — if the key is missing, run HN+RSS only and surface a
    # soft notice. Lets the radar still work without an Exa key configured.
    exa_client = None
    try:
        exa_client = get_radar_exa_client()
    except RadarConfigError as e:
        _LAST_RADAR_SCAN_SUMMARY["error"] = str(e)

    try:
        summary = radar_scanner.scan_all(conn, config, exa_client=exa_client)
    except Exception as e:  # noqa: BLE001
        _LAST_RADAR_SCAN_SUMMARY["error"] = f"Scan failed: {e}"
        return redirect(url_for("radar.radar_list"))

    _LAST_RADAR_SCAN_SUMMARY["summary"] = summary
    return redirect(url_for("radar.radar_list"))


def _update_radar_status(item_id, new_status):
    conn = radar_db.get_connection()
    radar_db.ensure_schema(conn)
    cur = conn.execute(
        "UPDATE radar_items SET status = :s, status_changed_at = datetime('now') "
        "WHERE id = :id",
        {"s": new_status, "id": item_id},
    )
    conn.commit()
    return cur.rowcount


@bp.route("/radar/<int:item_id>/save", methods=["POST"])
def radar_save(item_id):
    if _update_radar_status(item_id, "saved") == 0:
        abort(404)
    return redirect(url_for("radar.radar_list"))


@bp.route("/radar/<int:item_id>/skip", methods=["POST"])
def radar_skip(item_id):
    if _update_radar_status(item_id, "skipped") == 0:
        abort(404)
    return redirect(url_for("radar.radar_list"))


@bp.route("/radar/<int:item_id>/archive", methods=["POST"])
def radar_archive(item_id):
    if _update_radar_status(item_id, "archived") == 0:
        abort(404)
    # Archive happens from both /radar and /radar/saved; default to /radar.
    # The Referer-based redirect was an open-redirect risk (spoofable).
    return redirect(url_for("radar.radar_list"))


def _radar_topic_redirect(source, source_label, error=None):
    """Send the user back to /radar with the just-summarized topic still
    expanded (?opened=) so they don't have to click the dropdown again."""
    args = {"opened": f"{source}::{source_label}"}
    if error:
        args["topic_error"] = error
    return redirect(url_for("radar.radar_list", **args))


def _summarize_radar_topic(conn, source, source_label):
    """Generate one topic summary and upsert it. Returns (ok, message)
    where ok=True on success and message is the summary text or an error
    string. Caller controls redirect / response shape."""
    rows = conn.execute(
        "SELECT title, blurb FROM radar_items "
        "WHERE status = 'new' AND source = :src AND source_label = :lbl "
        "ORDER BY published_at DESC NULLS LAST, id DESC",
        {"src": source, "lbl": source_label},
    ).fetchall()
    if not rows:
        return False, "No items in this topic to summarize."

    lines = []
    for i, r in enumerate(rows, 1):
        title = (r["title"] or "").strip()
        blurb = (r["blurb"] or "").strip().replace("\n", " ")
        if len(blurb) > 400:
            blurb = blurb[:400] + "…"
        lines.append(f"{i}. {title}" + (f" — {blurb}" if blurb else ""))
    user_message = (
        f"Topic: {source_label} (source: {source}). "
        f"{len(rows)} item{'s' if len(rows) != 1 else ''}.\n\n"
        + "\n".join(lines)
    )

    result, err = llm.complete(model=RADAR_TOPIC_SUMMARY_MODEL,
                               system=RADAR_TOPIC_SUMMARY_SYSTEM_PROMPT,
                               user=user_message, max_tokens=400, temperature=0.2)
    if err:
        return False, err
    summary_text = result.text

    conn.execute(
        "INSERT INTO radar_topic_summaries (source, source_label, summary, item_count, generated_at) "
        "VALUES (:src, :lbl, :summary, :count, datetime('now')) "
        "ON CONFLICT(source, source_label) DO UPDATE SET "
        "summary = excluded.summary, item_count = excluded.item_count, "
        "generated_at = excluded.generated_at",
        {"src": source, "lbl": source_label, "summary": summary_text, "count": len(rows)},
    )
    conn.commit()
    return True, summary_text


@bp.route("/radar/topics/summarize", methods=["POST"])
def radar_topic_summarize():
    """Generate (or regenerate) the feature summary for one topic."""
    source = (request.form.get("source") or "").strip()
    source_label = (request.form.get("source_label") or "").strip()
    if not source or not source_label:
        return _radar_topic_redirect(source or "?", source_label or "?",
                                     error="Missing source or source_label.")

    conn = radar_db.get_connection()
    radar_db.ensure_schema(conn)
    ok, message = _summarize_radar_topic(conn, source, source_label)
    return _radar_topic_redirect(source, source_label, error=None if ok else message)


@bp.route("/radar/topics/summarize-all", methods=["POST"])
def radar_topic_summarize_all():
    """Generate summaries for every topic that's missing one or stale.
    Cheap (Haiku) but skip topics already in sync to avoid redundant calls."""
    conn = radar_db.get_connection()
    radar_db.ensure_schema(conn)

    rows = conn.execute(
        "SELECT * FROM radar_items WHERE status = 'new' "
        "ORDER BY published_at DESC NULLS LAST, first_seen_at DESC, id DESC"
    ).fetchall()
    groups = _group_radar_rows(rows)

    cached = {
        (c["source"], c["source_label"]): c["item_count"]
        for c in conn.execute(
            "SELECT source, source_label, item_count FROM radar_topic_summaries"
        ).fetchall()
    }

    generated = 0
    skipped_in_sync = 0
    errors = []
    for g in groups:
        cached_count = cached.get((g["source"], g["source_label"]))
        # Skip when the cached summary is already in sync with the topic's
        # current item count. New topics and stale ones (count drift) run.
        if cached_count is not None and cached_count == len(g["entries"]):
            skipped_in_sync += 1
            continue
        ok, message = _summarize_radar_topic(conn, g["source"], g["source_label"])
        if ok:
            generated += 1
        else:
            errors.append(f"{g['source_label']}: {message}")

    parts = [f"Generated {generated} summar{'y' if generated == 1 else 'ies'}."]
    if skipped_in_sync:
        parts.append(f"{skipped_in_sync} already in sync.")
    if errors:
        parts.append("Errors: " + "; ".join(errors))
    # Pass the result through a query param rather than a stash dict — this
    # endpoint is one-shot, doesn't need cross-request state.
    return redirect(url_for("radar.radar_list", bulk_summary_message=" ".join(parts)))


# ---------- weekly trend digest ----------
# A second read-lens over the same radar_items pile. Instead of per-topic
# feature extraction, this distills the week's NEW items into one brand-voiced
# trend digest (the cutting-edge + AI-in-wellness story) and keeps it in
# radar_digests as a growing journal. Generation suppresses themes already
# covered by the prior digest. First lane of the weekly content system.


def _digest_cutoff(conn):
    """Return (cutoff_iso, prior_id, prior_md) — cutoff and id from the most
    recent digest, prior_md from the last THREE joined newest-first, or an
    epoch sentinel for the first-ever run so everything counts as new.

    Three digests, not one: with a one-week dedupe memory, an item that skips
    a week resurfaces looking new (the Visa AI assistant got re-reported as
    fresh on 2026-08-02 after appearing 2026-07-19)."""
    rows = conn.execute(
        "SELECT id, generated_at, digest_md FROM radar_digests ORDER BY id DESC LIMIT 3"
    ).fetchall()
    if not rows:
        return "0000-00-00 00:00:00", None, ""
    prior_md = "\n\n".join(
        f"--- digest from {r['generated_at'][:10]} ---\n{r['digest_md']}"
        for r in rows if r["digest_md"]
    )
    return rows[0]["generated_at"], rows[0]["id"], prior_md


# How far back the digest looks. The cutoff (last digest time) already handles
# "not yet digested"; this window is a backstop so an old, never-digested backlog
# doesn't resurface — notably in the very first digest. ~3 weeks comfortably
# covers a weekly cadence with margin.
DIGEST_LOOKBACK_DAYS = 21


def _count_new_since(conn, cutoff):
    """Radar items eligible for the next digest: not triaged out, first seen
    after the cutoff, and within the lookback window."""
    return conn.execute(
        "SELECT COUNT(*) FROM radar_items "
        "WHERE status NOT IN ('skipped', 'archived') "
        "AND first_seen_at > :c AND first_seen_at > datetime('now', :win)",
        {"c": cutoff, "win": f"-{DIGEST_LOOKBACK_DAYS} days"},
    ).fetchone()[0]


# The digest cites its sources as [#N] markers (N = 1-based input item number).
# We resolve those to real URLs from radar_items deterministically — the model
# never writes URLs, so there's nothing to hallucinate. Distinct from the
# advice-line brackets like [education-side], which carry no '#'.
_DIGEST_CITE_RE = re.compile(r"\[#\s*([\d,\s#]+)\]")


def _extract_cited_positions(text):
    """1-based input item numbers cited as [#N] / [#N, #M] markers in the digest,
    in first-appearance order, deduped. Drives the Sources list."""
    cited, seen = [], set()
    for m in _DIGEST_CITE_RE.finditer(text or ""):
        for tok in re.findall(r"\d+", m.group(1)):
            n = int(tok)
            if n not in seen:
                seen.add(n)
                cited.append(n)
    return cited


def _digest_with_sources(row):
    """sqlite Row -> dict with a parsed `sources` list (empty if absent/bad)."""
    d = dict(row)
    raw = d.get("sources_json")
    try:
        d["sources"] = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        d["sources"] = []
    return d


def _generate_trend_digest(conn, prior_md_override=None):
    """Distill the week's new radar items into one digest row. Returns
    (ok, message): on success message is the digest text; otherwise an error
    string. Mirrors _summarize_radar_topic's Claude-call error handling.

    prior_md_override: when given, use this text as the "PREVIOUSLY REPORTED"
    dedupe block instead of the local DB's last digest. The fleet agent passes
    the last digest from Supabase, so week-over-week dedup still works when the
    local radar DB is ephemeral (e.g. the cloud cron)."""
    cutoff, prior_id, prior_md = _digest_cutoff(conn)
    if prior_md_override is not None:
        prior_md = prior_md_override
    rows = conn.execute(
        "SELECT title, blurb, source, source_label, url, published_at "
        "FROM radar_items "
        "WHERE status NOT IN ('skipped', 'archived') "
        "AND first_seen_at > :c AND first_seen_at > datetime('now', :win) "
        "ORDER BY first_seen_at DESC, COALESCE(score, 0) DESC LIMIT 120",
        {"c": cutoff, "win": f"-{DIGEST_LOOKBACK_DAYS} days"},
    ).fetchall()
    if not rows:
        return False, ("No new radar items since the last digest. "
                       "Run a scan on /radar first, then generate.")

    lines = []
    for i, r in enumerate(rows, 1):
        title = (r["title"] or "").strip()
        blurb = (r["blurb"] or "").strip().replace("\n", " ")
        if len(blurb) > 300:
            blurb = blurb[:300] + "…"
        label = r["source_label"] or r["source"] or "?"
        # Date the item so the digest can't present an older announcement as
        # this-week news (the drafting steps downstream inherit the framing).
        pub = (r["published_at"] or "").strip()[:10]
        stamp = f" (published {pub})" if pub else ""
        lines.append(f"{i}. [{label}] {title}{stamp}"
                     + (f" — {blurb}" if blurb else ""))

    parts = [f"{len(rows)} new item{'s' if len(rows) != 1 else ''} since the last digest."]
    if prior_md:
        parts.append(
            "\nPREVIOUSLY REPORTED (do not repeat unless there is genuine new "
            "movement):\n" + prior_md
        )
    parts.append("\nNEW ITEMS:\n" + "\n".join(lines))
    user_message = "\n".join(parts)

    result, err = llm.complete(model=RADAR_DIGEST_MODEL,
                               system=TREND_DIGEST_SYSTEM_PROMPT,
                               user=user_message, max_tokens=4000, temperature=0.3)
    if err:
        return False, err
    digest_text = result.text
    if result.truncated:
        digest_text += "\n\n*(digest hit the length cap and was cut off — raise max_tokens in _generate_trend_digest)*"

    # Resolve the [#N] citations the model left to real URLs from the pile we
    # numbered. Only in-range items with a plain http(s) URL make the list.
    sources = []
    for n in _extract_cited_positions(digest_text):
        if 1 <= n <= len(rows):
            r = rows[n - 1]
            url = (r["url"] or "").strip()
            if url.lower().startswith(("http://", "https://")):
                sources.append({
                    "n": n,
                    "title": r["title"] or url,
                    "url": url,
                    "label": r["source_label"] or r["source"] or "",
                })

    conn.execute(
        "INSERT INTO radar_digests "
        "(model, digest_md, source_item_count, sources_json, prior_digest_id) "
        "VALUES (:model, :md, :count, :sources, :prior)",
        {"model": RADAR_DIGEST_MODEL, "md": digest_text, "count": len(rows),
         "sources": json.dumps(sources), "prior": prior_id},
    )
    conn.commit()

    # The row above keeps the original text (the /radar page renders sources_json
    # itself), but the RETURNED text carries its URLs inline — the fleet agent
    # persists this to Supabase, where sources_json doesn't travel, so without
    # this the dashboard shows [#N] markers that point nowhere.
    url_by_n = {s["n"]: s["url"] for s in sources}
    if url_by_n:
        def _linkify(m):
            marks = []
            for tok in re.findall(r"\d+", m.group(1)):
                n = int(tok)
                url = url_by_n.get(n)
                marks.append(f"[#{n}]({url})" if url else f"[#{n}]")
            return " ".join(marks)
        digest_text = _DIGEST_CITE_RE.sub(_linkify, digest_text)
        digest_text += "\n\n### Sources\n" + "\n".join(
            f"- [#{s['n']}] [{s['title']}]({s['url']})"
            + (f" · {s['label']}" if s["label"] else "")
            for s in sources)
    return True, digest_text


@bp.route("/radar/digest")
def radar_digest():
    conn = radar_db.get_connection()
    radar_db.ensure_schema(conn)
    digests = [_digest_with_sources(r) for r in conn.execute(
        "SELECT * FROM radar_digests ORDER BY id DESC LIMIT 12"
    ).fetchall()]
    latest = digests[0] if digests else None
    prior = digests[1:] if len(digests) > 1 else []
    cutoff = latest["generated_at"] if latest else "0000-00-00 00:00:00"
    return render_template(
        "radar/digest.html",
        latest=latest,
        prior=prior,
        new_count=_count_new_since(conn, cutoff),
        gen_error=request.args.get("error") or "",
        gen_success=request.args.get("success") or "",
    )


@bp.route("/radar/digest/generate", methods=["POST"])
def radar_digest_generate():
    conn = radar_db.get_connection()
    radar_db.ensure_schema(conn)
    ok, message = _generate_trend_digest(conn)
    if ok:
        return redirect(url_for("radar.radar_digest",
                                success="Digest generated from the current radar pile."))
    return redirect(url_for("radar.radar_digest", error=message))
