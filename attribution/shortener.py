"""Short-code generation + UTM-aware destination URL assembly.

Codes are 6-character base62 (~57 billion combinations) — generated with
secrets.choice for unpredictability and checked for collision against
short_links.code before insert. At realistic volumes (thousands of links)
collisions are vanishingly rare; the retry loop is defensive.

URL assembly preserves any query string already on the destination and
appends UTM params from the short_link row, so the final URL the visitor
hits carries the standard utm_source / utm_medium / utm_campaign /
utm_content / utm_term tags that downstream analytics expect.

NOTE: the Fiboprana app's public redirect (src/app/r/[code]/route.ts in
the fiboprana-site repo) re-implements build_destination_url in TypeScript —
if the merge rules here ever change, change them there too.
"""

import secrets
import string

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from . import store


CODE_ALPHABET = string.ascii_letters + string.digits  # 62 chars
DEFAULT_CODE_LENGTH = 6
MAX_GENERATION_ATTEMPTS = 10


class ShortCodeCollisionError(RuntimeError):
    """Raised when we can't find an unused short code within MAX_GENERATION_ATTEMPTS.

    Effectively impossible at v1 volumes, but better a clear failure than a
    silent infinite loop if the table ever fills up unexpectedly.
    """


def generate_code(length=DEFAULT_CODE_LENGTH):
    """Return a base62 code not already present in short_links.code."""
    for _ in range(MAX_GENERATION_ATTEMPTS):
        code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
        if not store.code_exists(code):
            return code
    raise ShortCodeCollisionError(
        f"Could not generate a unique {length}-char code after "
        f"{MAX_GENERATION_ATTEMPTS} attempts."
    )


UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term")


def build_destination_url(short_link):
    """Take a short_links row (dict-like) and return the URL to 302 to.

    Merges the row's UTM params into the destination's existing query string.
    If the destination already carries a utm_* value, the row's value wins
    (so editing a link's campaign in the dashboard changes what visitors see).
    Empty UTM values are skipped.

    Non-UTM params on the destination are preserved as a list of pairs (not a
    dict) so duplicates like ?tag=one&tag=two survive verbatim — collapsing to
    a dict would silently drop the first value.
    """
    destination = (short_link["destination"] or "").strip()
    if not destination:
        return ""

    parsed = urlparse(destination)
    # Keep duplicates: parse_qsl returns list[tuple]. We drop only the UTM keys
    # we're about to override and keep everything else as-is.
    pairs = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
             if k not in UTM_KEYS]

    for key in UTM_KEYS:
        val = (short_link[key] or "").strip() if short_link[key] is not None else ""
        if val:
            pairs.append((key, val))

    new_query = urlencode(pairs)
    return urlunparse(parsed._replace(query=new_query))


def build_short_url(base_url, code):
    """Compose the full short URL ('https://host/r/<code>') the user copies."""
    base = (base_url or "").rstrip("/")
    return f"{base}/r/{code}"
