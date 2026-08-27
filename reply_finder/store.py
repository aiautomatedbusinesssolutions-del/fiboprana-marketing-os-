"""Persist sent replies to reply_log — the training dataset for the future
reply agent (the fleet).

This is the ONE interface both drivers use:
  * me, working in chat ("I replied to X with Y" -> log_reply_from_target(...))
  * a fleet agent later (imports this module, calls the same functions)

The scanner stays a pure function; nothing here touches scanning. Typical flow:

    from reply_finder.config import load_config
    from reply_finder.scanner import scan_for_reply_targets
    from reply_finder import store

    targets, _ = scan_for_reply_targets(load_config())
    rid = store.log_reply_from_target(
        targets[0],
        reply_text="...",                 # the hand-written reply (the moat)
        rationale="pre-decision flip; OP is asking buy/sell, turned it inward",
        replied_at="2026-06-23",
    )
    # ...days later, once engagement is in:
    store.update_outcome(rid, outcome_score=14, op_replied="yes", led_to_profile="unknown")
"""

from reply_finder import db


# Every writable column. Order is documentation, not enforcement.
_COLUMNS = [
    "replied_at", "platform",
    "post_url", "community", "post_title", "post_excerpt", "post_author",
    "finder_score", "flips", "soft_triggers", "angle",
    "reply_text", "reply_url", "rationale", "predicted_grade",
    "outcome_score", "op_replied", "led_to_profile", "outcome_notes", "outcome_checked_at",
    "idea_id",
]

# Outcome fields that update_outcome() is allowed to touch later.
_OUTCOME_FIELDS = ["outcome_score", "op_replied", "led_to_profile", "outcome_notes"]


def log_reply(**fields):
    """Insert one reply_log row. reply_text is required; everything else optional.

    Unknown keys are ignored (so a caller can pass a superset safely). Returns the
    new row id. Only non-None fields are written, so schema defaults (platform,
    timestamps) apply to anything omitted.
    """
    if not (fields.get("reply_text") or "").strip():
        raise ValueError("reply_text is required — the reply itself is the point.")

    row = {c: fields.get(c) for c in _COLUMNS if fields.get(c) is not None}
    cols = list(row.keys())
    placeholders = ", ".join(f":{c}" for c in cols)

    conn = db.get_connection()
    db.ensure_schema(conn)
    cur = conn.execute(
        f"INSERT INTO reply_log ({', '.join(cols)}) VALUES ({placeholders})", row
    )
    conn.commit()
    return cur.lastrowid


def log_reply_from_target(target, *, reply_text, rationale=None, replied_at=None,
                          reply_url=None, platform="reddit", idea_id=None,
                          post_excerpt=None, predicted_grade=None):
    """Map a scanner target dict (from scan_for_reply_targets) onto a reply_log row.

    Pulls the finder's signal (score, flips, soft_triggers, angle) straight off the
    target so the 'find replies better' loop has what it needs, then attaches the
    hand-written reply + reasoning. This is the call to prefer when the reply came
    off a scan.
    """
    flips = ", ".join(phrase for _grp, phrase in target.get("flips", []) or [])
    softs = ", ".join(target.get("soft_triggers", []) or [])
    sub = target.get("subreddit")
    return log_reply(
        replied_at=replied_at,
        platform=platform,
        post_url=target.get("url"),
        community=f"r/{sub}" if sub else None,
        post_title=target.get("title"),
        post_excerpt=post_excerpt,
        post_author=target.get("author"),
        finder_score=target.get("score"),
        flips=flips or None,
        soft_triggers=softs or None,
        angle=target.get("angle"),
        reply_text=reply_text,
        reply_url=reply_url,
        rationale=rationale,
        predicted_grade=predicted_grade,
        idea_id=idea_id,
    )


def update_outcome(reply_id, **fields):
    """Fill in / refresh the outcome of a reply after it's had time to land.

    Only the outcome fields are writable here; outcome_checked_at is stamped
    automatically. Returns the number of rows updated (0 if reply_id is unknown).
    """
    sets = {k: fields[k] for k in _OUTCOME_FIELDS if k in fields}
    if not sets:
        return 0
    assignments = ", ".join(f"{k} = :{k}" for k in sets)
    sets["id"] = reply_id

    conn = db.get_connection()
    db.ensure_schema(conn)
    cur = conn.execute(
        f"UPDATE reply_log SET {assignments}, "
        "outcome_checked_at = datetime('now'), updated_at = datetime('now') "
        "WHERE id = :id",
        sets,
    )
    conn.commit()
    return cur.rowcount


def recent_replies(limit=20, platform=None):
    """Return recent reply_log rows (newest first) — what the agent reads to learn.

    Ordered by replied_at then id, falling back to created_at for rows logged
    without an explicit replied_at.
    """
    conn = db.get_connection()
    db.ensure_schema(conn)
    sql = "SELECT * FROM reply_log"
    params = {}
    if platform:
        sql += " WHERE platform = :platform"
        params["platform"] = platform
    sql += " ORDER BY COALESCE(replied_at, created_at) DESC, id DESC LIMIT :limit"
    params["limit"] = limit
    return conn.execute(sql, params).fetchall()
