-- ============================================================================
-- 20260725000001_metric_snapshots.sql
-- Fiboprana marketing — metric_snapshots: the raw-numbers time series.
-- ----------------------------------------------------------------------------
-- One table, any source. The audit finding behind it (2026-07-25): the outcome
-- checker already fetches the WHOLE account's Typefully analytics every run,
-- keeps the few rows that match replies awaiting outcomes, and discards the
-- rest — and nothing anywhere records account-level history. Snapshots are the
-- one dataset you can't backfill: if it wasn't written down on the day, it's
-- gone. So the metrics agent (fleet/metrics_agent.py) now persists EVERYTHING
-- it reads, verbatim, one row per entity per UTC day.
--
--   entity_kind / entity_key      what was measured (platform-native id):
--     x_post        / tweet status id
--     x_account     / handle                (rollup of the posts window; no
--                                            follower count — Typefully doesn't
--                                            expose one; see the agent docstring)
--     reddit_account/ username              (karma, when creds are live)
--     reddit_comment/ t1_ fullname          (score history per sent reply)
--     yt_video, yt_channel                  (reserved for the YouTube agent)
--   source                        which API the numbers came from (trust label,
--                                 same vocabulary idea as outcome_checks.source)
--   metrics                       the raw API payload, verbatim jsonb — no
--                                 LLM ever writes here (deterministic-toolbox
--                                 rule), and no lossy flattening: fields we
--                                 don't care about yet are exactly the point.
--
-- POSTURE: append-only across days. The unique (source, entity_kind,
-- entity_key, snapshot_date) index makes the daily run idempotent — a re-run
-- the same day merges (latest read wins); yesterday is never touched by the
-- agent. UPDATE is granted only on the merge payload columns (PostgREST's
-- merge-duplicates needs it); no DELETE anywhere, like every ledger table.
--
-- HOW TO APPLY:  Supabase Studio -> SQL Editor -> paste this file -> Run.
--   `marketing` is already exposed; no API settings change needed.
-- ============================================================================

create table marketing.metric_snapshots (
  id            uuid primary key default gen_random_uuid(),
  snapshot_date date not null default (now() at time zone 'utc')::date,
  captured_at   timestamptz not null default now(),   -- refreshed on same-day merge
  source        text not null,                        -- typefully | reddit_api | yt_api | manual
  entity_kind   text not null,                        -- x_post | x_account | reddit_comment | ...
  entity_key    text not null,                        -- platform-native id
  metrics       jsonb not null,                       -- the raw API payload, verbatim
  created_at    timestamptz not null default now()
);

-- Daily idempotence: the upsert arbitration key (plain unique index —
-- PostgREST can't arbitrate on a partial one).
create unique index uq_metric_snapshots_day
  on marketing.metric_snapshots (source, entity_kind, entity_key, snapshot_date);

-- The two read paths: one entity's history, and "everything from day X".
create index idx_metric_snapshots_entity
  on marketing.metric_snapshots (entity_kind, entity_key, snapshot_date desc);
create index idx_metric_snapshots_date
  on marketing.metric_snapshots (snapshot_date desc);

-- ── privileges + RLS — anon posture, merge-scoped UPDATE, no DELETE ──────────
grant select, insert on marketing.metric_snapshots to anon, authenticated, service_role;

-- merge-duplicates sends INSERT ... ON CONFLICT DO UPDATE SET <payload columns>,
-- so the payload columns need UPDATE; id / created_at / snapshot_date stay
-- DB-owned (snapshot_date is defaulted, never sent).
grant update (source, entity_kind, entity_key, captured_at, metrics)
  on marketing.metric_snapshots to anon, authenticated;
grant update on marketing.metric_snapshots to service_role;

alter table marketing.metric_snapshots enable row level security;

create policy metric_snapshots_select on marketing.metric_snapshots
  for select to anon, authenticated using (true);
create policy metric_snapshots_insert on marketing.metric_snapshots
  for insert to anon, authenticated with check (true);
create policy metric_snapshots_update on marketing.metric_snapshots
  for update to anon, authenticated using (true) with check (true);

-- ============================================================================
-- End of migration.
-- ============================================================================
