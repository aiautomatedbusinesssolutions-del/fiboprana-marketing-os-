-- ============================================================================
-- 20260625000001_scope_hermes_role.sql
-- Fiboprana marketing — move the agent onto a dedicated, scoped `hermes` role
-- ----------------------------------------------------------------------------
-- WHY: the init migration authenticates the agent with the public Supabase ANON
-- key — the SAME key the SaaS app ships to browsers. With `using(true)` RLS that
-- means anyone who extracts the anon key can READ and OVERWRITE every marketing
-- row, the agent's training ledger included. Withholding DELETE does not help: a
-- full-row UPDATE blanks digest_md/reasoning just as effectively.
--
-- THIS MIGRATION gives the agent its OWN `hermes` Postgres role and removes
-- anon/authenticated access to the marketing tables entirely. After it is applied
-- a leaked anon key can no longer touch this schema. It also makes the ledgers'
-- core fields write-once (the agent may INSERT and fill outcome_* columns, but
-- cannot rewrite a past digest/reasoning) — the true immutability the init
-- migration only claimed.
--
-- ⚠️ APPLY ORDER MATTERS — the agent breaks if you apply this before it holds a
--    hermes credential. Do these IN ORDER:
--   1. Mint the credential FIRST:   python -m hermes.mint_hermes_token
--      (or, on projects using the newer asymmetric signing keys, create a Supabase
--       *secret* API key bound to role `hermes` — see hermes/SECURITY.md).
--   2. Set SUPABASE_HERMES_JWT in your local .env AND the Render dashboard.
--   3. Sanity-check the bearer wiring (still on anon grants here, should pass):
--        python -m hermes.check --write
--   4. THEN run this file in Supabase Studio -> SQL Editor.
--   5. Re-run `python -m hermes.check --write` to confirm hermes-role access works
--      and that a plain anon key is now locked out.
-- Full runbook + rollback: hermes/SECURITY.md.
-- ============================================================================

-- ── The dedicated role PostgREST switches into via the JWT `role` claim ───────
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'hermes') then
    create role hermes nologin;          -- target of SET ROLE; never logs in directly
  end if;
end $$;

grant hermes to authenticator;           -- lets PostgREST SET ROLE hermes
grant usage on schema marketing to hermes;

-- ── Grants: ledgers are append-first; working tables are full CRUD ────────────
-- research_runs: INSERT + SELECT, plus UPDATE limited to the outcome_* columns,
-- so digest_md / reasoning / content_pull / inputs become write-once.
grant select, insert on marketing.research_runs to hermes;
grant update (outcome_verdict, outcome_note, outcome_metrics, outcome_checked_at)
  on marketing.research_runs to hermes;

-- agent_execution_logs: append-only ops trail (no update/delete needed).
grant select, insert on marketing.agent_execution_logs to hermes;

-- working tables: full CRUD (content + goals are mutable by design).
grant select, insert, update, delete on marketing.content_calendar to hermes;
grant select, insert, update, delete on marketing.marketing_goals  to hermes;

-- ── Remove the public roles from the marketing tables entirely ────────────────
-- service_role is intentionally left untouched: it is your admin / break-glass
-- key (bypasses RLS) and must NOT be in the agent's runtime environment.
revoke all on marketing.research_runs        from anon, authenticated;
revoke all on marketing.agent_execution_logs from anon, authenticated;
revoke all on marketing.content_calendar     from anon, authenticated;
revoke all on marketing.marketing_goals      from anon, authenticated;

-- ── Replace the permissive policies with hermes-only ones ─────────────────────
drop policy research_runs_select on marketing.research_runs;
drop policy research_runs_insert on marketing.research_runs;
drop policy research_runs_update on marketing.research_runs;
drop policy agent_logs_select    on marketing.agent_execution_logs;
drop policy agent_logs_insert    on marketing.agent_execution_logs;
drop policy agent_logs_update    on marketing.agent_execution_logs;
drop policy content_calendar_all on marketing.content_calendar;
drop policy marketing_goals_all  on marketing.marketing_goals;

create policy research_runs_hermes_select on marketing.research_runs        for select to hermes using (true);
create policy research_runs_hermes_insert on marketing.research_runs        for insert to hermes with check (true);
create policy research_runs_hermes_update on marketing.research_runs        for update to hermes using (true) with check (true);
create policy agent_logs_hermes_select    on marketing.agent_execution_logs for select to hermes using (true);
create policy agent_logs_hermes_insert    on marketing.agent_execution_logs for insert to hermes with check (true);
create policy content_calendar_hermes_all on marketing.content_calendar     for all    to hermes using (true) with check (true);
create policy marketing_goals_hermes_all  on marketing.marketing_goals      for all    to hermes using (true) with check (true);

-- ============================================================================
-- End of migration. The agent now reaches Supabase ONLY as `hermes`; a leaked
-- public anon key has no access to the marketing schema, and past digests /
-- reasoning are write-once.
-- ============================================================================
