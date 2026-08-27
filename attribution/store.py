"""Supabase data access for the attribution module (v2, 2026-08-08).

v1 kept short_links + clicks in a local SQLite file, which meant the
redirect only worked on this machine — no published link was ever really
tracked. v2 moves both tables to the shared Supabase `marketing` schema
(migration 20260808000001) so the PUBLIC redirect lives in the Fiboprana
app on Vercel (fiboprana.com/r/<code>) while THIS dashboard stays local
and unauthenticated-but-unreachable, exactly as the README prescribed:
everything except /r/* locked down — by construction, not middleware.

All calls go through fleet/supabase.py (anon posture, same as the fleet).
Ledger discipline: links are never deleted (a published URL can't be
recalled), only `archived` flips; clicks are append-only.
"""

from fleet import supabase

LINKS = "short_links"
CLICKS = "link_clicks"


def get_link_by_code(code):
    rows = supabase.select(LINKS, params={"code": f"eq.{code}", "limit": 1})
    return rows[0] if rows else None


def get_link(link_id):
    rows = supabase.select(LINKS, params={"id": f"eq.{link_id}", "limit": 1})
    return rows[0] if rows else None


def code_exists(code):
    return bool(supabase.select(LINKS, params={"select": "id",
                                               "code": f"eq.{code}",
                                               "limit": 1}))


def find_link(match):
    """First non-archived link whose columns equal `match` exactly (None
    matches SQL NULL). The autolink idempotency lookup: one row per
    (destination x UTM identity), so click history accumulates instead of
    splintering across duplicate codes."""
    params = {"archived": "eq.false", "order": "id.asc", "limit": 1}
    for col, val in match.items():
        params[col] = "is.null" if val is None else f"eq.{val}"
    rows = supabase.select(LINKS, params=params)
    return rows[0] if rows else None


def insert_link(values):
    """Insert one short_links row, return it (id and created_at DB-filled).
    Raises SupabaseError on a unique-code race — caller retries with a
    fresh code, same contract as the SQLite IntegrityError loop had."""
    return supabase.insert(LINKS, values)


def set_archived(link_id, archived):
    """Returns the patched rows (empty list -> no such link)."""
    return supabase.update(LINKS, {"id": link_id}, {"archived": bool(archived)})


def record_click(row):
    supabase.insert(CLICKS, row, returning=False)


def fetch_links(include_archived=False, limit=200):
    params = {"order": "created_at.desc,id.desc", "limit": limit}
    if not include_archived:
        params["archived"] = "eq.false"
    return supabase.select(LINKS, params=params)


def fetch_clicks_for_link(link_id, limit=500):
    return supabase.select(CLICKS, params={
        "short_link_id": f"eq.{link_id}",
        "order": "clicked_at.desc", "limit": limit})


def fetch_all_clicks(limit=10000):
    """The whole click log, newest first — the analytics layer aggregates in
    Python. Fine at current volumes; when this cap ever bites, move the
    aggregation into a Postgres view and read that instead (log the day it
    happens: 10k clicks is a champagne problem)."""
    return supabase.select(CLICKS, params={
        "select": "short_link_id,clicked_at",
        "order": "clicked_at.desc", "limit": limit})
