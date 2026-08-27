"""Run all configured sources, dedupe by URL, insert into radar_items.

One scan = one HN call per hn_query + one HTTP fetch per rss_feed +
one Exa call per exa_query. Failures in one source don't kill the others;
errors are accumulated into the summary so the UI can show what broke.
"""

import re

from .sources import hn, rss, exa as exa_source


def _safe_url(url):
    """Reject anything that isn't a plain http(s) URL. Defends the rendered
    <a href> against javascript: / data: / file: schemes coming from a
    hostile feed or search result."""
    if not url:
        return False
    u = url.strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def _denylisted(item, keywords):
    """Drop items whose title or blurb matches any denylist entry.

    Single-word entries match with a LEADING word boundary only, so
    'trader' catches 'trader' / 'traders' but not 'tradition', and
    'leverage' catches 'leverage' / 'leveraged' / 'leveraging'. Multi-word
    phrases stay as plain case-insensitive substrings (what the user
    expects when they type 'options trading'). Empty list disables.
    """
    if not keywords:
        return False
    haystack = f"{item.get('title') or ''} {item.get('blurb') or ''}".lower()
    for kw in keywords:
        if not kw:
            continue
        kw_lower = kw.lower()
        if " " in kw_lower or "-" in kw_lower:
            if kw_lower in haystack:
                return True
        else:
            if re.search(rf"\b{re.escape(kw_lower)}", haystack):
                return True
    return False


def scan_all(conn, config, exa_client=None):
    summary = {
        "hn_fetched": 0,
        "rss_fetched": 0,
        "exa_fetched": 0,
        "filtered": 0,
        "inserted": 0,
        "duplicates": 0,
        "errors": [],
    }

    denylist = config.get("denylist_keywords") or []
    collected = []

    for q in config.get("hn_queries", []) or []:
        try:
            items = hn.search(
                q,
                hits_per_query=config.get("hn_hits_per_query", 20),
                lookback_days=config.get("lookback_days", 30),
            )
            summary["hn_fetched"] += len(items)
            collected.extend(items)
        except Exception as e:  # noqa: BLE001
            summary["errors"].append(f"HN '{q}': {e}")

    for feed in config.get("rss_feeds", []) or []:
        name = feed.get("name") or feed.get("url") or "(unnamed)"
        try:
            items = rss.read(
                name, feed["url"],
                lookback_days=config.get("lookback_days", 30),
            )
            summary["rss_fetched"] += len(items)
            collected.extend(items)
        except Exception as e:  # noqa: BLE001
            summary["errors"].append(f"RSS '{name}': {e}")

    if exa_client is not None:
        for q in config.get("exa_queries", []) or []:
            try:
                items = exa_source.search(
                    exa_client, q,
                    num_results=config.get("exa_results_per_query", 10),
                    lookback_days=config.get("lookback_days", 30),
                )
                summary["exa_fetched"] += len(items)
                collected.extend(items)
            except Exception as e:  # noqa: BLE001
                summary["errors"].append(f"Exa '{q}': {e}")

    for it in collected:
        if not _safe_url(it.get("url")):
            summary["filtered"] += 1
            continue
        if _denylisted(it, denylist):
            summary["filtered"] += 1
            continue
        cur = conn.execute(
            "INSERT OR IGNORE INTO radar_items "
            "(url, title, blurb, source, source_label, author, published_at, score) "
            "VALUES (:url, :title, :blurb, :source, :source_label, :author, :published_at, :score)",
            it,
        )
        if cur.rowcount:
            summary["inserted"] += 1
        else:
            summary["duplicates"] += 1
    conn.commit()
    return summary
