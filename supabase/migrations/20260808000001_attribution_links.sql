-- ============================================================================
-- 20260808000001_attribution_links.sql
-- Fiboprana marketing — attribution: short links + click log, now shared.
-- ----------------------------------------------------------------------------
-- The attribution/ module's v1 kept short_links + clicks in a local SQLite
-- file, which meant the redirect could only run on the founder's machine —
-- so no published link was ever really tracked (metric_snapshots shows
-- link_clicks: 0 forever). v2 (2026-08-08) splits the roles:
--
--   * the LOCAL dashboard (attribution/ Flask blueprint) still creates links
--     and reads analytics — as anon, like the rest of the fleet;
--   * the PUBLIC redirect moves into the Fiboprana app on Vercel
--     (fiboprana.com/r/<code>, service_role) — the only surface exposed,
--     which is the README's "lock down everything except /r/*" done by
--     construction rather than by auth middleware.
--
-- Both sides meet in these two tables.
--
-- POSTURE: ledger discipline, same as reply_ledger / metric_snapshots.
-- No DELETE anywhere. Links are never removed — a published URL can't be
-- recalled — only archived (the single UPDATE-able column). Clicks are
-- append-only, write-once. ip_hash is sha256(ip + UTC-date salt), never a
-- raw IP (same privacy stance as the SQLite v1).
--
-- HOW TO APPLY:  Supabase Studio -> SQL Editor -> paste this file -> Run.
--   `marketing` is already exposed; no API settings change needed.
-- ============================================================================

create table marketing.short_links (
  id             bigint generated always as identity primary key,
  code           text not null unique,       -- 6-char base62, e.g. "aB3xK9"
  destination    text not null,              -- where the redirect goes
  utm_source     text,                       -- x | tiktok | linkedin | reddit-dm | ...
  utm_medium     text,                       -- post | dm | bio | ...
  utm_campaign   text,
  utm_content    text,
  utm_term       text,
  observation_id bigint,                     -- soft ref into capture/conversations.db (local)
  post_text      text,                       -- the post/DM the link went into
  notes          text,
  archived       boolean not null default false,  -- hides from dashboard; redirect works forever
  created_at     timestamptz not null default now()
);

create index idx_short_links_archived_created
  on marketing.short_links (archived, created_at desc, id desc);

create table marketing.link_clicks (
  id            bigint generated always as identity primary key,
  short_link_id bigint not null references marketing.short_links (id),
  clicked_at    timestamptz not null default now(),
  ip_hash       text,                        -- sha256(ip + daily salt) — no raw PII
  user_agent    text,
  referer       text
);

create index idx_link_clicks_link_time
  on marketing.link_clicks (short_link_id, clicked_at desc);
create index idx_link_clicks_time
  on marketing.link_clicks (clicked_at desc);

-- ── privileges + RLS — anon dashboard, service_role redirect, no DELETE ─────
grant select, insert on marketing.short_links to anon, authenticated, service_role;
-- archived is the ONLY column the dashboard may flip; everything else is
-- write-once at insert.
grant update (archived) on marketing.short_links to anon, authenticated;
grant update on marketing.short_links to service_role;

grant select, insert on marketing.link_clicks to anon, authenticated, service_role;
-- no UPDATE on clicks for anyone below service_role: append-only log.

alter table marketing.short_links enable row level security;
alter table marketing.link_clicks enable row level security;

create policy short_links_select on marketing.short_links
  for select to anon, authenticated using (true);
create policy short_links_insert on marketing.short_links
  for insert to anon, authenticated with check (true);
create policy short_links_update on marketing.short_links
  for update to anon, authenticated using (true) with check (true);

create policy link_clicks_select on marketing.link_clicks
  for select to anon, authenticated using (true);
create policy link_clicks_insert on marketing.link_clicks
  for insert to anon, authenticated with check (true);

-- ============================================================================
-- End of migration.
-- ============================================================================
