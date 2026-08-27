"""Click event recording.

Called from the local /r/<code> redirect route after the short_link is
resolved (the PUBLIC redirect on fiboprana.com logs its own clicks from
the app side — same table, same hashing scheme). Stores timestamp, hashed
IP, user-agent, and referer. The IP hash uses a per-day salt so the same
visitor across multiple days isn't trivially correlatable in storage —
within a day, the hash is stable enough to count rough unique visitors;
across days it rotates.
"""

import hashlib
from datetime import datetime, timezone

from . import store


def _hash_ip(ip):
    """sha256(ip + today's UTC date). Returns the hex digest, or None if no ip."""
    if not ip:
        return None
    salt = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return hashlib.sha256(f"{ip}|{salt}".encode("utf-8")).hexdigest()


def _client_ip(request):
    """Extract the visitor's IP. Honors X-Forwarded-For if it's set (one hop
    only — first value in the list), otherwise falls back to remote_addr."""
    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        return xff.split(",", 1)[0].strip()
    return request.remote_addr


def record_click(short_link_id, request):
    """Insert a link_clicks row (clicked_at is DB-defaulted)."""
    ip = _client_ip(request)
    store.record_click({
        "short_link_id": short_link_id,
        "ip_hash": _hash_ip(ip),
        "user_agent": (request.headers.get("User-Agent") or "")[:500],
        "referer": (request.headers.get("Referer") or "")[:500],
    })
