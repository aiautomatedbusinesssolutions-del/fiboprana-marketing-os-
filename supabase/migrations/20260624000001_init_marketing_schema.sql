-- ============================================================================
-- 20260624000001_init_marketing_schema.sql
-- Fiboprana marketing — Hermes multi-agent foundation
-- ----------------------------------------------------------------------------
-- Applies to the SHARED Supabase instance (same database as the SaaS app).
-- All marketing objects live in their OWN `marketing` schema so they can never
-- collide with — or reach — the app's public / auth / billing tables.
--
-- Design principles (deliberate):
--   * The migrations/ folder is the single source of truth for schema HISTORY.
--     Schemas evolve only by ADDING new migrations, never editing live tables.
--   * Strict where invariant (PK, NOT NULL, non-negative, timestamps);
--     flexible where the domain is still emerging (text vocab instead of
--     ENUMs/CHECKs, jsonb for evolving denormalized payloads) so new fields
--     don't need a migration every week.
--   * The two ledger tables grant no DELETE to anon. NOTE: this does NOT make the
--     training history tamper-proof — a full-row UPDATE can still blank
--     digest_md/reasoning/content_pull. True write-once immutability arrives with
--     the scoped role in 20260625000001_scope_hermes_role.sql.
--
-- HOW TO APPLY (Route A — you own the secrets):
--   1. Supabase Studio -> SQL Editor -> paste this file -> Run.
--   2. Supabase Studio -> Settings -> API -> "Exposed schemas" -> add `marketing`.
--      (PostgREST/supabase-js only sees `public` by default; without this step
--       the anon key cannot reach these tables.)
--
-- STRONGER AUTH (recommended upgrade, when ready): instead of the public anon
-- key, connect the MCP tool via a DEDICATED Postgres role + direct connection
-- string scoped to ONLY this schema. Then drop the permissive anon policies
-- below — the agent could not touch app/user/billing tables even in principle.
-- ============================================================================

-- ── Extensions ──────────────────────────────────────────────────────────────
create extension if not exists pgcrypto;  -- provides gen_random_uuid()

-- ── Schema ──────────────────────────────────────────────────────────────────
create schema if not exists marketing;
grant usage on schema marketing to anon, authenticated, service_role;

