"""PRAW client factory. Read-only script-app mode (no user auth).

Raises SourcingConfigError when creds are missing or obviously malformed.
The scanner route catches this and renders a clear banner, matching the
pattern used for ANTHROPIC_API_KEY elsewhere.
"""

import os

import praw


class SourcingConfigError(Exception):
    """Raised when Reddit credentials are missing or rejected."""


def get_reddit_client():
    client_id = os.environ.get("REDDIT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "").strip()
    user_agent = os.environ.get("REDDIT_USER_AGENT", "").strip()

    missing = [k for k, v in [
        ("REDDIT_CLIENT_ID", client_id),
        ("REDDIT_CLIENT_SECRET", client_secret),
        ("REDDIT_USER_AGENT", user_agent),
    ] if not v]
    if missing:
        raise SourcingConfigError(
            "Missing Reddit credentials in .env: " + ", ".join(missing)
            + ". See README.md for setup."
        )

    return praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        check_for_async=False,
    )
