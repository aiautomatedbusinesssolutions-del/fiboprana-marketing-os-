-- ============================================================================
-- 20260703000002_content_calendar_posts.sql
-- Fiboprana marketing — content_calendar becomes the POSTS LEDGER
-- (MARKETING_OS.md §4). Run AFTER 20260703000001_video_system.sql.
-- ----------------------------------------------------------------------------
-- The init migration created content_calendar as a throwaway stub with FULL CRUD
-- under the public anon key — including DELETE. Now that it becomes the lineage
-- middle node (X posts AND shorts hang off videos through it), that posture is a
-- confirmed hole: the training history would be silently deletable. This
-- migration does two things in one shot:
--
--   1. EXTEND: the columns that make a calendar row a self-contained training
--      example, mirroring reply_ledger — draft->final diff capture (draft_text
--      is the write-once anchor), compliance-gate result, publish facts, and the
--      updateable outcome layer. Shorts live here as post_type='short' rows with
--      clip details in payload jsonb until 5-10 real shorts EARN a dedicated
--      table (exactly how reply_ledger earned its columns).
--   2. RE-SCOPE GRANTS: revoke DELETE + blanket UPDATE, replace the for-all
--      policy with select/insert/update, and column-scope UPDATE so draft_text
--      and provenance are write-once even under anon.
--
-- Edit-capture rule (accepted blind spot, not pretended away): the founder edits
-- X drafts IN-SESSION before pasting to Typefully, so final_text is real; edits
-- made inside Typefully afterward are not captured.
--
-- Also lands video_lineage here (it needs content_calendar.video_id).
--
-- HOW TO APPLY:  Supabase Studio -> SQL Editor -> paste this file -> Run.
-- ============================================================================

-- ── 1. Extend: posts-ledger columns ─────────────────────────────────────────
alter table marketing.content_calendar
  add column video_id             uuid references marketing.videos(id) on delete set null,
  add column post_type            text,                       -- x_post | x_thread | short | community_post | native_reddit
  add column pillar               text,                        -- content pillar this post serves
  add column hook_text            text,                        -- the first line / opening hook (its own lever)

  -- draft -> final diff capture (the voice-learning signal, like reply_ledger)
  add column draft_text           text,                        -- verbatim AI copy = the diff anchor (write-once)
  add column final_text           text,                        -- what actually shipped, post-founder-edit
  add column edit_delta           text,                        -- difflib unified diff (draft -> final)
  add column edit_distance        integer,
  add column edit_ratio           numeric(5,4),                -- 1.0 = shipped unedited
  add column edit_type            text,                        -- none | voice | substance | both | rejected

  -- compliance gate result (same shape as reply_drafts)
  add column gate_status          text,                        -- pass | flag | block
  add column gate_violations      jsonb,                       -- [{rule, severity, match}]
  add column gate_ruleset_version text,

  -- publish facts
  add column typefully_draft_id   text,                        -- editable-in-queue handle
  add column external_url         text,                        -- live permalink once up
  add column published_at         timestamptz,                 -- arms the outcome clock (dispatcher opens checks)

  -- outcome (updateable; two layers like everywhere else)
  add column outcome_verdict      text,                        -- L1 founder: strong | ok | off
  add column outcome_note         text,
  add column outcome_metrics      jsonb,
  add column outcome_checked_at   timestamptz;

create index idx_content_calendar_video on marketing.content_calendar (video_id);
create index idx_content_calendar_type  on marketing.content_calendar (post_type, status);
create index idx_content_calendar_pub   on marketing.content_calendar (published_at desc);

-- ── 2. Re-scope grants: close the stub's full-CRUD hole ─────────────────────
revoke delete on marketing.content_calendar from anon, authenticated;
revoke update on marketing.content_calendar from anon, authenticated;
drop policy if exists content_calendar_all on marketing.content_calendar;

-- Column-scoped UPDATE: lifecycle + edit capture + publish facts + outcome.
-- Write-once under anon: draft_text (the diff anchor) and the provenance pair
-- (source_research_run_id, source_item_id). final_text/edit_* stay updatable —
-- they're written at schedule time, after the insert — a looser moat than
-- reply_ledger's insert-time capture; accepted for the posts flow.
grant update (title, platform, status, scheduled_for, payload, notes,
              video_id, post_type, pillar, hook_text,
              final_text, edit_delta, edit_distance, edit_ratio, edit_type,
              gate_status, gate_violations, gate_ruleset_version,
              typefully_draft_id, external_url, published_at,
              outcome_verdict, outcome_note, outcome_metrics, outcome_checked_at)
  on marketing.content_calendar to anon, authenticated;
grant update on marketing.content_calendar to service_role;

create policy content_calendar_select on marketing.content_calendar for select to anon, authenticated using (true);
create policy content_calendar_insert on marketing.content_calendar for insert to anon, authenticated with check (true);
create policy content_calendar_update on marketing.content_calendar for update to anon, authenticated using (true) with check (true);

-- ── 3. video_lineage — the one-hop attribution map ──────────────────────────
-- One row per leaf (post/short), videos with no posts still visible (left join),
-- orphan posts (no video) visible too (full join via union would overcomplicate;
-- a post without video_id simply shows null video columns when queried from the
-- calendar side — this view reads from the VIDEO side, the spine).
create view marketing.video_lineage with (security_invoker = on) as
select r.id            as research_run_id,
       r.week_start,
       v.id            as video_id,
       v.slug,
       v.pillar        as video_pillar,
       v.status        as video_status,
       v.published_at  as video_published_at,
       v.outcome_verdict as video_verdict,
       c.id            as post_id,
       c.post_type,
       c.platform      as post_platform,
       c.status        as post_status,
       c.published_at  as post_published_at,
       c.outcome_verdict as post_verdict
from marketing.videos v
left join marketing.research_runs r on r.id = v.source_research_run_id
left join marketing.content_calendar c on c.video_id = v.id;

grant select on marketing.video_lineage to anon, authenticated, service_role;

-- ============================================================================
-- End of migration.
-- ============================================================================
