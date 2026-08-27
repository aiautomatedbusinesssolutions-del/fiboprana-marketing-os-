-- ============================================================================
-- 20260703000001_video_system.sql
-- Fiboprana marketing — the Marketing OS spine (MARKETING_OS.md §4)
-- ----------------------------------------------------------------------------
-- Four new tables that generalize what reply_ledger proved:
--
--   videos         -> the lineage spine node (research_run -> video -> posts)
--   decisions      -> the UNIFORM LEVER LEDGER: one row per lever-tagged decision
--                     (title, thumbnail, upload slot, clip pick, topic pick...)
--                     with alternatives + reasoning + optional pre-publish
--                     prediction. The training surface for every future agent.
--   experiments    -> the hypotheses layer. DB-ENFORCED: at most ONE active
--                     experiment globally, PLUS at most one concurrent YouTube
--                     Test & Compare (allowed because T&C randomizes WITHIN each
--                     video, so it can't confound a cross-video experiment).
--   outcome_checks -> the analytics WORK QUEUE (24h/7d/28d windows). Opened at
--                     publish; closed by the founder now, the analytics agent
--                     later. An overdue open row is the nag that keeps the
--                     outcome loop honest. Ships day 1; the agent is built last.
--
-- Plus two fixes to existing tables:
--   * reply_ledger.predicted_grade UPDATE revoked — it's the calibration anchor
--     and must be write-once at send like edit_type (same reasoning as
--     20260627000001). Post-hoc prediction edits would poison calibration.
--     Consequence (accepted): the 3 early ledger rows whose predicted_grade is
--     still NULL stay NULL — backdating predictions is exactly what this blocks.
--   * reply_candidates.video_id — nullable FK so future YouTube-comment replies
--     attribute to their video. NULL for keyword-scan replies (honest no-upstream).
--
-- POSTURE: same anon-key + column-scoped-UPDATE pattern as 20260626000001.
-- Write-once fields (reasoning, decision payload, predictions, hypotheses) get
-- NO UPDATE grant; only lifecycle/outcome columns mutate. No DELETE anywhere.
--
-- HOW TO APPLY:  Supabase Studio -> SQL Editor -> paste this file -> Run.
--   `marketing` is already exposed; no API settings change needed.
-- Assumes marketing.set_updated_at() + pgcrypto from 20260624000001.
-- NOTE: the video_lineage view lands in 20260703000002 (it needs
-- content_calendar.video_id, which that migration adds).
-- ============================================================================

-- ════════════════════════════════════════════════════════════════════════════
-- videos — the lineage spine node. One row per long-form video, idea -> closed.
-- Retires videos/IDEAS_LOG.md as dedup memory (slug is the dedup key). Facts of
-- record (status, paths, publish ids) mutate; provenance + reasoning write-once.
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.videos (
  id                     uuid primary key default gen_random_uuid(),
  slug                   text not null,                        -- kebab dedup key, e.g. 'who-pays-bad-ai-advice'
  pillar                 text,                                 -- news | feature | ascent | styles
  status                 text not null default 'idea',         -- idea | picked | scripted | recorded | edited |
                                                               -- packaged_draft | packaging_approved | published |
                                                               -- closed | skipped
  title_final            text,                                 -- the title that actually shipped
  youtube_video_id       text,                                 -- set at publish; arms the outcome clock with published_at
  published_at           timestamptz,

  -- artifact paths (repo-relative; facts of record, not training signal)
  script_path            text,
  transcript_path        text,
  deck_path              text,

  reasoning              text,                                 -- why this topic made the cut (training gold, write-once)
  payload                jsonb not null default '{}'::jsonb,   -- evolving extras (thumbnail file, T&C arm notes...)
  notes                  text,                                 -- human margin notes (mutable)
  legacy                 boolean not null default false,       -- backfilled pre-OS videos: excluded from calibration

  -- provenance: closes research -> video attribution (one hop only)
  source_research_run_id uuid references marketing.research_runs(id) on delete set null,
  source_item_id         text,                                 -- which seed in the content pull

  -- outcome (updateable; two layers like research_runs)
  outcome_verdict        text,                                 -- L1 founder: strong | ok | off
  outcome_note           text,
  outcome_metrics        jsonb,                                -- L2: views/CTR/retention snapshots per window
  outcome_checked_at     timestamptz,

  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create trigger trg_videos_updated_at
  before update on marketing.videos
  for each row execute function marketing.set_updated_at();

create unique index uq_videos_slug on marketing.videos (slug);
create unique index uq_videos_ytid on marketing.videos (youtube_video_id);  -- plain: NULLs distinct pre-publish
create index idx_videos_status  on marketing.videos (status);
create index idx_videos_source  on marketing.videos (source_research_run_id);
create index idx_videos_pub     on marketing.videos (published_at desc);

-- ════════════════════════════════════════════════════════════════════════════
-- decisions — the uniform lever ledger (the system's second moat table).
-- One row per lever-tagged decision, self-contained as a training example:
-- WHAT was chosen, what was REJECTED and why, the REASONING, and (only when a
-- real hypothesis exists, mode='experiment') a falsifiable pre-publish
-- PREDICTION. Outcomes close later against the lever's OWN identifying metric
-- (outcome_basis — see MARKETING_OS.md §6); rows with no identifying signal get
-- outcome_basis='none' and are excluded from calibration.
-- Lever + surface vocabulary is enforced in code: hermes/levers.py.
-- Replies keep their earned dedicated ledger; everything else logs here.
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.decisions (
  id                  uuid primary key default gen_random_uuid(),
  agent_name          text not null default 'founder',        -- founder | packager | repurposer | research ...
  model               text,                                   -- LLM used, if an agent decided
  lever               text not null,                          -- hermes/levers.py vocabulary
  surface             text not null,                          -- youtube | x | reddit | shorts (hook@x ≠ hook@shorts)
  subject_type        text not null,                          -- video | post | research_run | reply
  subject_id          uuid not null,                          -- the row this decision is about
  mode                text not null default 'routine',        -- routine | experiment

  -- the decision itself (write-once)
  decision            jsonb not null,                         -- {chosen, alternatives:[{option, why_rejected}], recipe_version}
  reasoning           text,                                   -- the why, in prose (training gold)

  -- hypothesis + prediction (REQUIRED iff mode='experiment'; enforced in the store)
  hypothesis          text,
  predicted_grade     text,                                   -- strong | ok | off
  prediction          jsonb,                                  -- {metric, band, horizon}
  experiment_id       uuid,                                   -- FK added below (experiments is created after this table)
  ab_variant          text,                                   -- which arm this subject got

  decided_at          timestamptz not null default now(),     -- vs created_at: divergence is flagged in calibration

  -- outcome (the ONLY mutable layer; closed by founder now, analytics agent later)
  outcome_verdict     text,                                   -- L1 founder: strong | ok | off
  outcome_score       numeric,
  outcome_basis       text,                                   -- WHICH metric identified this lever ('none' = no clean signal)
  outcome_metrics     jsonb,
  outcome_note        text,
  prediction_hit      boolean,                                -- did the pre-publish prediction land?
  outcome_checked_at  timestamptz,

  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);

create trigger trg_decisions_updated_at
  before update on marketing.decisions
  for each row execute function marketing.set_updated_at();

create index idx_decisions_lever   on marketing.decisions (lever, surface, decided_at desc);
create index idx_decisions_subject on marketing.decisions (subject_type, subject_id);
create index idx_decisions_open    on marketing.decisions (decided_at) where outcome_checked_at is null;
create index idx_decisions_exp     on marketing.decisions (experiment_id) where experiment_id is not null;

-- ════════════════════════════════════════════════════════════════════════════
-- experiments — the hypotheses layer. One row per hypothesis SPANNING min_n
-- observations (a thumbnail experiment covers several videos — never one row
-- per video). The two partial unique indexes are the discipline, DB-enforced:
--   * at most ONE active experiment globally (3 videos/wk = one video pool;
--     two concurrent cross-video experiments WOULD confound each other), and
--   * at most one concurrent YouTube Test & Compare (within-video randomized,
--     so it may run alongside the global slot — the thumbnail exception).
-- The store additionally guards title_wording vs T&C (shared CTR numerator).
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.experiments (
  id                 uuid primary key default gen_random_uuid(),
  lever              text not null,
  surface            text not null,
  hypothesis         text not null,                            -- falsifiable, in prose
  prediction         text,                                     -- what we expect to see if true
  method             text not null,                            -- yt_test_compare | sequential | ab_manual
  arms               jsonb not null default '[]'::jsonb,       -- the variants under test
  scope              text,                                     -- e.g. 'news-pillar videos' (stratify by pillar)
  min_n              integer,                                  -- observations before a verdict is allowed
  status             text not null default 'draft',            -- draft | active | concluded | abandoned
  started_at         timestamptz,
  concluded_at       timestamptz,
  result_verdict     text,                                     -- supported | refuted | inconclusive
  result_note        text,
  learned_rule       text,                                     -- the takeaway, ready to paste into the recipe doc
  applied_to_recipe  text,                                     -- where the rule landed (file + version)

  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

create trigger trg_experiments_updated_at
  before update on marketing.experiments
  for each row execute function marketing.set_updated_at();

-- The discipline, enforced where it can't be forgotten:
create unique index one_active_experiment on marketing.experiments ((1))
  where status = 'active' and method <> 'yt_test_compare';
create unique index one_active_tc on marketing.experiments ((1))
  where status = 'active' and method = 'yt_test_compare';
create index idx_experiments_status on marketing.experiments (status);

-- decisions.experiment_id FK, deferred to here so the referenced table exists.
alter table marketing.decisions
  add constraint fk_decisions_experiment
  foreign key (experiment_id) references marketing.experiments(id) on delete set null;

-- ════════════════════════════════════════════════════════════════════════════
-- outcome_checks — the analytics work queue. The dispatcher opens rows when an
-- entity publishes (published_at arms the clock); whoever closes them writes the
-- raw numbers into result and labels trust via source (hand-entered X numbers
-- are queryably lower-trust than API pulls). Overdue open rows nag in the daily
-- banner; a check can be honestly 'skipped' but never silently rots.
-- ════════════════════════════════════════════════════════════════════════════
create table marketing.outcome_checks (
  id            uuid primary key default gen_random_uuid(),
  entity_table  text not null,                                 -- videos | content_calendar | reply_ledger
  entity_id     uuid not null,
  window_label  text not null,                                 -- 24h | 7d | 28d
  due_at        timestamptz not null,
  status        text not null default 'open',                  -- open | done | skipped
  result        jsonb,                                         -- the raw pull (views, ctr, retention...)
  source        text,                                          -- yt_api | manual | screenshot
  checked_at    timestamptz,

  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create trigger trg_outcome_checks_updated_at
  before update on marketing.outcome_checks
  for each row execute function marketing.set_updated_at();

-- One check per entity+window; the upsert arbitration key that makes the
-- dispatcher's open-at-publish sweep idempotent (plain index — PostgREST can't
-- arbitrate on a partial one).
create unique index uq_outcome_checks_entity on marketing.outcome_checks (entity_table, entity_id, window_label);
create index idx_outcome_checks_due on marketing.outcome_checks (status, due_at);

-- ════════════════════════════════════════════════════════════════════════════
-- Views — security_invoker so the caller's RLS applies.
-- ════════════════════════════════════════════════════════════════════════════
-- Calibration: experiment-mode rows with a CLEAN identifying signal only.
-- backdated_rows flags decided_at/created_at divergence (the anon key COULD
-- backdate a prediction — we flag it rather than pretend it's impossible).
create view marketing.decision_calibration with (security_invoker = on) as
select lever,
       surface,
       predicted_grade,
       count(*)                                                as n,
       round(avg(outcome_score)::numeric, 1)                   as avg_outcome_score,
       round(avg(prediction_hit::int)::numeric, 3)             as prediction_hit_rate,
       count(*) filter
         (where abs(extract(epoch from (created_at - decided_at))) > 300)
                                                               as backdated_rows
from marketing.decisions
where mode = 'experiment'
  and outcome_checked_at is not null
  and coalesce(outcome_basis, 'none') <> 'none'
group by 1, 2, 3;

-- Heartbeat: a VIEW over the ops log (no dual-write drift). The daily banner's
-- "which agent is stale" read.
create view marketing.agent_heartbeat with (security_invoker = on) as
select agent_name,
       max(created_at) filter (where status = 'success')       as last_success,
       max(created_at)                                          as last_event,
       round((extract(epoch from (now() - max(created_at) filter (where status = 'success'))) / 3600.0)::numeric, 1)
                                                                as hours_since_success
from marketing.agent_execution_logs
where event = 'heartbeat'
group by agent_name;

-- ════════════════════════════════════════════════════════════════════════════
-- Privileges + RLS — anon posture, column-scoped UPDATE (write-once moat).
-- updated_at is trigger-set, intentionally absent from every grant list.
-- ════════════════════════════════════════════════════════════════════════════
grant select, insert on marketing.videos         to anon, authenticated, service_role;
grant select, insert on marketing.decisions      to anon, authenticated, service_role;
grant select, insert on marketing.experiments    to anon, authenticated, service_role;
grant select, insert on marketing.outcome_checks to anon, authenticated, service_role;

-- videos: lifecycle facts + outcome mutate; slug/provenance/reasoning/legacy write-once.
grant update (pillar, status, title_final, youtube_video_id, published_at,
              script_path, transcript_path, deck_path, payload, notes,
              outcome_verdict, outcome_note, outcome_metrics, outcome_checked_at)
  on marketing.videos to anon, authenticated;

-- decisions: ONLY the outcome layer mutates. The decision, reasoning, hypothesis,
-- prediction, and lever identity are write-once — they ARE the training set.
grant update (outcome_verdict, outcome_score, outcome_basis, outcome_metrics,
              outcome_note, prediction_hit, outcome_checked_at)
  on marketing.decisions to anon, authenticated;

-- experiments: lifecycle + result mutate; the hypothesis/prediction/method/arms
-- under test are write-once (no moving the goalposts mid-experiment).
grant update (status, started_at, concluded_at, result_verdict, result_note,
              learned_rule, applied_to_recipe)
  on marketing.experiments to anon, authenticated;

-- outcome_checks: closing a check mutates status/result/source/checked_at; the
-- entity + window + due date are write-once (the clock can't be quietly moved).
grant update (status, result, source, checked_at)
  on marketing.outcome_checks to anon, authenticated;

grant update on marketing.videos, marketing.decisions, marketing.experiments,
                marketing.outcome_checks to service_role;

grant select on marketing.decision_calibration, marketing.agent_heartbeat
  to anon, authenticated, service_role;

alter table marketing.videos         enable row level security;
alter table marketing.decisions      enable row level security;
alter table marketing.experiments    enable row level security;
alter table marketing.outcome_checks enable row level security;

-- select / insert / update, NO delete (protect history) — all four tables.
create policy videos_select on marketing.videos for select to anon, authenticated using (true);
create policy videos_insert on marketing.videos for insert to anon, authenticated with check (true);
create policy videos_update on marketing.videos for update to anon, authenticated using (true) with check (true);

create policy decisions_select on marketing.decisions for select to anon, authenticated using (true);
create policy decisions_insert on marketing.decisions for insert to anon, authenticated with check (true);
create policy decisions_update on marketing.decisions for update to anon, authenticated using (true) with check (true);

create policy experiments_select on marketing.experiments for select to anon, authenticated using (true);
create policy experiments_insert on marketing.experiments for insert to anon, authenticated with check (true);
create policy experiments_update on marketing.experiments for update to anon, authenticated using (true) with check (true);

create policy outcome_checks_select on marketing.outcome_checks for select to anon, authenticated using (true);
create policy outcome_checks_insert on marketing.outcome_checks for insert to anon, authenticated with check (true);
create policy outcome_checks_update on marketing.outcome_checks for update to anon, authenticated using (true) with check (true);

-- ════════════════════════════════════════════════════════════════════════════
-- Fixes to existing tables.
-- ════════════════════════════════════════════════════════════════════════════
-- predicted_grade is the calibration anchor — write-once at send, like edit_type.
revoke update (predicted_grade) on marketing.reply_ledger from anon, authenticated;

-- Future YouTube-comment replies attribute to their video; NULL for keyword-scan
-- replies (their honest attribution is channel + finder signal, not a video).
alter table marketing.reply_candidates
  add column video_id uuid references marketing.videos(id) on delete set null;

-- ============================================================================
-- End of migration.
-- ============================================================================
