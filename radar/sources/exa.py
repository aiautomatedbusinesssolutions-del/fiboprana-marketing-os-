"""Exa neural search for radar discovery queries.

One Exa.search_and_contents call per query. num_results <= 10 keeps each
query at 1 credit on the free tier. start_published_date narrows to the
lookback window so we don't re-surface old content.
"""

from datetime import datetime, timedelta, timezone


def search(exa, query, num_results=10, lookback_days=30):
    """Run one Exa neural search. Returns list[dict] in the radar_items shape."""
    start = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d")
    resp = exa.search_and_contents(
        query,
        num_results=num_results,
        start_published_date=start,
        type="neural",
        highlights=True,
    )

    items = []
    for r in resp.results:
        url = getattr(r, "url", None)
        if not url:
            continue
        highlights = getattr(r, "highlights", None) or []
        blurb = highlights[0] if highlights else ""
        items.append({
            "url": url,
            "title": (getattr(r, "title", "") or "").strip(),
            "blurb": (blurb or "")[:500],
            "source": "exa",
            "source_label": query,
            "author": getattr(r, "author", None),
            "published_at": getattr(r, "published_date", None),
            "score": getattr(r, "score", None),
        })
    return items
