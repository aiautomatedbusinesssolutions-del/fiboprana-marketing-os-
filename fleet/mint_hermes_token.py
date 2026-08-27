"""Mint a long-lived JWT for the dedicated `hermes` Postgres role.

Run this ONCE, locally, as part of applying the scoped-role hardening
(supabase/migrations/20260625000001_scope_hermes_role.sql). It reads your
project's JWT secret from the environment and prints a token whose `role` claim is
`hermes`, so the agent acts as that scoped role instead of the public `anon` role.
Paste the printed token into your environment as SUPABASE_HERMES_JWT (local .env
and the Railway dashboard).

NOTE: this HS256 self-signing path does NOT work on projects with modern
ASYMMETRIC signing keys (confirmed 2026-06-25 by a live 401 PGRST301 on the
engine's original Supabase project — check yours before relying on it).
Use the secret-key-bound-to-a-custom-`hermes`-role path in fleet/SECURITY.md
instead. This script is kept only for legacy HS256 projects.

    # Git Bash / macOS / Linux:
    SUPABASE_JWT_SECRET=... python -m fleet.mint_hermes_token
    # PowerShell:
    $env:SUPABASE_JWT_SECRET="..."; python -m fleet.mint_hermes_token

Find the secret in Supabase -> Settings -> API -> JWT Settings -> "JWT Secret"
(legacy HS256 projects). If your project uses the newer ASYMMETRIC signing keys,
you cannot self-sign here: instead create a Supabase *secret* API key bound to a
custom `hermes` role in the dashboard and use that value as SUPABASE_HERMES_JWT.
See fleet/SECURITY.md for both paths.

Stdlib only (hmac/hashlib/base64/json) — no PyJWT dependency, per repo convention.
"""

import base64
import hashlib
import hmac
import json
import os
import sys
import time

# Ten years. This is a backend service credential, not a user session — rotate it
# by re-running this script and replacing SUPABASE_HERMES_JWT if it ever leaks.
TTL_SECONDS = 10 * 365 * 24 * 3600


def _b64url(raw):
    """Base64url with padding stripped, the way JWS encodes each segment."""
    return base64.urlsafe_b64encode(raw).rstrip(b"=")


def mint(secret, *, role="hermes", ttl=TTL_SECONDS, now=None):
    """Return a signed HS256 JWT carrying a `role` claim PostgREST will SET ROLE to."""
    issued = int(now if now is not None else time.time())
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {"role": role, "iss": "fiboprana-hermes", "iat": issued, "exp": issued + ttl}
    segments = [
        _b64url(json.dumps(header, separators=(",", ":")).encode("utf-8")),
        _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
    ]
    signing_input = b".".join(segments)
    signature = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    segments.append(_b64url(signature))
    return b".".join(segments).decode("ascii")


def main():
    secret = os.environ.get("SUPABASE_JWT_SECRET")
    if not secret:
        print("ERROR: set SUPABASE_JWT_SECRET first "
              "(Supabase -> Settings -> API -> JWT Settings -> \"JWT Secret\").",
              file=sys.stderr)
        return 1
    token = mint(secret)
    print(token)  # stdout: the token alone, so you can pipe/copy it cleanly
    print("\nSet this in your local .env and the Railway dashboard:\n"
          "  SUPABASE_HERMES_JWT=<the token above>\n"
          "Then apply migration 20260625000001 (see fleet/SECURITY.md).",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
