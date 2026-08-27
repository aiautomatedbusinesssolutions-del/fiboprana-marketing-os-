-- ============================================================================
-- 20260626000001_reply_system.sql
-- Fiboprana marketing — Reply Assistant (Phase 1: Reddit, lean floor)
-- ----------------------------------------------------------------------------
-- Adds the reply system to the existing `marketing` schema, next to research_runs.
--
-- Pipeline:
--   finder + thin judge  -> reply_candidates   (one row per post worth a look)
--   drafter (2-3 variants, regex-gated)
--                        -> reply_drafts        (one row per variant)
--   you review/edit in the CLI
--                        -> reply_ledger        (one row per SENT reply, WITH the
--                                                draft->final DIFF: the core
--                                                voice-learning signal)
--
-- POSTURE (deliberate, Phase 1): authenticate as the public ANON key, exactly
-- like research_runs does today. SUPABASE_HERMES_JWT is unset/unmintable on this
-- project (asymmetric signing keys), so the cron AND the review CLI both run as
-- anon; a hermes-only migration would lock them both out. We DO improve on the
-- init migration: UPDATE here is COLUMN-SCOPED, so the verbatim AI draft and your
-- sent reply are write-once even under anon (only outcome/label columns mutate).
-- A later migration can revoke anon + add a scoped `hermes` role for all three
-- tables at once (the same reversible move 20260625000001 makes) with NO data
-- migration — only when the asymmetric-key credential path is solved.
--
-- LEAN PHASE 1 (founder's call): the style guide is a git-tracked markdown file
-- (hermes/reply_style_guide.md), referenced here only by a text tag
-- (style_version). The versioned style-guide TABLE, the LLM compliance judge, and
-- the weekly distiller arrive in a Phase 2 migration. Adding tables/columns later
-- is this repo's normal evolution (see 20260624000001 header) — it does NOT mean
-- leaving Supabase, which is the migration the founder wants to avoid.
--
-- HOW TO APPLY:  Supabase Studio -> SQL Editor -> paste this file -> Run.
--   `marketing` is already an exposed schema (init migration), and the anon key
--   already reaches it (research_runs works), so NO API settings change is needed.
-- Assumes marketing.set_updated_at() + pgcrypto from 20260624000001.
-- ============================================================================

-- ════════════════════════════════════════════════════════════════════════════
-- reply_candidates — finder output + thin LLM judge. Channel-pluggable (platform).
-- Denormalized post snapshot so each candidate stands alone. intended_use lets
-- the deferred content-idea sibling share this pool without a second table.
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.reply_candidates (
  id                     uuid primary key default gen_random_uuid(),
  platform               text not null default 'reddit',      -- reddit | x | youtube (channel-pluggable)
  intended_use           text not null default 'reply',       -- reply (now) | content (deferred sibling)
  status                 text not null default 'new',         -- new | judged | drafted | sent | skipped | expired

  external_id            text,                                 -- derived from the thread URL /comments/<base36>/

  -- post snapshot (denormalized)
  post_url               text,
  community              text,                                 -- subreddit / handle / channel
  post_title             text,
  post_excerpt           text,                                 -- finder blurb (drafter fetches the FULL body at draft time)
  post_author            text,
  post_created_at        timestamptz,

  -- finder signal at selection time (trains the "find better" loop)
  finder_score           integer,
  flips                  text,
  soft_triggers          text,
  angle                  text,
  flip_group             text,                                 -- pre_decision | post_decision | journey_validation
  finder_signal          jsonb not null default '{}'::jsonb,

  -- thin LLM judge (Phase 1)
  judge_verdict          text,                                 -- engage | maybe | skip
  judge_score            numeric(6,2),
  judge_rationale        text,                                 -- the one-line "why engage"
  on_theme               boolean,                              -- PRIMARY signal: fits our content
  audience_fit           text,                                 -- core | adjacent | off (collaborators = positive)
  judge_model            text,
  judge                  jsonb not null default '{}'::jsonb,
  dismiss_reason         text,                                 -- you rejected a high pick = finder false-positive signal

  -- provenance into the marketing graph
  source_research_run_id uuid references marketing.research_runs(id) on delete set null,
  run_correlation_id     uuid,                                 -- ties to the agent_execution_logs run

  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create trigger trg_reply_candidates_updated_at
  before update on marketing.reply_candidates
  for each row execute function marketing.set_updated_at();

-- external_id (derived from the Reddit thread URL) is the dedup/upsert key. NOT a
-- partial index: PostgREST can only arbitrate ON CONFLICT against a plain unique
-- index, and NULLs are distinct in Postgres anyway — so undated/null rows never
-- false-collide while non-null (platform, external_id) pairs stay unique.
create unique index uq_reply_candidates_extid on marketing.reply_candidates (platform, external_id);
create index idx_reply_candidates_queue  on marketing.reply_candidates (platform, status, judge_score desc);
create index idx_reply_candidates_use    on marketing.reply_candidates (intended_use);
create index idx_reply_candidates_source on marketing.reply_candidates (source_research_run_id);

-- ════════════════════════════════════════════════════════════════════════════
-- reply_drafts — one row per variant. draft_text is the diff anchor (write-once).
-- Dropped variants are kept too: a variant you never pick is a training signal.
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.reply_drafts (
  id                     uuid primary key default gen_random_uuid(),
  candidate_id           uuid not null references marketing.reply_candidates(id) on delete cascade,
  platform               text not null,
  variant_index          smallint not null,                   -- 0 .. N-1
  angle                  text,                                 -- this variant's flip-move
  draft_text             text not null,                        -- verbatim AI copy = the diff anchor (write-once)
  rationale              text,
  model                  text,
  style_version          text,                                 -- e.g. 'guide-v1' (git-tracked guide in Phase 1)

  -- compliance gate result (regex floor in Phase 1). Pessimistic default.
  gate_status            text not null default 'ungated',     -- ungated | pass | flag | block (block = hidden from review)
  gate_violations        jsonb not null default '[]'::jsonb,  -- [{rule, severity, match}]
  gate_ruleset_version   text,                                 -- which floor version screened it

  chosen                 boolean not null default false,      -- your selection signal

  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create trigger trg_reply_drafts_updated_at
  before update on marketing.reply_drafts
  for each row execute function marketing.set_updated_at();

create unique index uq_reply_drafts_variant on marketing.reply_drafts (candidate_id, variant_index);
create index idx_reply_drafts_candidate on marketing.reply_drafts (candidate_id);
create index idx_reply_drafts_chosen    on marketing.reply_drafts (candidate_id) where chosen;

-- ════════════════════════════════════════════════════════════════════════════
-- reply_ledger — the training ledger WITH DIFF CAPTURE. One row per SENT reply.
-- Denormalized so every row stands alone as a training example. Append-once at
-- send; only outcome + your labels are mutable (column-scoped grants below) so
-- ai_draft / final_sent / edit_* are write-once even under the anon key.
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.reply_ledger (
  id                     uuid primary key default gen_random_uuid(),
  replied_at             timestamptz,
  platform               text not null default 'reddit',

  -- post snapshot (copied from the candidate at send)
  external_id            text,
  post_url               text,
  community              text,
  post_title             text,
  post_excerpt           text,
  post_author            text,

  -- finder + judge signal (calibrates the find/judge loop)
  finder_score           integer,
  flips                  text,
  soft_triggers          text,
  angle                  text,
  judge_verdict          text,
  judge_score            numeric(6,2),
  judge_rationale        text,

  -- DIFF CAPTURE — the core voice-learning signal
  ai_draft               text,                                 -- chosen variant as the AI wrote it (null if you wrote your own)
  chosen_variant         smallint,
  final_sent             text not null,                        -- your edited-and-sent reply (the moat)
  edit_delta             text,                                 -- difflib unified diff (ai_draft -> final_sent)
  edit_distance          integer,
  edit_ratio             numeric(5,4),                         -- SequenceMatcher.ratio(); 1.0 = sent unedited
  edit_type              text,                                 -- none | voice | substance | both | compliance | rejected | legacy
  intent_preserved       boolean,
  style_notes            text,                                 -- one-line "why I changed it" (supervised label)
  predicted_grade        text,                                 -- strong | ok | off, set at send (calibration anchor)

  -- non-destructive curation overrides for the distilled prompt (Phase 2)
  curation_tag           text,                                 -- exemplar | negative
  curation_note          text,

  reply_url              text,                                 -- permalink to my posted reply, once known
  rationale              text,

  -- outcome (updatable; two layers like research_runs)
  outcome_score          integer,
  op_replied             text,                                 -- yes | no | unknown
  led_to_profile         text,                                 -- yes | no | unknown
  outcome_notes          text,
  outcome_metrics        jsonb,
  outcome_checked_at     timestamptz,

  -- provenance
  candidate_id           uuid references marketing.reply_candidates(id) on delete set null,
  draft_id               uuid references marketing.reply_drafts(id)     on delete set null,
  style_version          text,
  source_research_run_id uuid references marketing.research_runs(id)    on delete set null,
  idea_id                text,                                 -- soft ref (product_ideas is integer SQLite elsewhere)
  legacy_sqlite_id       integer,                              -- backfill provenance + idempotency

  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create trigger trg_reply_ledger_updated_at
  before update on marketing.reply_ledger
  for each row execute function marketing.set_updated_at();

create unique index uq_reply_ledger_legacy on marketing.reply_ledger (legacy_sqlite_id);  -- plain (NULLs distinct) so the idempotent backfill upsert can arbitrate on it
create index idx_reply_ledger_replied  on marketing.reply_ledger (replied_at desc);
create index idx_reply_ledger_edittype on marketing.reply_ledger (edit_type);
create index idx_reply_ledger_curation on marketing.reply_ledger (curation_tag) where curation_tag is not null;
create index idx_reply_ledger_calib    on marketing.reply_ledger (predicted_grade) where outcome_checked_at is not null;

-- ════════════════════════════════════════════════════════════════════════════
-- Views — lean eval surface. security_invoker so the caller's RLS applies.
-- Legacy backfill rows are excluded from the voice metrics (no AI draft to diff).
-- ════════════════════════════════════════════════════════════════════════════
create view marketing.reply_weekly_metrics with (security_invoker = on) as
select date_trunc('week', replied_at)::date                  as week,
       platform,
       count(*)                                               as sent,
       count(*) filter (where edit_type = 'none')             as unedited,
       round(count(*) filter (where edit_type = 'none')::numeric
             / nullif(count(*), 0), 3)                        as unedited_rate,
       round(avg(edit_ratio)::numeric, 4)                     as avg_edit_ratio,
       round(avg(outcome_score)::numeric, 1)                  as avg_outcome_score
from marketing.reply_ledger
where replied_at is not null
  and coalesce(edit_type, '') <> 'legacy'
group by 1, 2;

create view marketing.reply_calibration with (security_invoker = on) as
select predicted_grade,
       count(*)                                               as graded,
       round(avg(outcome_score)::numeric, 1)                  as avg_outcome_score,
       round(avg((op_replied = 'yes')::int)::numeric, 3)      as op_reply_rate
from marketing.reply_ledger
where predicted_grade is not null
  and outcome_checked_at is not null
  and op_replied in ('yes', 'no')
group by predicted_grade;

-- ════════════════════════════════════════════════════════════════════════════
-- Privileges + RLS — anon posture, mirroring 20260624000001. INSERT + SELECT for
-- all; UPDATE is COLUMN-SCOPED so the verbatim draft/sent text is write-once even
-- under the public anon key. service_role keeps full UPDATE (admin/break-glass).
-- No DELETE for anon (protect history). updated_at is set by the trigger, so it
-- is intentionally NOT in the column grants.
-- ════════════════════════════════════════════════════════════════════════════
grant select, insert on marketing.reply_candidates to anon, authenticated, service_role;
grant select, insert on marketing.reply_drafts     to anon, authenticated, service_role;
grant select, insert on marketing.reply_ledger     to anon, authenticated, service_role;

-- candidates: judge fills its verdict; you can dismiss; status advances.
grant update (status, judge_verdict, judge_score, judge_rationale, on_theme, audience_fit,
              judge_model, judge, dismiss_reason)
  on marketing.reply_candidates to anon, authenticated;

-- drafts: gate result + your variant choice. draft_text stays write-once.
grant update (gate_status, gate_violations, gate_ruleset_version, chosen, style_version)
  on marketing.reply_drafts to anon, authenticated;

-- ledger: only outcome + your labels mutate. ai_draft / final_sent / edit_* (incl.
-- edit_type) write-once — they're the diff/calibration signal, set once at log time.
grant update (reply_url, intent_preserved, style_notes, predicted_grade,
              curation_tag, curation_note,
              outcome_score, op_replied, led_to_profile, outcome_notes, outcome_metrics,
              outcome_checked_at)
  on marketing.reply_ledger to anon, authenticated;

grant update on marketing.reply_candidates, marketing.reply_drafts, marketing.reply_ledger to service_role;

grant select on marketing.reply_weekly_metrics, marketing.reply_calibration to anon, authenticated, service_role;

alter table marketing.reply_candidates enable row level security;
alter table marketing.reply_drafts     enable row level security;
alter table marketing.reply_ledger     enable row level security;

-- candidates — select / insert / update (no delete)
create policy reply_candidates_select on marketing.reply_candidates for select to anon, authenticated using (true);
create policy reply_candidates_insert on marketing.reply_candidates for insert to anon, authenticated with check (true);
create policy reply_candidates_update on marketing.reply_candidates for update to anon, authenticated using (true) with check (true);

-- drafts — select / insert / update (no delete)
create policy reply_drafts_select on marketing.reply_drafts for select to anon, authenticated using (true);
create policy reply_drafts_insert on marketing.reply_drafts for insert to anon, authenticated with check (true);
create policy reply_drafts_update on marketing.reply_drafts for update to anon, authenticated using (true) with check (true);

-- ledger — select / insert / update (no delete)
create policy reply_ledger_select on marketing.reply_ledger for select to anon, authenticated using (true);
create policy reply_ledger_insert on marketing.reply_ledger for insert to anon, authenticated with check (true);
create policy reply_ledger_update on marketing.reply_ledger for update to anon, authenticated using (true) with check (true);

-- ============================================================================
-- End of migration.
-- ============================================================================
