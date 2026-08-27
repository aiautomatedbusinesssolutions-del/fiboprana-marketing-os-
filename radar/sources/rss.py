"""Minimal RSS / Atom reader using stdlib xml.etree.

Avoids a feedparser dependency. Sturdy enough for the major mainstream
feeds we care about (NerdWallet, Morning Brew, etc.). If a feed breaks
parsing, swap in feedparser just for that one source later.
"""

import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

ATOM_NS = "{http://www.w3.org/2005/Atom}"
DC_NS = "{http://purl.org/dc/elements/1.1/}"
# Some feeds (e.g. Mr. Money Mustache behind Cloudflare) 403 a bot-looking
# user-agent. A normal browser UA + an RSS-friendly Accept header gets through;
# these requests only read public feed XML, nothing authenticated.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ACCEPT = "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5"


def _fetch(url, timeout=20):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _parse_date(s):
    """Try RFC822 (RSS) then ISO8601 (Atom). Return aware datetime or None."""
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
        if d is not None:
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        pass
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _strip_html(text):
    """Crude tag stripper for RSS descriptions. Most feeds embed HTML; we
    only need a short blurb, so a regex-free approach is fine."""
    if not text:
        return ""
    out, in_tag = [], False
    for ch in text:
        if ch == "<":
            in_tag = True
        elif ch == ">":
            in_tag = False
        elif not in_tag:
            out.append(ch)
    return "".join(out).strip()


def _parse_rss_items(root, feed_name):
    items = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        if not link:
            continue
        pub = _parse_date(item.findtext("pubDate"))
        items.append({
            "url": link,
            "title": (item.findtext("title") or "").strip(),
            "blurb": _strip_html(item.findtext("description") or "")[:500],
            "source": "rss",
            "source_label": feed_name,
            "author": item.findtext(f"{DC_NS}creator"),
            "published_at": pub.isoformat() if pub else None,
            "score": None,
        })
    return items


def _atom_article_link(entry):
    """Pick the entry's article URL. Atom entries often carry several <link>
    elements (rel='self', rel='edit', enclosure, etc.); rel='alternate' or
    no rel is the article link. Falls back to the first link only if no
    alternate exists."""
    links = entry.findall(f"{ATOM_NS}link")
    if not links:
        return ""
    for el in links:
        rel = el.get("rel")
        if rel is None or rel == "alternate":
            href = el.get("href")
            if href:
                return href
    return links[0].get("href") or ""


def _parse_atom_entries(root, feed_name):
    items = []
    for entry in root.iter(f"{ATOM_NS}entry"):
        link = _atom_article_link(entry)
        if not link:
            continue
        summary = (entry.findtext(f"{ATOM_NS}summary") or
                   entry.findtext(f"{ATOM_NS}content") or "")
        pub = _parse_date(entry.findtext(f"{ATOM_NS}published") or
                          entry.findtext(f"{ATOM_NS}updated"))
        items.append({
            "url": link,
            "title": (entry.findtext(f"{ATOM_NS}title") or "").strip(),
            "blurb": _strip_html(summary)[:500],
            "source": "rss",
            "source_label": feed_name,
            "author": None,
            "published_at": pub.isoformat() if pub else None,
            "score": None,
        })
    return items


def read(feed_name, url, lookback_days=30):
    """Fetch + parse one feed. Filters out items older than lookback_days
    when the feed exposes a date; undated items are kept (better to surface
    once than to lose them silently)."""
    xml_bytes = _fetch(url)
    root = ET.fromstring(xml_bytes)

    items = _parse_rss_items(root, feed_name)
    if not items:
        items = _parse_atom_entries(root, feed_name)

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    out = []
    for it in items:
        if it["published_at"]:
            d = _parse_date(it["published_at"])
            if d is not None and d < cutoff:
                continue
        out.append(it)
    return out