-- ── Shared helper: auto-maintain updated_at ─────────────────────────────────
create or replace function marketing.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- ════════════════════════════════════════════════════════════════════════════
-- research_runs — the historical LEARNING LEDGER (load-bearing; agent #1).
-- One self-contained row per research run. Denormalized on purpose: each row
-- must stand alone as a training example -> decision + reasoning + outcome.
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.research_runs (
  id                 uuid primary key default gen_random_uuid(),
  agent_name         text not null default 'research',
  model              text,                                  -- provider/model used (multi-provider-ready)
  week_start         date,                                  -- the week this run covers
  status             text not null default 'complete',      -- complete | partial | error (enforced in code)

  -- INPUTS: what the agent read (source counts, prior verdicts considered, params).
  inputs             jsonb not null default '{}'::jsonb,

  -- OUTPUTS
  digest_md          text,                                  -- brand-voiced digest (human-readable)
  content_pull       jsonb not null default '{}'::jsonb,    -- the weekly pull: headline, video_angle, competitor_note, reddit_themes[], feature_seed
  reasoning          text,                                  -- WHY these made the cut / the in-lane call (training gold)

  -- OUTCOME — updateable later, two layers:
  outcome_verdict    text,                                  -- L1 manual: strong | ok | off
  outcome_note       text,                                  -- L1 manual: one-line founder note
  outcome_metrics    jsonb,                                 -- L2 auto: downstream content performance, attributed back via item_id
  outcome_checked_at timestamptz,                           -- stamp when the outcome was last updated

  run_started_at     timestamptz not null default now(),
  run_finished_at    timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create trigger trg_research_runs_updated_at
  before update on marketing.research_runs
  for each row execute function marketing.set_updated_at();

create index idx_research_runs_week_start  on marketing.research_runs (week_start desc);
create index idx_research_runs_created_at  on marketing.research_runs (created_at desc);
create index idx_research_runs_agent       on marketing.research_runs (agent_name);
create index idx_research_runs_pull_gin    on marketing.research_runs using gin (content_pull);  -- jsonb queries into the pull

-- ════════════════════════════════════════════════════════════════════════════
-- agent_execution_logs — live OPS log (load-bearing). NOT the training ledger:
-- this is "what ran, when, cost, errors" for EVERY agent. Kept distinct from
-- research_runs so the training data stays cleanly queryable.
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.agent_execution_logs (
  id             uuid primary key default gen_random_uuid(),
  agent_name     text not null,
  correlation_id uuid,                                      -- soft link to a run (e.g. research_runs.id); not an FK — all agents log here
  event          text not null,                             -- start | finish | tool_call | error (enforced in code)
  status         text,                                      -- success | failure | running
  model          text,
  tokens_input   integer       check (tokens_input  >= 0),
  tokens_output  integer       check (tokens_output >= 0),
  cost_usd       numeric(10,4) check (cost_usd       >= 0),
  duration_ms    integer       check (duration_ms   >= 0),
  message        text,
  detail         jsonb not null default '{}'::jsonb,
  created_at     timestamptz not null default now()
);

create index idx_agent_logs_agent      on marketing.agent_execution_logs (agent_name);
create index idx_agent_logs_created_at on marketing.agent_execution_logs (created_at desc);
create index idx_agent_logs_corr       on marketing.agent_execution_logs (correlation_id);

-- ════════════════════════════════════════════════════════════════════════════
-- content_calendar — DEFERRED STUB (the content agent + dashboard own this later).
-- Created now only to lock the scaffold AND to wire PROVENANCE from day one:
-- content traces back to the exact research item that spawned it.
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.content_calendar (
  id                     uuid primary key default gen_random_uuid(),
  title                  text,
  platform               text,
  status                 text not null default 'idea',
  scheduled_for          timestamptz,
  -- provenance: closes the research -> content -> outcome loop
  source_research_run_id uuid references marketing.research_runs(id) on delete set null,
  source_item_id         text,                               -- which seed: headline | feature_seed | a theme
  payload                jsonb not null default '{}'::jsonb,
  notes                  text,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create trigger trg_content_calendar_updated_at
  before update on marketing.content_calendar
  for each row execute function marketing.set_updated_at();

create index idx_content_calendar_source on marketing.content_calendar (source_research_run_id);

-- ════════════════════════════════════════════════════════════════════════════
-- marketing_goals — DEFERRED STUB (high-level CTR / traffic / list milestones).
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.marketing_goals (
  id            uuid primary key default gen_random_uuid(),
  metric        text not null,                              -- e.g. ctr | traffic | subscribers
  target_value  numeric,
  current_value numeric,
  period        text,                                       -- e.g. 2026-Q3 | 2026-07
  notes         text,
  payload       jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create trigger trg_marketing_goals_updated_at
  before update on marketing.marketing_goals
  for each row execute function marketing.set_updated_at();

-- ── Table privileges ─────────────────────────────────────────────────────────
-- PostgREST needs BOTH grants and RLS policies. Ledgers get no DELETE for anon
-- (protect history); working tables get full CRUD.
grant select, insert, update          on marketing.research_runs         to anon, authenticated, service_role;
grant select, insert, update          on marketing.agent_execution_logs  to anon, authenticated, service_role;
grant select, insert, update, delete  on marketing.content_calendar      to anon, authenticated, service_role;
grant select, insert, update, delete  on marketing.marketing_goals       to anon, authenticated, service_role;

-- ── Row Level Security ───────────────────────────────────────────────────────
-- Auth is via the Supabase ANON key (per the Hermes config contract). WARNING:
-- the anon key is the SAME public key the SaaS app ships to browsers — it is NOT
-- contained. With using(true)/with check(true), anyone holding it can READ and
-- OVERWRITE every marketing row. This is a known, accepted INTERIM posture; it is
-- closed by the scoped `hermes` role in 20260625000001_scope_hermes_role.sql
-- (apply that once you've minted its credential — see hermes/SECURITY.md).
alter table marketing.research_runs        enable row level security;
alter table marketing.agent_execution_logs enable row level security;
alter table marketing.content_calendar     enable row level security;
alter table marketing.marketing_goals      enable row level security;

-- research_runs — append/update only (no delete policy)
create policy research_runs_select on marketing.research_runs for select to anon, authenticated using (true);
create policy research_runs_insert on marketing.research_runs for insert to anon, authenticated with check (true);
create policy research_runs_update on marketing.research_runs for update to anon, authenticated using (true) with check (true);

-- agent_execution_logs — append/update only (no delete policy)
create policy agent_logs_select on marketing.agent_execution_logs for select to anon, authenticated using (true);
create policy agent_logs_insert on marketing.agent_execution_logs for insert to anon, authenticated with check (true);
create policy agent_logs_update on marketing.agent_execution_logs for update to anon, authenticated using (true) with check (true);

-- content_calendar / marketing_goals — full CRUD (working tables)
create policy content_calendar_all on marketing.content_calendar for all to anon, authenticated using (true) with check (true);
create policy marketing_goals_all  on marketing.marketing_goals  for all to anon, authenticated using (true) with check (true);

-- ============================================================================
-- End of migration.
-- ============================================================================
