"""Channel adapters — the ONLY channel-specific surface in the reply system.

Each adapter turns a channel's posts into the channel-agnostic candidate shape in
base.CANDIDATE_FIELDS; the judge, drafter, gate, store, and CLI never branch on
channel. Add a channel = add an adapter here, nothing else.
"""

import os

from fleet.channels.reddit import RedditAdapter
from fleet.channels.x import XAdapter
from fleet.channels.youtube import YouTubeAdapter

_REGISTRY = {a.name: a for a in (RedditAdapter(), XAdapter(), YouTubeAdapter())}


def get_adapter(name):
    try:
        return _REGISTRY[name]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY))
        raise ValueError(f"unknown channel '{name}'; known: {known}")


def enabled_adapters():
    """Adapters named in the REPLY_CHANNELS env var (comma-separated). Defaults to
    'reddit' — the only production channel — so the cron does the right thing out
    of the box. An EMPTY value counts as unset (a blank REPLY_CHANNELS= line in
    .env silently disabled every channel on 2026-08-11: the finder ran nothing
    and still reported ok)."""
    names = os.environ.get("REPLY_CHANNELS") or "reddit"
    return [get_adapter(n.strip()) for n in names.split(",") if n.strip()]
