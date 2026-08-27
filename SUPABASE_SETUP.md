# Supabase setup — the one shared Fiboprana project

Decision (2026-08-26): **one Supabase project for the whole business.** Marketing lives
in its own `marketing` Postgres schema with a scoped agent role; the product app
(fiboprana-site, per its PRD) later adds its tables in `public` with RLS and its own
credentials. Agents never hold product-data credentials — schema isolation + the
`hermes` role is the boundary.

## 1. Create the project (once, ~5 min, in the browser)

1. supabase.com → New project. Suggested name: `fiboprana`. Pick the region closest to
   you; save the database password in your password manager.
2. Settings → API: copy the **Project URL** and the **anon public key** into `.env`
   (`SUPABASE_URL`, `SUPABASE_ANON_KEY`).

## 2. Apply the migrations (in order)

The 10 files in `supabase/migrations/` create the `marketing` schema end-to-end
(research_runs, agent_execution_logs, content_calendar, marketing_goals, the reply
system, videos/decisions/experiments/outcome_checks, metric_snapshots, funnel counts,
short_links/link_clicks, fleet_state) plus the scoped `hermes` role.

Easiest path, no CLI: SQL Editor → paste each file's contents → Run, **in filename
order** (they're timestamped). With the Supabase CLI instead: `supabase link
--project-ref <ref>` then `supabase db push`.

Note on `20260625000001_scope_hermes_role.sql`: it mints the dedicated agent role.
Apply it with the rest; wiring agents to actually USE it is step 4 and can wait.

## 3. Smoke-test the ledger

With `.env` filled, from the repo root:

```powershell
.\.venv\Scripts\Activate.ps1
python -c "from fleet import store; print(store.recent_runs(limit=1))"
```

An empty list (no error) means the fleet can reach the schema.

## 4. Optional hardening (recommended before any cloud deploy)

Follow `fleet/SECURITY.md`: set `SUPABASE_JWT_SECRET` locally, run
`python -m fleet.mint_hermes_token`, put the token in `SUPABASE_HERMES_JWT`. Agents
then act as the `hermes` role (marketing schema only) instead of anon. Do this before
the dispatcher ever runs off this machine.

## 5. When the product side arrives

fiboprana-site's tables go in `public` (or their own schema) with RLS from day one —
user check-in data is sensitive by definition. The marketing fleet reads product-side
funnel numbers only through `PRODUCT_SUPABASE_URL` / `PRODUCT_SUPABASE_SERVICE_KEY`
(see `.env.example`), which stay unset until that exists; `fleet/metrics_agent.py`
skips gracefully meanwhile.
