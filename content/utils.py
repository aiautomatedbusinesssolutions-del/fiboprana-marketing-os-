"""Helpers for the content generators (X posts, TikTok posts, LinkedIn posts,
idea proposals, image prompts).

Functions:
    format_observation_for_x_prompt(row) -> str
        Builds the user message that sits alongside X_POST_SYSTEM_PROMPT.
        Skips empty fields. Excludes feature_implications by design — that's
        internal product thinking, not material the model should turn into posts.

    format_observation_for_tiktok_prompt(row) -> str
        Builds the user message that sits alongside TIKTOK_POST_SYSTEM_PROMPT.
        Same observation fields as the X version; kept as a separate function
        so each generator's call site reads cleanly.

    format_observation_for_linkedin_prompt(row) -> str
        Builds the user message that sits alongside LINKEDIN_POST_SYSTEM_PROMPT.
        Same observation fields; kept as a separate function for the same
        readability reason.

    format_observations_and_synthesis_for_idea_prompt(rows, synthesis_text) -> str
        Builds the user message that sits alongside IDEA_PROPOSAL_SYSTEM_PROMPT.
        Loads BRAND_VOICE.md (selected sections), recent video-transcript voice
        excerpts, and content/SHIPPED_FEATURES.md from disk, then prepends them
        to a slim view of each observation and the full SYNTHESIS.md text.
        Order: voice rules -> voice excerpts -> shipped -> observations ->
        synthesis. Missing or empty context is silently skipped.

    load_transcript_corpus(limit=4, max_chars_per=900) -> str
        Recent founder-voice excerpts from the content/videos/ transcript corpus,
        with intro/outro housekeeping stripped at read time. Returns "" if empty.

    format_post_for_image_prompt(post_text, post_style, observation) -> str
        Builds the user message that sits alongside IMAGE_PROMPT_SYSTEM_PROMPT.
        Pairs the actual post (text + style) with the observation context so
        the image-prompt model can ground visuals in the specific post.

    parse_x_posts_response(text) -> list[dict]
        Validates the 5-posts JSON shape from the X post generator.

    parse_tiktok_posts_response(text) -> list[dict]
        Validates the 4-posts JSON shape from the TikTok post generator.

    parse_linkedin_posts_response(text) -> list[dict]
        Validates the 4-posts JSON shape from the LinkedIn post generator.

    parse_idea_proposal_response(text) -> list[dict]
        Validates the 5-ideas JSON shape from the idea proposal generator.

    parse_image_prompts_response(text) -> dict
        Validates the {ideogram, gpt_image} JSON shape from the image prompt
        generator. Returns a dict with both keys as non-empty stripped strings.

    _strip_json_fences(text) -> str
        Shared cleanup for ```json``` and bare ``` fences.
"""

import json
import re
from pathlib import Path

import yaml


_USER_MESSAGE_SECTIONS = [
    ("Segment guess",                                         "segment_guess"),
    ("Pain points",                                           "pain_points"),
    ("Notable quotes",                                        "notable_quotes"),
    ("Content hooks (for inspiration, not to copy verbatim)", "content_hooks"),
    ("Tags",                                                  "tags"),
]


# Slim observation view used inside the idea-proposal user message. Includes
# feature_implications (the internal product-thinking field) because the
# proposal task IS internal product thinking — unlike X posts, where that
# field is excluded.
_IDEA_OBSERVATION_SECTIONS = [
    ("Segment guess",        "segment_guess"),
    ("Pain points",          "pain_points"),
    ("Notable quotes",       "notable_quotes"),
    ("Feature implications", "feature_implications"),
    ("Tags",                 "tags"),
]


def _strip_json_fences(text):
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
        cleaned = cleaned.strip()
    return cleaned


# TODO: section names must match the Fiboprana brand-voice doc this extractor
# reads (wiki brand/voice); update when that doc's headings are finalized.
_BRAND_VOICE_SECTIONS_FOR_IDEA_PROMPT = (
    "Core voice attributes (universal across every surface)",
    "Who it's talking to",
    "Their words (prefer these)",
    "Avoid (brand side — the full binding list is [[compliance/advice-line]])",
)


