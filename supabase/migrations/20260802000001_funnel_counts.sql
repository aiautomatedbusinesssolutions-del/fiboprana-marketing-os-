-- 20260802000001_funnel_counts.sql
-- Fiboprana marketing — aggregate app-funnel counts for the daily metrics agent.
--
-- Fiboprana clone note (2026-08-26): the engine's original version counted its
-- product's tables (public.users, waitlist, quiz_leads, founding_members). The
-- Fiboprana product database doesn't exist yet (fiboprana-site is pre-launch;
-- email capture lives in Resend, not Postgres), so this is a STUB with the same
-- signature: the metrics agent can call it today and gets zeros. When product
-- tables land in public (with RLS), replace the zeros with real COUNT()s —
-- SECURITY DEFINER + aggregates-only is the one narrow bridge across RLS:
-- counts only, no rows, no columns, no PII.

create or replace function marketing.funnel_counts()
returns jsonb
language sql
stable
security definer
set search_path = ''
as $$
  select jsonb_build_object(
    'users',            0,
    'waitlist',         0,
    'quiz_leads',       0,
    'founding_members', 0
  );
$$;

comment on function marketing.funnel_counts() is
  'Aggregate signup-funnel counts for the daily metrics agent. STUB returning zeros until the Fiboprana product tables exist. Counts only — never row data.';

revoke all on function marketing.funnel_counts() from public, authenticated;
grant execute on function marketing.funnel_counts() to anon, service_role;
