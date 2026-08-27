"""The business wiki loader — the fleet's open-book exam (WIKI_PLAN.md).

Agents study the relevant page BEFORE they act, instead of re-deriving brand/market
facts or carrying a stale copy baked into a prompt string. A page is plain markdown
with a small frontmatter block; agents load one by its `id` (a path relative to the
vault, e.g. "brand/voice"), NEVER the whole vault — selective retrieval is where the
token savings live (WIKI_PLAN.md, "the retrieval contract").

stdlib only (no PyYAML): the frontmatter we use is a handful of `key: value` lines
plus simple `[a, b]` lists, so a tiny line parser is enough and stays dependency-free
(fleet/AGENT_PLAYBOOK.md principle 6).

Usage:
    from fleet import wiki
    voice = wiki.load("brand/voice")          # body text, frontmatter stripped
    meta, body = wiki.read("brand/voice")     # both, when you need `consumers` etc.
"""

from pathlib import Path

# The vault is the top-level wiki/ folder (a sibling of fleet/). Obsidian opens this
# same folder as its own vault; agents read it as plain files. One source, two views.
VAULT = Path(__file__).resolve().parent.parent / "wiki"

# Everything below a line that is exactly this marker is human-only provenance
# (source links, tuning notes) — read() keeps it, load() drops it. This lets a page
# carry the verbatim block a prompt interpolates AND its context, on one page, without
# the context leaking into the prompt.
HUMAN_MARKER = "<!-- human -->"


class WikiError(Exception):
    """A page id that doesn't resolve to a file under the vault."""


def _page_path(page_id: str) -> Path:
    """Resolve a page id ("brand/voice") to wiki/brand/voice.md, refusing any id that
    escapes the vault (no "..", no absolute paths)."""
    rel = page_id.strip().strip("/")
    if not rel:
        raise WikiError("empty page id")
    path = (VAULT / rel).with_suffix(".md")
    resolved = path.resolve()
    if VAULT not in resolved.parents:
        raise WikiError(f"page id {page_id!r} escapes the vault")
    return resolved


def _parse_frontmatter(text: str):
    """Split a page into (meta dict, body). A page with no `---` block returns
    ({}, whole text). Values are strings, bools, or simple one-line [a, b] lists —
    enough for id/title/tags/consumers/canonical/source_of_truth/last_verified."""
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end].strip("\n")
    body = text[end + 4:].lstrip("\n")
    meta = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        meta[key.strip()] = _coerce(raw.strip())
    return meta, body


def _coerce(raw: str):
    """Turn a frontmatter value string into a Python value: [a, b] -> list, true/false
    -> bool, null/'' -> None, else the unquoted string."""
    if raw.startswith("[") and raw.endswith("]"):
        inner = raw[1:-1].strip()
        return [p.strip().strip("'\"") for p in inner.split(",") if p.strip()]
    low = raw.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "none", ""):
        return None
    return raw.strip("'\"")


def read(page_id: str):
    """(meta, body) for a page. Raises WikiError if the page doesn't exist."""
    path = _page_path(page_id)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise WikiError(f"cannot read wiki page {page_id!r}: {e}") from e
    return _parse_frontmatter(text)


def load(page_id: str, *, default=None) -> str:
    """The model-facing text of a page: frontmatter stripped, and everything below the
    HUMAN_MARKER dropped — this is what you interpolate into a prompt. On a missing
    page, return `default` if given, else re-raise: a caller that wants the agent to
    degrade gracefully passes default="" (mirrors reply_drafter's style-guide
    fallback); one that wants a loud failure (e.g. a compliance page) lets it raise."""
    try:
        _, body = read(page_id)
    except WikiError:
        if default is not None:
            return default
        raise
    marker_at = body.find("\n" + HUMAN_MARKER)
    if marker_at != -1:
        body = body[:marker_at]
    elif body.strip() == HUMAN_MARKER:
        body = ""
    return body.strip()


def consumers(page_id: str):
    """The `consumers` list from a page's frontmatter (which agents depend on it), or
    [] if unset. Lets us see who to re-check before changing a page."""
    meta, _ = read(page_id)
    val = meta.get("consumers")
    return list(val) if isinstance(val, list) else ([val] if val else [])
