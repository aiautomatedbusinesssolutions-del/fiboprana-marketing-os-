-- 20260826000001_regrant_anon_until_hermes_wired.sql
-- Fiboprana clone (2026-08-26): the fresh project applied 20260625000001 (the
-- hermes scoped-role hardening) from day one, which revokes anon on the four
-- init tables. The engine's live project never ran that revoke — its fleet
-- authenticates as anon — and this clone's fleet does the same for now
-- (SUPABASE_HERMES_JWT unset; self-signed HS256 minting may not work on new
-- projects with asymmetric JWT keys anyway).
--
-- So: restore the init migration's anon/authenticated grants on those four
-- tables. The hermes role and its grants stay in place; when a real hermes
-- (or renamed marketing_fleet) credential is wired — e.g. before any cloud
-- deploy — re-apply the revokes from 20260625000001 §"revoke" verbatim.

grant select, insert, update          on marketing.research_runs         to anon, authenticated;
grant select, insert, update          on marketing.agent_execution_logs  to anon, authenticated;
grant select, insert, update, delete  on marketing.content_calendar      to anon, authenticated;
grant select, insert, update, delete  on marketing.marketing_goals       to anon, authenticated;

-- The hermes migration also REPLACED the init RLS policies with hermes-only
-- ones (its §"drop policy" block) — grants alone leave anon RLS-blocked
-- (selects return empty, inserts 42501). Recreate the init-era anon policies;
-- they coexist with the hermes ones (permissive policies OR together).
create policy research_runs_select on marketing.research_runs for select to anon, authenticated using (true);
create policy research_runs_insert on marketing.research_runs for insert to anon, authenticated with check (true);
create policy research_runs_update on marketing.research_runs for update to anon, authenticated using (true) with check (true);
create policy agent_logs_select on marketing.agent_execution_logs for select to anon, authenticated using (true);
create policy agent_logs_insert on marketing.agent_execution_logs for insert to anon, authenticated with check (true);
create policy agent_logs_update on marketing.agent_execution_logs for update to anon, authenticated using (true) with check (true);
create policy marketing_goals_all on marketing.marketing_goals for all to anon, authenticated using (true) with check (true);
-- content_calendar's select/insert/update policies were already recreated for
-- anon by 20260703000002; nothing to restore there.
