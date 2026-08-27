"""Exa search + persistence for the sourcing_web module.

Two entry points used by app.py:
    scan_with_templates(exa, config, conn) -> summary dict
    scan_with_query(exa, config, conn, adhoc_query) -> summary dict

Mirrors the shape of sourcing/scanner.py: per-iteration try/except around the
external API call so one bad query doesn't kill the whole scan.
"""

import hashlib
import json
import re
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse


# Capture the subreddit name from a reddit.com URL. Tolerates www., old., new.
_REDDIT_SUBREDDIT_RE = re.compile(
    r"^https?://(?:www\.|old\.|new\.)?reddit\.com/r/([A-Za-z0-9_]+)/?",
    re.IGNORECASE,
)

# Stripped from URLs before dedup comparison only — storage is unchanged.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term",
    "source", "ref", "ref_", "ref_src",
    "fbclid", "gclid",
})

# Domains where per-post permalinks (e.g. phpBB ?p=NNN) generate distinct
# URLs for content that is semantically the same source. Triggers the
# content-hash secondary dedup pass; do not add general-web blog domains
# here — quoting across blogs is a legitimate distinct source.
FORUM_DOMAINS = frozenset({"bogleheads.org", "reddit.com"})


def _parse_subreddit(url):
    if not url:
        return None
    m = _REDDIT_SUBREDDIT_RE.match(url)
    return m.group(1) if m else None


def _normalize_url(url):
    """Comparison-only canonical form: strip tracking params, fragment,
    trailing slash. Does not mutate stored URLs."""
    if not url:
        return url
    p = urlparse(url)
    query = urlencode([
        (k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True)
        if k not in _TRACKING_PARAMS
    ])
    path = p.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunparse((p.scheme, p.netloc, path, p.params, query, ""))


def _forum_domain_for(url):
    """Return the FORUM_DOMAINS entry this url's host matches (incl. subdomains),
    or None. www.bogleheads.org -> bogleheads.org; old.reddit.com -> reddit.com."""
    if not url:
        return None
    host = (urlparse(url).netloc or "").lower()
    for d in FORUM_DOMAINS:
        if host == d or host.endswith("." + d):
            return d
    return None


def _content_hash(domain, highlights):
    """sha256 of (domain + first highlight). Returns None if no highlight text
    is available — without content signal we can't safely call it a duplicate."""
    first = (highlights[0] if highlights else "").strip()
    if not first:
        return None
    return hashlib.sha256(f"{domain}\n{first}".encode("utf-8")).hexdigest()


def _already_seen(conn, url, highlights):
    """Three-pass dedup. Pass 1+2 catch URL variants; pass 3 catches phpBB-style
    per-post permalinks pointing at the same thread content."""
    # Pass 1: byte-exact (cheap, hits UNIQUE(url) index).
    if conn.execute(
        "SELECT 1 FROM web_results WHERE url = :url", {"url": url}
    ).fetchone() is not None:
        return True

    # Pass 2: normalized-URL comparison, scoped to rows on the same host.
    host = (urlparse(url).netloc or "").lower()
    norm_candidate = _normalize_url(url)
    if host:
        for (existing_url,) in conn.execute(
            "SELECT url FROM web_results WHERE url LIKE :pat",
            {"pat": f"%{host}%"},
        ):
            if _normalize_url(existing_url) == norm_candidate:
                return True

    # Pass 3: forum-only content-hash dedup.
    forum = _forum_domain_for(url)
    if forum is None:
        return False
    candidate_hash = _content_hash(forum, highlights)
    if candidate_hash is None:
        return False
    for (existing_url, existing_hl_json) in conn.execute(
        "SELECT url, highlights FROM web_results WHERE url LIKE :pat",
        {"pat": f"%{forum}%"},
    ):
        if _forum_domain_for(existing_url) != forum:
            continue
        try:
            existing_hl = json.loads(existing_hl_json or "[]")
        except (TypeError, ValueError):
            continue
        if _content_hash(forum, existing_hl) == candidate_hash:
            return True
    return False


def _insert_result(conn, result, query, query_kind, query_label):
    conn.execute(
        """
        INSERT INTO web_results (
            url, title, highlights, exa_score, published_date,
            query, query_kind, query_label, subreddit, status
        ) VALUES (
            :url, :title, :highlights, :exa_score, :published_date,
            :query, :query_kind, :query_label, :subreddit, 'new'
        )
        """,
        {
            "url": result.url,
            "title": getattr(result, "title", None) or "",
            "highlights": json.dumps(getattr(result, "highlights", None) or []),
            "exa_score": getattr(result, "score", None),
            "published_date": getattr(result, "published_date", None),
            "query": query,
            "query_kind": query_kind,
            "query_label": query_label,
            "subreddit": _parse_subreddit(result.url),
        },
    )


def _run_search(exa, search_cfg, query):
    """One Exa search-with-contents call. Returns a SearchResponse."""
    kwargs = {
        "type": search_cfg.get("type", "auto"),
        "num_results": search_cfg.get("num_results", 8),
        "highlights": True,
    }
    include = search_cfg.get("include_domains")
    if include:
        kwargs["include_domains"] = include
    max_age = search_cfg.get("max_age_hours")
    if max_age is not None:
        kwargs["max_age_hours"] = max_age
    return exa.search_and_contents(query, **kwargs)


def _persist(response, query, query_kind, query_label, conn, summary):
    for r in getattr(response, "results", None) or []:
        summary["results_returned"] += 1
        highlights = getattr(r, "highlights", None) or []
        if _already_seen(conn, r.url, highlights):
            summary["skipped_already_seen"] += 1
            continue
        _insert_result(conn, r, query, query_kind, query_label)
        summary["new_candidates"] += 1


def _new_summary(ad_hoc=False, adhoc_query=None):
    return {
        "templates_run": 0,
        "templates_failed": [],
        "results_returned": 0,
        "new_candidates": 0,
        "skipped_already_seen": 0,
        "ad_hoc": ad_hoc,
        "adhoc_query": adhoc_query,
    }


def scan_with_templates(exa, config, conn):
    """Run every template in config['templates']. Per-template failures are
    isolated so one bad query doesn't kill the whole scan.
    """
    summary = _new_summary()
    search_cfg = config.get("search", {}) or {}
    for template in (config.get("templates") or []):
        label = (template.get("label") or "(unnamed)").strip()
        query = (template.get("query") or "").strip()
        if not query:
            continue
        try:
            response = _run_search(exa, search_cfg, query)
            _persist(response, query, "template", label, conn, summary)
            summary["templates_run"] += 1
        except Exception as e:  # noqa: BLE001 — per-template isolation
            summary["templates_failed"].append((label, str(e)))
    conn.commit()
    return summary


def scan_with_query(exa, config, conn, adhoc_query):
    """Run a single ad-hoc query."""
    summary = _new_summary(ad_hoc=True, adhoc_query=adhoc_query)
    search_cfg = config.get("search", {}) or {}
    response = _run_search(exa, search_cfg, adhoc_query)
    _persist(response, adhoc_query, "adhoc", None, conn, summary)
    summary["templates_run"] = 1
    conn.commit()
    return summary