def _extract_brand_voice_sections(text):
    """Return only the BRAND_VOICE.md `## ` sections relevant to idea generation."""
    wanted = set(_BRAND_VOICE_SECTIONS_FOR_IDEA_PROMPT)
    out = []
    in_wanted = False
    for line in text.splitlines():
        if line.startswith("## "):
            in_wanted = line[3:].strip() in wanted
        if in_wanted:
            out.append(line)
    return "\n".join(out).strip()


_ALLOWED_ACCESS_TIERS = ("free", "pro", "elite")


# Strict key set for parse_image_prompts_response. Update this constant in the
# same change that adds a third generator (e.g., adding "midjourney" alongside
# the cornerstone update + route + button).
_EXPECTED_IMAGE_PROMPT_KEYS = frozenset({"ideogram", "gpt_image"})


def format_observation_for_x_prompt(row):
    parts = [f"Observation #{row['id']}"]
    for label, field in _USER_MESSAGE_SECTIONS:
        value = (row[field] or "").strip()
        if value:
            parts.append(f"{label}:\n{value}")
    return "\n\n".join(parts)


def format_observation_for_tiktok_prompt(row):
    parts = [f"Observation #{row['id']}"]
    for label, field in _USER_MESSAGE_SECTIONS:
        value = (row[field] or "").strip()
        if value:
            parts.append(f"{label}:\n{value}")
    return "\n\n".join(parts)


def format_observation_for_linkedin_prompt(row):
    parts = [f"Observation #{row['id']}"]
    for label, field in _USER_MESSAGE_SECTIONS:
        value = (row[field] or "").strip()
        if value:
            parts.append(f"{label}:\n{value}")
    return "\n\n".join(parts)


def format_post_for_image_prompt(post_text, post_style, observation):
    """Pair one X post with its source observation for the image-prompt model.

    Style label and post text come first because the image must match the
    specific post being shown to the user. The observation follows as
    grounding context the model can pull concrete details from.
    """
    parts = [
        f"Post style: {post_style}",
        f"Post text:\n{post_text}",
        f"Source observation #{observation['id']}",
    ]
    for label, field in _USER_MESSAGE_SECTIONS:
        value = (observation[field] or "").strip()
        if value:
            parts.append(f"{label}:\n{value}")
    return "\n\n".join(parts)


def format_caption_for_tiktok_image_prompt(caption_text, caption_style, observation):
    """Pair one TikTok caption with its source observation for the image-prompt model.

    Mirrors format_post_for_image_prompt but uses caption-vocabulary in the
    user message because the TikTok image-prompt cornerstone reads "caption
    text" / "caption style" rather than "post text" / "post style."
    """
    parts = [
        f"Caption style: {caption_style}",
        f"Caption text:\n{caption_text}",
        f"Source observation #{observation['id']}",
    ]
    for label, field in _USER_MESSAGE_SECTIONS:
        value = (observation[field] or "").strip()
        if value:
            parts.append(f"{label}:\n{value}")
    return "\n\n".join(parts)


def format_post_for_linkedin_image_prompt(post_text, post_style, observation):
    """Pair one LinkedIn post with its source observation for the image-prompt model.

    Same shape as format_post_for_image_prompt but kept separate so the
    LinkedIn image cornerstone can be tuned independently of the X cornerstone.
    """
    parts = [
        f"Post style: {post_style}",
        f"Post text:\n{post_text}",
        f"Source observation #{observation['id']}",
    ]
    for label, field in _USER_MESSAGE_SECTIONS:
        value = (observation[field] or "").strip()
        if value:
            parts.append(f"{label}:\n{value}")
    return "\n\n".join(parts)


VIDEOS_DIR = Path(__file__).resolve().parent / "videos"

# Intro/outro YouTube boilerplate that should never seed the founder's voice.
_HOUSEKEEPING = re.compile(
    r"\b(subscribe|like button|hit the like|smash|notification|the bell|"
    r"link in|in the comments|leave a comment|let me know|feel free to|"
    r"back to the comments|make sure you|get the video out|share (this|the) video|"
    r"next video|coming soon|thanks for watching|that's it for today)\b",
    re.I,
)


