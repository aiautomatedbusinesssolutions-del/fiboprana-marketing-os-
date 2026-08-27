-- 20260802000001_funnel_counts.sql
-- Fiboprana marketing — aggregate app-funnel counts for the daily metrics agent.
-- APPLIED to the live project 2026-08-02 (SQL editor). Idempotent; safe to re-run.
--
-- The app's tables (public.users, waitlist, quiz_leads, founding_members) are
-- RLS-locked, as they should be. The metrics agent only needs four COUNTs, so
-- this SECURITY DEFINER function is the one narrow bridge across that boundary:
-- it returns aggregates only — no rows, no columns, no PII.
--
-- Grant note, stated plainly: execute is granted to `anon` because that is the
-- credential the whole fleet actually authenticates with — the scoped-role
-- hardening (20260625000001) was never applied live; its role doesn't exist
-- (42704, confirmed 2026-08-02). Exposure: anyone holding the app's public key
-- can read four aggregate numbers, nothing more. When the scoped agent role
-- lands (renamed — "hermes" collides with the official Nous Research Hermes
-- agents; use e.g. marketing_fleet), grant it execute and revoke anon.

create or replace function marketing.funnel_counts()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_build_object(
    'users',            (select count(*) from public.users),
    'waitlist',         (select count(*) from public.waitlist),
    'quiz_leads',       (select count(*) from public.quiz_leads),
    'founding_members', (select count(*) from public.founding_members)
  );
$$;

comment on function marketing.funnel_counts() is
  'Aggregate signup-funnel counts for the daily metrics agent. Counts only — never row data.';

revoke all on function marketing.funnel_counts() from public, authenticated;
grant execute on function marketing.funnel_counts() to anon, service_role;
