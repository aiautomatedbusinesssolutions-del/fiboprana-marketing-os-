"""Aggregates for the attribution dashboard.

v1 did this with SQL over local SQLite. v2's data lives in Supabase and the
volumes are tiny (hundreds of links, thousands of clicks at most), so the
helpers fetch rows through attribution/store.py and aggregate in plain
Python — one HTTP call per helper beats a Postgres view's worth of moving
parts at this scale. store.fetch_all_clicks caps at 10k rows; when that
ever bites, promote these to a view (and celebrate).
"""

from datetime import datetime, timedelta, timezone

from . import store


def _display_ts(iso):
    """'2026-08-08T19:04:11.123456+00:00' -> '2026-08-08 19:04' for templates."""
    if not iso:
        return iso
    return iso[:16].replace("T", " ")


def list_links(include_archived=False, limit=200):
    """short_links rows + their click_count / last_clicked_at, newest first."""
    links = store.fetch_links(include_archived=include_archived, limit=limit)
    clicks = store.fetch_all_clicks()
    count, last = {}, {}
    for c in clicks:  # newest first: the first hit per link is its latest
        lid = c["short_link_id"]
        count[lid] = count.get(lid, 0) + 1
        last.setdefault(lid, c["clicked_at"])
    for link in links:
        link["click_count"] = count.get(link["id"], 0)
        link["last_clicked_at"] = _display_ts(last.get(link["id"]))
        link["created_at"] = _display_ts(link["created_at"])
    return links


def link_clicks(short_link_id, limit=500):
    """Individual click rows for one link, newest first."""
    rows = store.fetch_clicks_for_link(short_link_id, limit=limit)
    for r in rows:
        r["clicked_at"] = _display_ts(r["clicked_at"])
    return rows


def summary():
    """Dashboard headline numbers."""
    links = store.fetch_links(include_archived=False, limit=10000)
    clicks = store.fetch_all_clicks()
    now = datetime.now(timezone.utc)

    def since(days):
        cutoff = (now - timedelta(days=days)).isoformat()
        return sum(1 for c in clicks if c["clicked_at"] >= cutoff)

    return {
        "total_links":  len(links),
        "total_clicks": len(clicks),
        "clicks_7d":    since(7),
        "clicks_30d":   since(30),
    }


def clicks_by_source(include_archived=False):
    """Click totals grouped by utm_source. Links with zero clicks still show,
    so a channel you've published into but that never pulls stays visible."""
    links = store.fetch_links(include_archived=include_archived, limit=10000)
    clicks = store.fetch_all_clicks()
    count = {}
    for c in clicks:
        count[c["short_link_id"]] = count.get(c["short_link_id"], 0) + 1

    by_source = {}
    for link in links:
        source = (link.get("utm_source") or "").strip() or "(unset)"
        by_source[source] = by_source.get(source, 0) + count.get(link["id"], 0)

    return [{"source": s, "click_count": n}
            for s, n in sorted(by_source.items(),
                               key=lambda kv: (-kv[1], kv[0]))]


def clicks_per_day(days=30):
    """One row per day for the last N days with click counts. Days with zero
    clicks are still returned so the dashboard can render a complete strip."""
    clicks = store.fetch_all_clicks()
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=days - 1)
    counts = {}
    for c in clicks:
        day = c["clicked_at"][:10]
        counts[day] = counts.get(day, 0) + 1

    out = []
    for i in range(days):
        d = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        out.append({"day": d, "n": counts.get(d, 0)})
    return out
