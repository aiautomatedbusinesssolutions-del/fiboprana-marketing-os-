"""Auto-attach tracking links to agent-drafted copy (v2.1, 2026-08-08).

The manual v2 flow (draft -> open /attribution/new -> copy the short URL ->
paste it back into the draft) never actually happened in practice, so agent
drafts carried bare fiboprana.com URLs and clicks went unmeasured. This
module closes the loop: every drafting lane that carries a link calls
rewrite_urls() at its finalize point and the copy leaves the repo already
tracked.

UTM vocabulary (the "right information per post" contract):
  utm_source   = the platform the copy ships on: x | email-newsletter | youtube
  utm_medium   = the slot type on that platform: post | email | description
  utm_campaign = the batch/lane identity: weekly-x-<monday> | newsletter-<week>
                 | video-<slug>
  utm_content  = the individual post within the campaign: slot-3 | cta |
                 longform-xpost  (what makes two posts in one campaign
                 distinguishable in analytics)

Rules:
  * Only fiboprana.com URLs are rewritten — external curation links (e.g.
    the newsletter's around-the-web section) pass through untouched, and
    /r/<code> links are never re-shortened.
  * mint() is IDEMPOTENT on (destination, source, medium, campaign,
    content): re-running a generator or re-assembling an email reuses the
    existing short link instead of minting a duplicate code, so click
    history accumulates on one row per logical link.
  * FAIL-OPEN: if Supabase is unreachable, rewrite_urls() returns the text
    unchanged and prints a warning — a flaky shortener must never cost a
    batch, an approval, or a newsletter send. The bare URL still works;
    only the tracking is lost.

CLI (for in-session agents drafting YouTube descriptions etc.):
    python -m attribution.autolink https://fiboprana.com/quiz \
        --source youtube --medium description --campaign video-my-slug
prints the short URL on stdout.
"""

import argparse
import re
import sys

from fleet.supabase import SupabaseError

from . import shortener, store

SHORT_BASE = "https://fiboprana.com/r/"
# fiboprana.com URLs in prose (optionally www.), any path, stopping before
# whitespace and common trailing punctuation.
_FIBOPRANA_URL_RE = re.compile(
    r"https?://(?:www\.)?fiboprana\.com(?:/[^\s)\]}>,]*)?", re.IGNORECASE
)
_TRAILING_PUNCT = ".,;:!?'\""


def mint(destination, *, source, medium, campaign, content=None, post_text=None):
    """Find-or-create the short link for this (destination x UTM) identity.
    Returns the full short URL."""
    match = {
        "destination": destination,
        "utm_source": source,
        "utm_medium": medium,
        "utm_campaign": campaign,
        "utm_content": content,
    }
    existing = store.find_link(match)
    if existing:
        return shortener.build_short_url("https://fiboprana.com", existing["code"])
    row = None
    for _ in range(3):  # same unique-code race retry as the dashboard create
        code = shortener.generate_code()
        try:
            row = store.insert_link({**match, "code": code,
                                     "post_text": (post_text or None)})
            break
        except SupabaseError as e:
            if "23505" not in str(e):
                raise
    if row is None:
        raise SupabaseError("could not mint a unique short code in 3 attempts")
    return shortener.build_short_url("https://fiboprana.com", row["code"])


def rewrite_urls(text, *, source, medium, campaign, content=None, post_text=None):
    """Replace every bare fiboprana.com URL in `text` with a tracked short
    link carrying the given UTM identity. Short links themselves (/r/...),
    non-fiboprana URLs, and everything else pass through untouched.
    Fail-open: on any Supabase error the original text comes back verbatim."""
    if not text:
        return text

    def _sub(match):
        url = match.group(0)
        # split trailing sentence punctuation off the URL so "…/quiz." works
        stripped = url.rstrip(_TRAILING_PUNCT)
        tail = url[len(stripped):]
        if "/r/" in stripped.lower():
            return url  # already a short link
        return mint(stripped, source=source, medium=medium, campaign=campaign,
                    content=content, post_text=post_text) + tail

    try:
        return _FIBOPRANA_URL_RE.sub(_sub, text)
    except SupabaseError as e:
        print(f"(autolink: leaving links untracked — {e})", file=sys.stderr)
        return text


def main():
    parser = argparse.ArgumentParser(
        description="Mint (or reuse) a tracked short link for a fiboprana.com URL."
    )
    parser.add_argument("destination")
    parser.add_argument("--source", required=True,
                        help="platform: x | email-newsletter | youtube | ...")
    parser.add_argument("--medium", required=True,
                        help="slot type: post | email | description | ...")
    parser.add_argument("--campaign", required=True,
                        help="lane identity, e.g. video-<slug>")
    parser.add_argument("--content", default=None,
                        help="per-post disambiguator within the campaign")
    args = parser.parse_args()
    print(mint(args.destination, source=args.source, medium=args.medium,
               campaign=args.campaign, content=args.content))
    return 0


if __name__ == "__main__":
    from dotenv import load_dotenv
    from pathlib import Path
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    raise SystemExit(main())
