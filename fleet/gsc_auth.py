r"""Google Search Console API OAuth — stdlib only, mirrors fleet/youtube_auth.py.

A SEPARATE refresh token from the YouTube one, on purpose: refresh tokens are
scope-bound, and the YouTube token now carries write scope that the video
upload pipeline depends on — re-minting it to add a scope risks breaking that
grant on a mis-click. GSC gets its own read-only token instead; both reuse the
same GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET (one GCP project).

One-time bootstrap (prereq: enable "Google Search Console API" on the GCP
project first, console.cloud.google.com -> APIs & Services):

    python -m fleet.gsc_auth

Open the printed URL and consent AS THE ACCOUNT THAT OWNS THE GSC PROPERTIES
(the one that verified fiboprana.com — likely the main
Google account, NOT any Brand Account a YouTube consent used).
The catcher appends GSC_OAUTH_REFRESH_TOKEN to .env (bytes append — never
Set-Content, see the .env BOM gotcha) and proves the grant by listing the
sites it can see. Tokens are never printed. For cloud runs, copy
GSC_OAUTH_REFRESH_TOKEN to Railway alongside the GOOGLE_OAUTH_* vars.

Library use (the metrics agent imports this):

    from fleet.gsc_auth import sites, search_analytics
"""

import json
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

from fleet.youtube_auth import _append_env, _env, _token_request

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
# Different port than youtube_auth's 8399 so both catchers can coexist.
REDIRECT = "http://127.0.0.1:8398/"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
API = "https://www.googleapis.com/webmasters/v3"


def access_token():
    """Refresh-token -> short-lived access token (the recurring-agent path)."""
    out = _token_request({
        "client_id": _env("GOOGLE_OAUTH_CLIENT_ID"),
        "client_secret": _env("GOOGLE_OAUTH_CLIENT_SECRET"),
        "refresh_token": _env("GSC_OAUTH_REFRESH_TOKEN"),
        "grant_type": "refresh_token",
    })
    return out["access_token"]


def _request(url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url, data=data,
        headers={"Authorization": f"Bearer {access_token()}",
                 "Content-Type": "application/json"},
        method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def sites():
    """Every property the credential can see, verified ones only."""
    entries = _request(f"{API}/sites").get("siteEntry") or []
    return [s for s in entries
            if s.get("permissionLevel") != "siteUnverifiedUser"]


def search_analytics(site_url, body):
    """POST searchAnalytics/query for one property."""
    return _request(
        f"{API}/sites/{urllib.parse.quote(site_url, safe='')}/searchAnalytics/query",
        body)


# ── one-time bootstrap ───────────────────────────────────────────────────────
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
    print("Open this URL and consent as the account that OWNS the GSC "
          "properties (not the Brand Account):\n")
    print(auth, flush=True)
    server = HTTPServer(("127.0.0.1", 8398), _Catcher)
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
        raise SystemExit("no refresh_token in response — retry with prompt=consent")
    _append_env("GSC_OAUTH_REFRESH_TOKEN", out["refresh_token"])
    print("refresh token written to .env")
    for s in sites():
        print(f"credential sees property: {s['siteUrl']} "
              f"({s['permissionLevel']})")


if __name__ == "__main__":
    bootstrap()
