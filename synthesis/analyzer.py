"""Layer-1 aggregations over the observations corpus.

These are the cheap, deterministic stats that run on every dashboard page
load — no AI, no per-query cost. Complements the Layer-2 AI synthesis
(in synthesizer.py) which produces narrative pattern descriptions.

All functions accept a connection to the capture/ database (where the
observations table lives), not the synthesis runs DB. The route handler
passes the right one.
"""

from collections import Counter


def observation_count(conn):
    """Total observations in the corpus."""
    row = conn.execute("SELECT COUNT(*) AS n FROM observations").fetchone()
    return row["n"] if row else 0


def recent_observation_count(conn, days=30):
    """Observations added in the last N days (by date_observed)."""
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM observations "
        "WHERE date_observed >= date('now', :delta)",
        {"delta": f"-{days} days"},
    ).fetchone()
    return row["n"] if row else 0


def tag_frequency(conn, limit=30):
    """Return [(tag, count)] sorted by count desc.

    Tags column is comma-separated text; split and tally in Python rather
    than fighting SQL string manipulation. Comma-separated columns are the
    convention across the project (see capture/README.md).
    """
    rows = conn.execute(
        "SELECT tags FROM observations WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall()

    counter = Counter()
    for r in rows:
        for raw in (r["tags"] or "").split(","):
            tag = raw.strip().lower()
            if tag:
                counter[tag] += 1
    return counter.most_common(limit)


def source_breakdown(conn):
    """Return [(source, count)] sorted by count desc.

    Sources are case- and whitespace-normalized for grouping so that
    'reddit' / 'Reddit' / ' reddit ' collapse to one row. Empty/NULL
    sources surface as '(unset)'.
    """
    rows = conn.execute(
        "SELECT COALESCE(NULLIF(LOWER(TRIM(source)), ''), '(unset)') AS source, "
        "       COUNT(*) AS n "
        "FROM observations "
        "GROUP BY COALESCE(NULLIF(LOWER(TRIM(source)), ''), '(unset)') "
        "ORDER BY n DESC, source ASC"
    ).fetchall()
    return [(r["source"], r["n"]) for r in rows]


def product_insight_observations(conn):
    """Return observations tagged `product-insight`, newest first.

    Per DECISIONS.md (2026-04-22): observations tagged `product-insight`
    have feature implications that are directly actionable for the
    Fiboprana app — this view aggregates them so recurring opportunities
    surface without re-reading the whole corpus.
    """
    rows = conn.execute(
        "SELECT id, date_observed, segment_guess, feature_implications, tags "
        "FROM observations "
        "WHERE tags LIKE '%product-insight%' "
        "ORDER BY date_observed DESC, id DESC"
    ).fetchall()
    return rows


def days_since_observation_added(conn, since_iso):
    """How many observations have been added (by date_observed) since the
    given ISO date? Used to flag stale syntheses on the dashboard."""
    if not since_iso:
        return observation_count(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM observations WHERE date_observed > :since",
        {"since": since_iso[:10]},  # accept full datetime or just date
    ).fetchone()
    return row["n"] if row else 0
