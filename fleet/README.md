# fleet/ — shared agent foundation (Supabase ledger)

The data layer every fleet agent (and you, in chat) reads and writes. Agents run
in the cloud and share **one central ledger**, so this writes to Supabase's
`marketing` schema rather than the per-module SQLite the rest of the repo uses.

## Files
- `supabase.py` — minimal stdlib PostgREST client (urllib + json; no new deps).
  Sends the `marketing` schema-profile header on every request.
- `store.py` — the domain API: `log_research_run`, `update_outcome`,
  `recent_runs_for_learning`, `log_execution`. Import this; don't hand-roll SQL.
- `check.py` — connectivity smoke test (`python -m fleet.check`).

## Setup (one time)
1. Apply `supabase/migrations/20260624000001_init_marketing_schema.sql` in
   **Supabase Studio → SQL Editor**.
2. **Supabase Studio → Settings → API → Exposed schemas → add `marketing`.**
   (Without this the anon key can't see the tables — the #1 gotcha.)
3. Make the keys readable by the code:
   - Local testing → add to `.env`:
     ```
     SUPABASE_URL=https://<project>.supabase.co
     SUPABASE_ANON_KEY=<anon public key>
     ```
   - Production → your MCP client injects them via its `config.yaml` `env:` block.

## Verify
```
python -m fleet.check          # read-only round-trip
python -m fleet.check --write  # also insert + update a throwaway row (delete it after)
```
If `check` passes, the MCP server (`fleet/mcp_server.py`) will too.

## Provenance contract
The run id is the provenance anchor. When content is later made from one of the
pull's seeds (headline, feature_seed, a reddit theme...), the content row stores
this run's id plus which seed it came from. That's how performance can be
attributed back to the research that sparked it — and how the research agent
eventually learns which *kinds* of picks lead to content that lands.

## Why stdlib, not supabase-py
Repo rule: stdlib over deps. PostgREST is HTTP + JSON, so a thin urllib wrapper
stays transparent and easy to edit. To switch to supabase-py later, only
`supabase.py` changes — `store.py` and every caller stay the same.
