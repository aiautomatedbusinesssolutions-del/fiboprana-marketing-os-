"""Hacker News Algolia search. Free, no auth, JSON over HTTP.

Returns normalized item dicts ready for the radar_items insert. Filtering
by lookback_days happens server-side via numericFilters on created_at_i,
so we don't fetch and discard.
"""

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

API = "https://hn.algolia.com/api/v1/search_by_date"
USER_AGENT = "fiboprana-radar/0.1"


def search(query, hits_per_query=20, lookback_days=30):
    """Run one HN search. Returns list[dict] in the radar_items shape."""
    cutoff = int((datetime.now(timezone.utc) - timedelta(days=lookback_days)).timestamp())
    params = {
        "query": query,
        "tags": "story",
        "hitsPerPage": str(hits_per_query),
        "numericFilters": f"created_at_i>{cutoff}",
    }
    url = f"{API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8"))

    # Algolia typo-tolerance makes short queries match near-anything ("Oura" ->
    # "our", "HRV" -> "HRM"), and search_by_date then hands back the 20 newest of
    # those. Keep only hits where the query actually appears as a whole word in
    # the title or story text (2026-09-03 fix: the front page was leaking in).
    word = re.compile(r"(?<![A-Za-z0-9])" + re.escape(query) + r"(?![A-Za-z0-9])", re.I)

    items = []
    for hit in data.get("hits", []):
        haystack = f"{hit.get('title') or ''} {hit.get('story_text') or ''}"
        if not word.search(haystack):
            continue
        # External link when present; fall back to the HN comments page.
        story_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not story_url:
            continue
        items.append({
            "url": story_url,
            "title": (hit.get("title") or "").strip(),
            "blurb": (hit.get("story_text") or "")[:500],
            "source": "hn",
            "source_label": query,
            "author": hit.get("author"),
            "published_at": hit.get("created_at"),  # already ISO8601
            "score": hit.get("points"),
        })
    return items
