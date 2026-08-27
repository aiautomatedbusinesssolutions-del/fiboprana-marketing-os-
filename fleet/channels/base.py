"""The channel adapter contract — the one place the reply system is channel-specific.

An adapter does two channel-specific things and nothing else: scan() (fetch + rank +
normalize into candidate dicts) and fetch_body() (the full post text for drafting).
Everything downstream — judge, drafter, compliance gate, store, CLI — consumes the
normalized shape and never branches on channel.
"""

import abc

# The normalized candidate dict every adapter.scan() yields. reply_store.log_candidates
# consumes exactly these keys; the adapter owns the mapping from its channel's raw data.
CANDIDATE_FIELDS = (
    "platform", "external_id", "post_url", "community", "post_title",
    "post_excerpt", "post_author", "post_created_at",
    "finder_score", "flips", "soft_triggers", "angle", "flip_group", "finder_signal",
)


class ChannelAdapter(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def scan(self):
        """Return (candidates, summary): normalized candidate dicts (CANDIDATE_FIELDS),
        best-first, plus a dict of scan stats. The only required method."""

    def fetch_body(self, candidate):
        """Full post text for drafting — the scan carries only a short excerpt.
        Best-effort: return None when unavailable and the drafter falls back to the
        excerpt. Default: no extra body (override per channel)."""
        return None
