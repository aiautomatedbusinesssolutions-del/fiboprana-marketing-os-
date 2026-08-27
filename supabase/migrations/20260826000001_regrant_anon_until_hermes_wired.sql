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
