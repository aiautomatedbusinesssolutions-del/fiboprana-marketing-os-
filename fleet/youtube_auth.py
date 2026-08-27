r"""YouTube Data API OAuth for the Fiboprana channel â€” stdlib only.

One-time bootstrap (run it, then open the printed URL and consent AS the
Fiboprana channel â€” the Brand Account appears in Google's account chooser):

    python -m fleet.youtube_auth

It starts a loopback catcher on 127.0.0.1:8399, waits for Google's redirect,
exchanges the code, appends GOOGLE_OAUTH_REFRESH_TOKEN to .env (bytes append â€”
never Set-Content, see the .env BOM gotcha), and proves the grant by printing
the channel it can see. Tokens are never printed.

Library use (the repurposer/metrics collectors import this):

    from fleet.youtube_auth import access_token
    token = access_token()   # fresh short-lived bearer from the refresh token

Scope was youtube.readonly from 2026-07-30 to 2026-08-07; founder upgraded it
to write access (youtube + youtube.upload) so the fleet can upload/schedule
videos, edit metadata, and set thumbnails. Every mutation still happens only
on explicit founder direction â€” nothing in the fleet auto-publishes.

2026-08-23: two scopes added for the personal-brand-workflow ports â€”
youtube.force-ssl (the CTA comment watcher posts the pinned-comment CTA;
comments accept ONLY this scope) and yt-analytics.readonly (video_outcomes
reads first-7-day numbers + the Reporting API's thumbnail-CTR reports).
âš  The existing refresh token predates them â€” both agents skip honestly with
a "re-run the bootstrap" message until the founder re-consents once:
    python -m fleet.youtube_auth
(the new refresh token replaces the old line in .env automatically).
"""

import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
SCOPE = ("https://www.googleapis.com/auth/youtube "
         "https://www.googleapis.com/auth/youtube.upload "
         "https://www.googleapis.com/auth/youtube.force-ssl "
         "https://www.googleapis.com/auth/yt-analytics.readonly")
REDIRECT = "http://127.0.0.1:8399/"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"


def _env(key):
    val = os.environ.get(key)
    if val:
        return val
    # .env without python-dotenv so library callers stay dependency-free.
    for line in ENV.read_bytes().decode("utf-8-sig").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError(f"{key} missing â€” run the bootstrap (module docstring)")


def _token_request(fields):
    body = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(TOKEN_URL, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def access_token():
    """Refresh-token -> short-lived access token (the recurring-agent path)."""
    out = _token_request({
        "client_id": _env("GOOGLE_OAUTH_CLIENT_ID"),
        "client_secret": _env("GOOGLE_OAUTH_CLIENT_SECRET"),
        "refresh_token": _env("GOOGLE_OAUTH_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    })
    return out["access_token"]


def api_get(path, **params):
    """GET a YouTube Data API v3 endpoint with the channel credential."""
    qs = urllib.parse.urlencode(params, doseq=True)
    req = urllib.request.Request(
        f"https://www.googleapis.com/youtube/v3/{path}?{qs}",
        headers={"Authorization": f"Bearer {access_token()}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


# â”€â”€ one-time bootstrap â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def _append_env(key, value):
    # Replace an existing line (a scope upgrade re-runs the bootstrap and the
    # FIRST match wins in _env) or append; always byte-level (BOM gotcha).
    data = ENV.read_bytes()
    lines = data.split(b"\n")
    prefix = f"{key}=".encode()
    replaced = False
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            lines[i] = prefix + value.encode()
            replaced = True
    if replaced:
        ENV.write_bytes(b"\n".join(lines))
        return
    if not data.endswith(b"\n"):
        data += b"\n"
    ENV.write_bytes(data + f"{key}={value}\n".encode())


class _Catcher(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        _Catcher.code = urllib.parse.parse_qs(
            urllib.parse.urlparse(self.path).query).get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        ok = _Catcher.code is not None
        self.wfile.write(b"<h2>%s</h2>" % (
            b"Consent received - you can close this tab." if ok
            else b"No code in redirect - check the terminal."))

    def log_message(self, *a):  # keep the terminal clean
        pass


def bootstrap():
    auth = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": _env("GOOGLE_OAUTH_CLIENT_ID"),
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
    })
    print("Open this URL and consent as the Fiboprana channel:\n")
    print(auth, flush=True)
    server = HTTPServer(("127.0.0.1", 8399), _Catcher)
    while _Catcher.code is None:
        server.handle_request()
    out = _token_request({
        "client_id": _env("GOOGLE_OAUTH_CLIENT_ID"),
        "client_secret": _env("GOOGLE_OAUTH_CLIENT_SECRET"),
        "code": _Catcher.code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT,
    })
    if "refresh_token" not in out:
        raise SystemExit("no refresh_token in response â€” retry with prompt=consent")
    _append_env("GOOGLE_OAUTH_REFRESH_TOKEN", out["refresh_token"])
    print("refresh token written to .env")
    chan = api_get("channels", part="snippet", mine="true")
    for c in chan.get("items", []):
        print(f"credential sees channel: {c['snippet']['title']} ({c['id']})")


if __name__ == "__main__":
    bootstrap()
