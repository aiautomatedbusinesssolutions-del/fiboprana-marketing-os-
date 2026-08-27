"""Minimal Supabase (PostgREST) client built on the standard library.

Why stdlib and not supabase-py: this repo's rule is stdlib over deps — it already
runs on sqlite3 + urllib with no `requests`. PostgREST is just HTTP + JSON, so a
thin urllib wrapper is enough and stays transparent and easy to edit. If you ever
want the official client, only this file changes.

All marketing tables live in the `marketing` Postgres schema (not `public`), so
every request carries the PostgREST schema-profile header — Accept-Profile for
reads, Content-Profile for writes. You must ALSO add `marketing` to
Supabase -> Settings -> API -> Exposed schemas, or these calls can't see the tables.

Auth: SUPABASE_URL + SUPABASE_ANON_KEY are read from the environment (your MCP
client or the Railway dashboard injects them; locally, your .env). No secrets here.

Optional hardening: if SUPABASE_HERMES_JWT is set, it is sent as the Bearer token
so the agent acts as the dedicated `hermes` Postgres role instead of the public
`anon` role (see supabase/migrations/20260625000001_scope_hermes_role.sql and
fleet/SECURITY.md). Unset -> behaves exactly as before (anon). The anon key is
still sent as the `apikey` header either way (the API gateway requires it).
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request

SCHEMA = "marketing"
REQUEST_TIMEOUT = 20  # seconds — never let a stalled PostgREST call hang the cron


class SupabaseError(RuntimeError):
    """A PostgREST request failed — network, auth, exposed-schema, or a constraint."""


def _config():
    url = os.environ.get("SUPABASE_URL")
    apikey = os.environ.get("SUPABASE_ANON_KEY")
    if not url or not apikey:
        raise SupabaseError(
            "SUPABASE_URL and SUPABASE_ANON_KEY must be set "
            "(MCP client config `env:` block in production, or your local .env)."
        )
    # Scoped credential when present: the agent acts as the `hermes` role rather
    # than public `anon`. Falls back to the anon key (pre-hardening behavior).
    bearer = os.environ.get("SUPABASE_HERMES_JWT") or apikey
    return url.rstrip("/"), apikey, bearer


def _request(method, table, *, params=None, body=None, prefer=None):
    base, apikey, bearer = _config()
    endpoint = f"{base}/rest/v1/{table}"
    if params:
        endpoint += "?" + urllib.parse.urlencode(params, doseq=True)

    headers = {
        "apikey": apikey,
        "Authorization": f"Bearer {bearer}",
        "Content-Type": "application/json",
        # Target the marketing schema instead of public (both directions).
        "Accept-Profile": SCHEMA,
        "Content-Profile": SCHEMA,
    }
    if prefer:
        headers["Prefer"] = prefer

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(endpoint, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        raise SupabaseError(f"{method} {table} -> HTTP {e.code}: {detail}") from e
    except urllib.error.URLError as e:
        raise SupabaseError(f"{method} {table} -> network error: {e.reason}") from e


def insert(table, row, *, returning=True):
    """Insert one row. Returns the inserted row (with DB-filled id/defaults) by default."""
    prefer = "return=representation" if returning else "return=minimal"
    result = _request("POST", table, body=row, prefer=prefer)
    if returning and isinstance(result, list):
        return result[0] if result else None
    return result


def _rows_by_key_signature(rows):
    """Group rows by their exact set of keys.

    PostgREST rejects a bulk insert whose objects don't all carry identical keys
    (PGRST102: "All object keys must match"). Scanned rows are ragged — each omits
    whatever optional fields it lacks — so we POST one uniform group per key set.
    Grouping (rather than padding missing keys with null) keeps the "omit a key ->
    the column DEFAULT applies" contract, so a NOT NULL DEFAULT column (e.g. a jsonb
    '{}') is never handed an illegal null. Order within each group is preserved.
    """
    groups = {}
    for row in rows:
        groups.setdefault(tuple(sorted(row)), []).append(row)
    return list(groups.values())


def upsert(table, rows, *, on_conflict, resolution="merge-duplicates", returning=True):
    """Insert rows, resolving conflicts on `on_conflict` instead of erroring.

    rows may be a single dict or a list. on_conflict is the comma-joined column
    list of a UNIQUE index to arbitrate on (e.g. "platform,external_id"); it must
    be a plain (non-partial) unique index — PostgREST can't target a partial one.
    resolution:
      'merge-duplicates'  -> UPDATE the existing row (a true upsert)
      'ignore-duplicates' -> leave the existing row untouched (INSERT ... DO NOTHING)
    With 'ignore-duplicates', PostgREST returns ONLY the newly inserted rows, so the
    caller can act on just what's new. Returns the affected rows (list).

    A ragged batch (rows with differing key sets) is split into uniform POSTs by key
    signature — PostgREST requires identical keys per request. The unique index
    arbitrates conflicts globally, so splitting never affects dedup.
    """
    prefer = f"resolution={resolution}," + (
        "return=representation" if returning else "return=minimal"
    )
    if isinstance(rows, dict):
        rows = [rows]
    affected = []
    for group in _rows_by_key_signature(rows):
        result = _request("POST", table, params={"on_conflict": on_conflict},
                          body=group, prefer=prefer)
        if isinstance(result, list):
            affected.extend(result)
        elif result is not None:
            affected.append(result)
    return affected


def update(table, match, changes, *, returning=True):
    """Patch rows where every column in `match` equals its value (eq filters).

    PostgREST refuses an unfiltered PATCH, so `match` must be non-empty — that's
    the guardrail against a runaway update.
    """
    if not match:
        raise SupabaseError("update() requires a match filter (refusing to patch every row).")
    params = {col: f"eq.{val}" for col, val in match.items()}
    prefer = "return=representation" if returning else "return=minimal"
    return _request("PATCH", table, params=params, body=changes, prefer=prefer)


def select(table, *, params=None):
    """GET rows. `params` are raw PostgREST query params (select, order, limit, eq...)."""
    return _request("GET", table, params=params) or []


def rpc(fn, args=None):
    """Call a Postgres function exposed in the marketing schema (POST /rpc/<fn>)."""
    return _request("POST", f"rpc/{fn}", body=args or {})
