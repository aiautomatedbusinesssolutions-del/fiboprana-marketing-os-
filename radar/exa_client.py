"""Exa API client factory for the radar module.

Mirrors sourcing_web/exa_client.py. Same env var (EXA_API_KEY); separate
error class so the radar route can render a radar-specific banner.
"""

import os

from exa_py import Exa


class RadarConfigError(Exception):
    """Raised when EXA_API_KEY is missing or rejected."""


def get_exa_client():
    api_key = (os.environ.get("EXA_API_KEY") or "").strip()
    if not api_key:
        raise RadarConfigError(
            "EXA_API_KEY not set. Add it to .env and restart the app. "
            "See EXA_GUIDE.md for setup."
        )
    return Exa(api_key=api_key)
