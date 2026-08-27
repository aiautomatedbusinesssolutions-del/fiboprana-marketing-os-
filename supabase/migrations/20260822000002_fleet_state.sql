-- ============================================================================
-- 20260822000002_fleet_state.sql
-- Fiboprana marketing - the click-state mirror (off-PC drain v1).
-- ----------------------------------------------------------------------------
-- The /week click-state (flow_state.json, video_ideas.json) lives on the
-- founder's PC, which is why the drain sweeps could only run there. This
-- table is the one-way MIRROR that lets the Railway dispatcher act on it:
--
--   * "flow_state" / "video_ideas": pushed UP by every local write (the
--     local files stay the source of truth; the cloud never writes these).
--   * "cloud_results": the cloud drain job's outbox - deltas only (a video
--     it logged, an xpost it scheduled). The local drain runner applies each
--     delta to the local files and clears it. Split rows = no clobber.
--
-- Design decided 2026-08-22 (founder-approved build): full state migration
-- to Supabase was rejected for now (too much churn before the first real
-- week); the mirror gets the founder "PC can be off overnight" at daily
-- cron granularity, and the local runner still covers the 10-minute case.
--
-- HOW TO APPLY:  Supabase Studio -> SQL Editor -> paste this file -> Run.
--   `marketing` is already exposed; no API settings change needed.
-- ============================================================================

create table marketing.fleet_state (
  name        text primary key,
  data        jsonb not null,
  updated_at  timestamptz not null default now()
);

comment on table marketing.fleet_state is
  'Click-state mirror: flow_state / video_ideas pushed up from the founder''s PC; cloud_results = the cloud drain''s delta outbox.';

grant select, insert, update on marketing.fleet_state to anon, authenticated, service_role;

alter table marketing.fleet_state enable row level security;

create policy fleet_state_select on marketing.fleet_state
  for select to anon, authenticated using (true);
create policy fleet_state_insert on marketing.fleet_state
  for insert to anon, authenticated with check (true);
create policy fleet_state_update on marketing.fleet_state
  for update to anon, authenticated using (true) with check (true);
