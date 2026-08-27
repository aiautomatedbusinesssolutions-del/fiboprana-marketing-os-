"""X (Twitter) channel adapter — Grok as the search eyes, nothing more.

⚠ BUILT 2026-07-22, NOT YET LIVE-TESTED: needs XAI_API_KEY (founder adds it
off-stream). Built against the documented contract (docs.x.ai: POST
/v1/responses + the x_search server tool, model grok-4.5). First run with a
real key is the acceptance test.

Division of labor (locked in the reply playbook): xAI's x_search tool is
currently the one affordable door that can SEARCH live X posts, so Grok finds
and returns raw candidates. Claude (or whatever MODEL_JUDGE says) stays the
judge; the drafter, gate, store, and CLI are untouched — this file is the
only X-specific surface, per the channel-adapter contract.

Config (all env, founder-swappable like every agent):
  XAI_API_KEY       activates the adapter; without it scan() soft-skips so the
                    reddit channel keeps running.
  MODEL_X_SEARCH    the xAI model driving the search (default grok-4.5). Note:
                    this knob swaps between xAI models; a NON-xAI model can't
                    power x_search — X search is a capability of xAI's API,
                    not a property of any model. A different search door
                    (X API search, browser) = a new adapter, same seam.
  REPLY_CHANNELS    set to "reddit,x" to enable this channel in the daily run.

The hunt itself encodes the deeper-pain thesis: the target is
analysis-done-but-can't-act posts — people who KNOW the options and can't
pull the trigger — plus beginner buy/sell questions and portfolio-anxiety
posts. Regular accounts, not big educators; fresh (48h); modest engagement so
a reply can actually be seen.
"""

import json
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone

from fleet import llm
from fleet.channels.base import ChannelAdapter

XAI_RESPONSES_URL = "https://api.x.ai/v1/responses"
WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "x_watchlist.md")


def _watchlist_section():
    """Curated-accounts paragraph for the hunt prompt (ported from the
    personal-brand workflow's list-driven replying, 2026-08-22). The founder
    curates handles in fleet/x_watchlist.md; empty/missing file = open hunt,
    exactly as before. Curation substitutes for part of the visibility floor:
    a known-good account's fresh on-target post is worth surfacing even at
    moderate reach."""
    try:
        with open(WATCHLIST_FILE, encoding="utf-8-sig") as f:
            handles = [line.strip().lstrip("@") for line in f
                       if line.strip() and not line.strip().startswith("#")]
    except OSError:
        return ""
    if not handles:
        return ""
    return ("PRIORITY ACCOUNTS (founder-curated watchlist): check these "
            "accounts' posts from the window FIRST and include any on-target "
            "post of theirs even at moderate visibility - curation stands in "
            "for the visibility floor here (all other hard requirements still "
            "apply): " + ", ".join("@" + h for h in handles[:40]) + "\n\n")

_STATUS_ID_RE = re.compile(r"/status(?:es)?/(\d+)")

HUNT_PROMPT = """\
Search X for posts from the LAST 48 HOURS that a thoughtful practitioner-peer
(someone who meditates, breathes, tracks, and thinks about mind-body signals)
could genuinely help by replying, AND where a reply will actually be SEEN.
Target content, in priority order:

1. Tracker/score anxiety and self-observation posts: the wearable is making
   them feel worse, not better ("my sleep score is stressing me out",
   "checking my ring first thing every morning", readiness-score dread).
2. Practice-doubt questions asked sincerely ("been meditating for months,
   can't tell if it's doing anything", "am I doing this right", "should I
   keep going").
3. Burnout / wired-and-tired posts from people who track everything and
   still feel off ("optimized everything, still exhausted", "mind won't
   shut off at 2am", "years of data, no idea what it means").

Hard requirements:
- STANDALONE original posts only. Never quote-reposts, never replies, never
  posts whose meaning depends on a quoted post, link, image, or video you
  can't fully see. When in doubt, skip.
- NOTHING political. Skip any post that mentions or reacts to politicians,
  parties, elections, or policy fights — even as a joke or backdrop.
- NOTHING medical or crisis-adjacent. Skip posts about diagnoses,
  medication, therapy decisions, self-harm, or acute mental-health crisis —
  a reply from us does not belong there.
- VISIBILITY: a reply only matters if people scroll past it. Prefer posts
  already being seen — thousands of views, an active reply section, or an
  author whose posts reliably draw engagement. Skip near-zero-view posts
  from tiny accounts no matter how well the content fits.
- Regular people and wellness-adjacent accounts, not brands or engagement
  farmers. Skip anything that smells like bait, a giveaway, a supplement
  pitch, a miracle-protocol promo, or a thread-promo.
- English only.

{watchlist_section}Return the 8 best as STRICT JSON only, no prose around it:
{"posts": [{"url": "<full x.com status url>", "handle": "<author handle>",
  "text": "<the post text, verbatim>", "posted_at": "<when, if known>",
  "views": <int or null>, "likes": <int or null>, "replies": <int or null>,
  "reposts": <int or null>, "author_followers": <int or null>,
  "is_quote": <true if it quotes/reposts another post, else false>,
  "fit_score": <0-100 how well it matches the targets>,
  "why": "<one sentence: which target it matches and why a reply gets seen>"}]}
"""


