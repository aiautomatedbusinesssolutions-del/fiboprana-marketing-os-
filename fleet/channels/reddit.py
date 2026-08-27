"""Reddit channel adapter — wraps the existing reply_finder scanner and normalizes
its targets into the channel-agnostic candidate shape the reply store consumes.

This is the only place that knows Reddit specifics: the RSS scan (via reply_finder),
deriving a thread's external_id from its /comments/<id>/ URL, and fetching the full
post body at draft time (the scan itself only carries a short excerpt).
"""

import json
import re
import urllib.request

from radar.sources.rss import USER_AGENT
from reply_finder.config import load_config
from reply_finder.scanner import scan_for_reply_targets
from fleet.channels.base import ChannelAdapter

_COMMENTS_RE = re.compile(r"/comments/([a-z0-9]+)", re.IGNORECASE)


def _external_id(url):
    """The Reddit thread id (the base36 after /comments/), or None. Stable per
    thread, so it's the dedup key the candidates table upserts on."""
    if not url:
        return None
    m = _COMMENTS_RE.search(url)
    return m.group(1) if m else None


def _fetch_json(url, timeout=20):
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


class RedditAdapter(ChannelAdapter):
    name = "reddit"

    def scan(self):
        """Run the reply_finder RSS scan and normalize each ranked target into a
        candidate dict. Returns (candidates, summary)."""
        targets, summary = scan_for_reply_targets(load_config())
        return [self._to_candidate(t) for t in targets], summary

    def _to_candidate(self, t):
        sub = t.get("subreddit")
        flips = t.get("flips") or []
        return {
            "platform": "reddit",
            "external_id": _external_id(t.get("url")),
            "post_url": t.get("url"),
            "community": f"r/{sub}" if sub else None,
            "post_title": t.get("title"),
            "post_excerpt": t.get("blurb"),
            "post_author": t.get("author"),
            "post_created_at": t.get("published_at"),
            "finder_score": t.get("score"),
            # Normalize the scanner's (group, phrase) tuples into plain strings here,
            # so the store + everything downstream stay channel-agnostic.
            "flips": ", ".join(phrase for _group, phrase in flips) or None,
            "soft_triggers": ", ".join(t.get("soft_triggers") or []) or None,
            "angle": t.get("angle"),
            "flip_group": t.get("flip_group") or (flips[0][0] if flips else None),
            "finder_signal": {
                "age_hours": t.get("age_hours"),
                "flip_phrases": [list(f) for f in flips],
            },
        }

    def fetch_body(self, candidate):
        """The thread's selftext, from the public <permalink>.json. Used at DRAFT
        time so the drafter sees the whole post, not just the RSS excerpt.
        Best-effort: returns None on any hiccup (throttle, link post, deleted), and
        the drafter falls back to post_excerpt."""
        url = candidate.get("post_url")
        if not url:
            return None
        try:
            raw = _fetch_json(url.rstrip("/") + "/.json")
            post = raw[0]["data"]["children"][0]["data"]
            return (post.get("selftext") or "").strip() or None
        except Exception:  # noqa: BLE001 — the body is a nice-to-have, never fatal
            return None
