"""Exa API client factory for the sourcing_web module.

Mirrors sourcing/reddit_client.py:get_reddit_client() — validates the env
var upfront and raises SourcingWebConfigError if missing. The route
catches this and renders a banner instead of 500ing.
"""

import os

from exa_py import Exa


class SourcingWebConfigError(Exception):
    """Raised when EXA_API_KEY is missing or rejected."""


def get_exa_client():
    api_key = (os.environ.get("EXA_API_KEY") or "").strip()
    if not api_key:
        raise SourcingWebConfigError(
            "EXA_API_KEY not set. Add it to .env and restart the app. "
            "See EXA_GUIDE.md for setup."
        )
    return Exa(api_key=api_key)