def _extract_text(data):
    """The assistant text out of a Responses-API payload, defensively — walk
    output items for text content; fall back to the output_text convenience."""
    parts = []
    for item in data.get("output") or []:
        for block in item.get("content") or []:
            if isinstance(block, dict) and block.get("text"):
                parts.append(block["text"])
    if not parts and data.get("output_text"):
        parts.append(data["output_text"])
    return "\n".join(parts).strip()


def _parse_posts(text):
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        obj = json.loads(text[start:end + 1])
    except ValueError:
        return None
    posts = obj.get("posts")
    return posts if isinstance(posts, list) else None


class XAdapter(ChannelAdapter):
    name = "x"

    def scan(self):
        key = os.environ.get("XAI_API_KEY")
        if not key:
            return [], {"skipped": "XAI_API_KEY not set — X finder inactive "
                                   "(add the key, then REPLY_CHANNELS=reddit,x)"}
        model = os.environ.get("MODEL_X_SEARCH", "grok-4.5")
        today = datetime.now(timezone.utc).date()
        # Own-account handle comes from the environment; empty means "no own
        # account yet" and the exclusion filter is simply omitted.
        own = os.environ.get("X_USERNAME", "")
        x_search_tool = {
            "type": "x_search",
            "from_date": str(today - timedelta(days=2)),
            "to_date": str(today),
        }
        if own:
            x_search_tool["excluded_x_handles"] = [own]  # never target our own posts
        body = {
            "model": model,
            "input": [{"role": "user",
                       "content": HUNT_PROMPT.replace(
                           "{watchlist_section}", _watchlist_section())}],
            "tools": [x_search_tool],
        }
        req = urllib.request.Request(
            XAI_RESPONSES_URL, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        usage = data.get("usage") or {}
        llm.record_usage(model=data.get("model") or model,
                         tokens_input=usage.get("input_tokens") or 0,
                         tokens_output=usage.get("output_tokens") or 0,
                         cost_usd=None)   # xAI pricing not in PRICES — unpriced, visible

        text = _extract_text(data)
        posts = _parse_posts(text)
        if posts is None:
            raise ValueError(f"could not parse x_search JSON from: {text[:200]}")

        candidates = []
        for p in posts:
            url = (p.get("url") or "").split("?")[0].strip()
            m = _STATUS_ID_RE.search(url)
            if not m:
                continue                     # no status id -> not a real post link
            if p.get("is_quote"):
                continue                     # quote-RTs carry unseen context (learned
                                             # 2026-07-23: a "portfolio anxiety" post
                                             # was a political quote-RT underneath)
            handle = (p.get("handle") or "").lstrip("@").strip()
            text_ = (p.get("text") or "").strip()
            if not text_:
                continue
            metrics = {k: p.get(k) for k in
                       ("views", "likes", "replies", "reposts", "author_followers")
                       if p.get(k) is not None}
            title = text_.splitlines()[0][:80]
            candidates.append({
                "platform": "x",
                "external_id": m.group(1),
                "post_url": url,
                "community": f"x/{handle}" if handle else "x",
                "post_title": title,
                "post_excerpt": text_,
                "post_author": handle or None,
                "post_created_at": p.get("posted_at") or None,
                "finder_score": p.get("fit_score"),
                "flips": None,
                "soft_triggers": None,
                "angle": p.get("why"),
                "flip_group": None,
                "finder_signal": {"source": "xai_x_search", "model": model,
                                  "why": p.get("why"),
                                  "metrics": metrics or None},
            })
        candidates.sort(key=lambda c: -(c.get("finder_score") or 0))
        return candidates, {"returned": len(posts), "usable": len(candidates)}

    # fetch_body: default None is right — a tweet's excerpt IS the full text.