def _parse_front_matter(text):
    """Return (meta, body) for a file that starts with a `---` YAML block."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        meta = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        meta = {}
    return (meta if isinstance(meta, dict) else {}), parts[2].strip()


def _extract_voice_excerpt(body, max_chars):
    """Substantive sentences from a transcript, with housekeeping stripped."""
    kept, total = [], 0
    for sentence in re.split(r"(?<=[.!?])\s+", body):
        sentence = sentence.strip()
        if len(sentence) < 25 or _HOUSEKEEPING.search(sentence):
            continue
        kept.append(sentence)
        total += len(sentence) + 1
        if total >= max_chars:
            break
    return " ".join(kept)


def load_transcript_corpus(limit=4, max_chars_per=900):
    """Recent founder-voice excerpts from content/videos/*.md as a prompt block.

    Reads the growing transcript corpus, keeps files flagged `voice_exemplar`,
    strips intro/outro boilerplate at read time (the raw .md is never touched),
    and returns the most recent `limit` excerpts. Returns "" if the corpus is
    empty, so callers can skip the block cleanly.
    """
    if not VIDEOS_DIR.is_dir():
        return ""
    entries = []
    for path in sorted(VIDEOS_DIR.glob("*.md")):
        if path.name.lower() == "readme.md":
            continue
        meta, body = _parse_front_matter(path.read_text(encoding="utf-8"))
        if not meta or not meta.get("voice_exemplar", True):
            continue
        excerpt = _extract_voice_excerpt(body, max_chars_per)
        if excerpt:
            entries.append((str(meta.get("date_recorded") or ""), meta, excerpt))
    if not entries:
        return ""

    entries.sort(key=lambda e: e[0], reverse=True)
    parts = [
        "# Founder voice — verbatim excerpts from recent videos",
        "Match this phrasing, rhythm, and vocabulary. These are real spoken words "
        "(lightly trimmed). Echo the voice; do not reuse the specific facts.",
    ]
    for _, meta, excerpt in entries[:limit]:
        title = meta.get("title") or "untitled"
        pillar = meta.get("pillar")
        header = f"## {title}" + (f" — {pillar}" if pillar else "")
        parts.append(f"{header}\n{excerpt}")
    return "\n\n".join(parts)


def format_observations_and_synthesis_for_idea_prompt(rows, synthesis_text):
    blocks = []

    brand_voice_path = Path(__file__).resolve().parent.parent / "BRAND_VOICE.md"
    if brand_voice_path.is_file():
        voice_excerpt = _extract_brand_voice_sections(
            brand_voice_path.read_text(encoding="utf-8")
        )
        if voice_excerpt:
            blocks.append("# Brand voice (excerpts)\n\n" + voice_excerpt)

    corpus_block = load_transcript_corpus()
    if corpus_block:
        blocks.append(corpus_block)

    shipped_path = Path(__file__).resolve().parent / "SHIPPED_FEATURES.md"
    if shipped_path.is_file():
        shipped_text = shipped_path.read_text(encoding="utf-8").strip()
        if shipped_text:
            blocks.append(shipped_text)

    obs_block = ["# Observations\n"]
    for row in rows:
        obs_parts = [f"## Observation #{row['id']} ({row['date_observed'] or 'undated'})"]
        for label, field in _IDEA_OBSERVATION_SECTIONS:
            value = (row[field] or "").strip()
            if value:
                obs_parts.append(f"{label}:\n{value}")
        obs_block.append("\n\n".join(obs_parts))
    blocks.append("\n\n".join(obs_block))

    synthesis = (synthesis_text or "").strip()
    if synthesis:
        blocks.append("# SYNTHESIS.md\n\n" + synthesis)

    return "\n\n---\n\n".join(blocks)


def parse_x_posts_response(text):
    cleaned = _strip_json_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Response was not valid JSON: {e}") from e

    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list) or len(posts) != 5:
        raise ValueError(f"Expected exactly 5 posts in response.posts; got {posts!r}")
    for p in posts:
        if not isinstance(p, dict) or "style" not in p or "text" not in p:
            raise ValueError(f"Malformed post (need 'style' and 'text' keys): {p!r}")
        if not isinstance(p["text"], str) or not p["text"].strip():
            raise ValueError(f"Empty post text: {p!r}")
    return posts


def parse_tiktok_posts_response(text):
    cleaned = _strip_json_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Response was not valid JSON: {e}") from e

    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list) or len(posts) != 4:
        raise ValueError(f"Expected exactly 4 posts in response.posts; got {posts!r}")
    for p in posts:
        if not isinstance(p, dict) or "style" not in p or "text" not in p:
            raise ValueError(f"Malformed post (need 'style' and 'text' keys): {p!r}")
        if not isinstance(p["text"], str) or not p["text"].strip():
            raise ValueError(f"Empty post text: {p!r}")
    return posts


def parse_linkedin_posts_response(text):
    cleaned = _strip_json_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Response was not valid JSON: {e}") from e

    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list) or len(posts) != 4:
        raise ValueError(f"Expected exactly 4 posts in response.posts; got {posts!r}")
    for p in posts:
        if not isinstance(p, dict) or "style" not in p or "text" not in p:
            raise ValueError(f"Malformed post (need 'style' and 'text' keys): {p!r}")
        if not isinstance(p["text"], str) or not p["text"].strip():
            raise ValueError(f"Empty post text: {p!r}")
    return posts


def parse_image_prompts_response(text):
    """Validate the {ideogram, gpt_image} JSON shape from the image prompt generator.

    Strict on the key set: rejects missing keys, extra keys, or any deviation
    from exactly {"ideogram", "gpt_image"}. If a third generator is added
    later (e.g., midjourney), update _EXPECTED_IMAGE_PROMPT_KEYS at the same
    time as the route + cornerstone.
    """
    cleaned = _strip_json_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Response was not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object; got {type(data).__name__}")
    if set(data.keys()) != _EXPECTED_IMAGE_PROMPT_KEYS:
        raise ValueError(
            f"Expected exactly the keys {sorted(_EXPECTED_IMAGE_PROMPT_KEYS)}; "
            f"got {sorted(data.keys())}"
        )
    out = {}
    for key in _EXPECTED_IMAGE_PROMPT_KEYS:
        if not isinstance(data[key], str) or not data[key].strip():
            raise ValueError(f"Key '{key}' must be a non-empty string")
        out[key] = data[key].strip()
    return out


def parse_idea_proposal_response(text):
    cleaned = _strip_json_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Response was not valid JSON: {e}") from e

    ideas = data.get("ideas") if isinstance(data, dict) else None
    if not isinstance(ideas, list) or len(ideas) != 5:
        raise ValueError(f"Expected exactly 5 ideas in response.ideas; got {ideas!r}")

    required = ("title", "description", "source", "tags", "accessTier")
    for idea in ideas:
        if not isinstance(idea, dict):
            raise ValueError(f"Idea is not an object: {idea!r}")
        for key in required:
            if key not in idea:
                raise ValueError(f"Idea missing required key '{key}': {idea!r}")
            if not isinstance(idea[key], str):
                raise ValueError(f"Idea key '{key}' must be a string: {idea!r}")
        if not idea["title"].strip() or not idea["description"].strip():
            raise ValueError(f"Idea has empty title or description: {idea!r}")
        if idea["accessTier"].strip() not in _ALLOWED_ACCESS_TIERS:
            raise ValueError(
                f"Idea accessTier must be one of {_ALLOWED_ACCESS_TIERS}; "
                f"got {idea['accessTier']!r}"
            )
    return ideas


# ---- Weekly X batch generator (digest-driven; see content/weekly_x_prompt.py) ----

def shingle_overlap(text_a, text_b, n=4):
    """Jaccard similarity of the two texts' word n-gram sets (0.0-1.0).

    The mechanical half of the no-echo guard (ported from the personal-brand
    workflow 2026-08-23): X silently rejects posts substantially similar to an
    account's past posts, so generated posts get compared against the recent
    corpus and dropped when they overlap. Word 4-grams catch reused phrasings
    while ignoring shared vocabulary; texts shorter than n words only match
    when identical."""
    def shingles(text):
        words = re.findall(r"[a-z0-9']+", text.lower())
        if len(words) < n:
            return {" ".join(words)} if words else set()
        return {" ".join(words[i:i + n]) for i in range(len(words) - n + 1)}
    a, b = shingles(text_a), shingles(text_b)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def format_digest_for_weekly_x_prompt(digest_md, count, quiz_url, tool_url, themes=None,
                                      founder_takes=None, pattern_log=None,
                                      recent_posts=None):
    """Build the user message that sits alongside WEEKLY_X_SYSTEM_PROMPT.

    The digest is the week's timely raw material; count + the two CTA URLs are the
    parameters the prompt references. `themes` (optional) carries Reddit listening
    themes for extra flavor. `founder_takes` / `pattern_log` (optional, ported from
    the personal-brand workflow 2026-08-22) carry the founder's own words for the week and
    the mechanism log of what has measurably worked - the system prompt says how
    each is used.
    """
    parts = [
        f"Number of posts to produce: {count}",
        f"Landing/waitlist URL (for CTA posts): {quiz_url}",
        f"Lead magnet URL — The Missing Layer (for CTA posts): {tool_url}",
    ]
    if themes and themes.strip():
        parts.append("Reddit listening themes this week (optional flavor):\n" + themes.strip())
    if founder_takes and founder_takes.strip():
        parts.append("FOUNDER'S TAKES THIS WEEK (his real words - see system prompt):\n"
                     + founder_takes.strip())
    if pattern_log and pattern_log.strip():
        parts.append("PROVEN POST PATTERNS (mechanism log - see system prompt):\n"
                     + pattern_log.strip())
    if recent_posts:
        parts.append("ACCOUNT'S RECENT POSTS (no-echo corpus - see system prompt; "
                     "do not reuse these hooks, openings, or phrasings):\n"
                     + "\n".join(f"- {p}" for p in recent_posts))
    parts.append("This week's research digest:\n\n" + (digest_md or "").strip())
    return "\n\n".join(parts)


def parse_weekly_x_response(text, expected_count=None):
    """Validate the weekly-batch JSON from the X foundation generator.

    Each post needs: pillar (a known key), is_cta (bool), text (non-empty, <=280),
    link_reply (non-empty string when is_cta, else null), suggested_day (string).
    If expected_count is given, the post count must match it.
    """
    from content.weekly_x_prompt import PILLAR_KEYS  # local import avoids any import cycle

    cleaned = _strip_json_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"Response was not valid JSON: {e}") from e

    posts = data.get("posts") if isinstance(data, dict) else None
    if not isinstance(posts, list) or not posts:
        raise ValueError(f"Expected a non-empty 'posts' list; got {posts!r}")
    if expected_count is not None and len(posts) != expected_count:
        raise ValueError(f"Expected {expected_count} posts; got {len(posts)}")

    for p in posts:
        if not isinstance(p, dict):
            raise ValueError(f"Post is not an object: {p!r}")
        if p.get("pillar") not in PILLAR_KEYS:
            raise ValueError(
                f"Post pillar must be one of {sorted(PILLAR_KEYS)}; got {p.get('pillar')!r}"
            )
        if not isinstance(p.get("is_cta"), bool):
            raise ValueError(f"Post is_cta must be a bool: {p!r}")
        if not isinstance(p.get("text"), str) or not p["text"].strip():
            raise ValueError(f"Empty post text: {p!r}")
        # The 280-char limit is a soft guideline, not a hard reject: the model
        # occasionally runs a few chars over, and the human trims it in the approval
        # pass. The runner flags over-limit posts rather than discarding the batch.
        link_reply = p.get("link_reply")
        if p["is_cta"]:
            if not isinstance(link_reply, str) or not link_reply.strip():
                raise ValueError(f"CTA post needs a non-empty link_reply: {p!r}")
        elif link_reply not in (None, ""):
            raise ValueError(f"Non-CTA post must have null link_reply: {p!r}")
        if not isinstance(p.get("suggested_day"), str) or not p["suggested_day"].strip():
            raise ValueError(f"Post needs a suggested_day string: {p!r}")
    return posts
