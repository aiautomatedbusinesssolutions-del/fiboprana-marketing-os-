"""Content extraction for already-known URLs via Exa /contents.

Used by /sourcing/web/<id>/extract to prefill /observations/new?raw_content=...
For Reddit URLs this returns the page text without a flattened comment tree
(unlike sourcing/scanner.py:fetch_thread_markdown which uses PRAW). See the
module README for the trade-off.
"""

DEFAULT_MAX_CHARACTERS = 20000


def fetch_content(exa, url, max_characters=DEFAULT_MAX_CHARACTERS):
    """Fetch text via Exa /contents and wrap as a thin markdown blob."""
    response = exa.get_contents(
        [url],
        text={"max_characters": max_characters},
    )
    results = getattr(response, "results", None) or []
    if not results:
        raise RuntimeError(f"Exa returned no contents for URL: {url}")
    result = results[0]
    title = getattr(result, "title", None) or "(no title)"
    text = (getattr(result, "text", None) or "").strip() or "(no body)"
    return f"# {title}\nSource: {url}\n\n{text}"
