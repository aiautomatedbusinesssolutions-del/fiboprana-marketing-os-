"""The shared foundation for the marketing agent fleet.

Agents run in the cloud (isolated Docker first, a local box later) and meet at one
central ledger in Supabase's `marketing` schema, not the per-module SQLite the rest
of the repo uses. This package is that meeting point:

    supabase.py  — minimal stdlib PostgREST client (the transport)
    store.py     — the domain API every agent + chat calls (the vocabulary)
    check.py     — connectivity smoke test

See README.md for setup (migration + exposed schema + env keys).
"""
