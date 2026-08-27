# Marketing OS

*Synthesized 2026-07-02 from four adversarially-critiqued designs. Skeleton: the ops design (dispatcher + state machines + outcome_checks), with the learning contract from the learning design, the uniform lever ledger from the data design, and the reps-first ladder + not-yet list from the mvp design. Every critic-confirmed issue is fixed inline; tradeoffs are noted where designs conflicted.*

**One sentence:** cron + a shared Supabase `marketing` schema move work between single-job agents; every taste decision is a founder gate the system *waits at*; every decision writes a lever-tagged row with a pre-publish prediction; outcomes flow back onto those same rows through a work queue that exists from day 1 even though the agent that services it is built last.

---

## 1. The big picture

```
                    SUNDAY CRON                          FOUNDER GATES (⛔ = system waits)
 HN/RSS/Exa ─→ [research] ─→ research_runs ──⛔ verdict
                               │ content_pull
                               ├─→ content_calendar (idea) ──⛔ pick
                               └─→ product_ideas.build_stage ──⛔ build-or-content
                                            │
            videos (idea) ←── founder picks 3 topics (news / feature / ascent / styles)
               │ scripted → recorded → edited        [manual + silence_cut/transcribe]
               ▼
       [packaging session] ─→ decisions rows (lever + alternatives + prediction NOW)
               │                      videos → packaged_draft ──⛔ approve → upload by hand
               ▼                                               ──⛔ published_at set
       dispatcher opens outcome_checks (24h/7d/28d) ──────────────────────────┐
               │                                                              │
       [repurposing session] ─→ content_calendar rows (shorts) ──⛔ post by hand
                                                                              │
 DAILY CRON                                                                   ▼
 Reddit RSS ─→ [reply finder→judge→drafter→gate] ─→ reply_candidates/drafts   │
               ──⛔ review CLI: rewrite, grade, hand-send ─→ reply_ledger ────┤
                                                                              ▼
 WEEKLY (local, in-session)                                          [analytics agent]
 [x-batch] ─→ content_calendar (draft) ──⛔ edit + Typefully          closes outcome_checks;
                                                                     founder L1 verdicts Sunday
 LINEAGE: research_run → video → content_calendar (shorts + posts)
 Replies attribute to channel + finder signal, NOT to research runs
 (honest: keyword-scan replies have no upstream; video_id is nullable
  for future YouTube-comment replies).
```

Four workflows, one loop:

1. **Research (weekly)** feeds topic picks for 3 long videos.
2. **Per-video**: packaging (title/thumb/description/slot) → publish → repurposing (shorts per channel, each a `content_calendar` row).
3. **Replies (daily)**: per-channel finder → shared judge → drafter → compliance gate → founder review → hand-send. Reddit live; X hand-fed now, scanner later.
4. **Analytics** closes the outcome columns everyone else opened. Decisions flow *down* through status transitions; outcomes flow *back up* through `outcome_*` columns.

No orchestrator (locked). Coordinator = one dispatcher cron + status columns as state machines + `outcome_checks` as the work queue + heartbeats.

---

## 2. Agent roster

| # | Agent | Job | Cadence | Levers owned | Primary metric | Status today |
|---|-------|-----|---------|--------------|----------------|--------------|
| 1 | **research** | Scan → digest → content pull | Weekly (Sun cron) | `digest_sourcing`, `topic_selection` (proposes; founder decides) | founder verdict strong-rate; derived-video 7d views | **LIVE** (Railway cron) |
| 2a | **reply — Reddit** | Find → judge → draft → gate → review → hand-send | Daily | `reply_voice`, `candidate_selection` | edit_ratio ↑; op_replied at 7d | **BUILT**, Phase 1 signed off; cron door pending |
| 2b | **reply — X** | Same pipeline, X adapter | Daily (later) | `reply_voice` (X dialect), `candidate_selection` | same | **Manual-reps stage** — judge/drafter/gate already channel-agnostic (only `XAdapter.scan()` is stubbed); hand-feed candidates now |
| 3 | **x-content** | Weekly post batch → Typefully | Weekly, local in-session (no cron — see §5) | `hook`, `pillar_mix`, `post_time` | impressions/post at 7d (hand-entered) | **SEMI** — `content/x_run.py` works, needs Supabase wiring |
| 4 | **packager** | Title / thumbnail spec / description / upload slot per video | Per-video (session, not cron) | `title_wording`, `thumbnail_composition`, `description_seo`, `upload_time` | per-lever metric (see §6 mapping) | **MANUAL reps now**; ledger this week; agent after ~15–20 logged reps with closed outcomes |
| 5 | **repurposer** | Long video → shorts per channel | Per-video (session) | `clip_selection`, `hook`, `caption_style` | short views 7d | **ZERO reps** — reps first; rows live in `content_calendar` until the shape is earned |
| 6 | **analytics** (outcome-writer) | Close due `outcome_checks`; score predictions | Daily sweep | none — grades everyone else's | % of checks closed on time | **Schema ships this week; agent built last** — the founder's manual Sunday closes ARE its manual reps |

Every agent: plain `run_<job>()` in its module, thin cron + MCP wrappers, ops rows to `agent_execution_logs`, learning rows to the ledger with lever tag + prediction at decision time. Python, stdlib-first.

---

## 3. The learning contract

One canonical row shape, everywhere. `reply_ledger` proved it; the new `decisions` table generalizes it. A learning row is valid only if it captures all five layers at the right time:

| Layer | Fields | Written when | Mutable? |
|---|---|---|---|
| **Identity** | `agent_name` (or `founder`), `decided_at`, `lever`, `surface`, `subject_type` + `subject_id` | at decision | write-once |
| **Decision** | `decision` jsonb — **chosen + alternatives considered + why rejected** + `recipe_version` | at decision | write-once |
| **Reasoning** | `reasoning` prose — the why (training gold) | at decision | write-once |
| **Prediction** | `predicted_grade` (strong\|ok\|off) + `prediction` jsonb `{metric, band, horizon}` — **required only when `mode='experiment'`**, optional otherwise | at decision, before publish | write-once |
| **Outcome** | `outcome_verdict` (L1 founder), `outcome_metrics` (L2), `outcome_basis` (which metric identified this lever — see §6), `prediction_hit`, `outcome_checked_at` | at window close | column-scoped UPDATE only |

Rules that make rows worth training on:

1. **Alternatives are half the signal.** "Chose title B" teaches little; "B over A because A buries the keyword, over C because C reads clickbait" teaches the rulebook. Mandatory in the `decision` jsonb. Costs one sentence when Claude is the scribe.
2. **Predictions are honest only if pre-publish and write-once.** `log_decision()` refuses subjects already published; prediction columns get no UPDATE grant; `decided_at` vs `published_at` is auditable, and the calibration view flags `created_at`/`decided_at` divergence (the anon key could backdate — flag it, don't pretend it's impossible).
3. **No hypothesis theater.** Frozen or routine levers log the decision + reasoning (cheap, still training gold) with `mode='routine'` and NO forced prediction. A falsifiable prediction is demanded only where a real hypothesis exists — usually the one lever under an active experiment. *(Fixes the critic-confirmed "12 rote predictions/week" junk-calibration problem in three of four designs.)*
4. **Levers are a code-enforced vocabulary** in `fleet/levers.py` (text column, no ENUM): `topic_selection, title_wording, thumbnail_composition, description_seo, upload_time, hook, clip_selection, caption_style, pillar_mix, post_time, reply_voice, candidate_selection, subreddit_choice, digest_sourcing`. Every decision row also carries `surface` (youtube|x|reddit|shorts) so `hook` on X never contaminates `hook` on shorts.
5. **Enforcement honesty:** lever validation, refuse-if-published, and experiment guards live in the store funnel (`fleet/asset_store.py`) — convention, bypassable via raw PostgREST. Only the column-scoped grants are real DB enforcement. Acceptable: the scribe is Claude-in-session using the store, and the grants protect the moat fields.

---

## 4. Data architecture

### Principles (inherited from what already works)
Migrations-only; text vocab in code, not ENUMs; jsonb for evolving payloads; write-once via column-scoped UPDATE grants (the `20260626` reply pattern) on **every** new table from day 1; no DELETE on any ledger; ops (`agent_execution_logs`) never mixed into training ledgers; one store module per domain.

### Unchanged
`research_runs`, `agent_execution_logs`, `reply_*` tables and views, `product_ideas` (build gate already lives there via `build_stage`), `marketing_goals`.

### One fix to the template
`reply_ledger.predicted_grade` is currently in the anon UPDATE grant (critic-verified) — post-hoc prediction edits are possible in the flagship calibration anchor. **Revoke it** in this week's migration.

### NEW: `videos` — the lineage spine node
`id, source_research_run_id FK, source_item_id, slug unique, pillar (news|feature|ascent|styles), status (idea|picked|scripted|recorded|edited|packaged_draft|packaging_approved|published|closed|skipped), title_final, youtube_video_id unique, published_at, script_path, transcript_path, deck_path, reasoning, payload jsonb, outcome_verdict/note/metrics/checked_at, timestamps.`
Retires IDEAS_LOG.md as dedup memory (`select slug, title_final from videos`); keep the md as human notes for a quarter. Backfill V1–V3 tagged `legacy` (calibration-excluded).

### NEW: `decisions` — the uniform lever ledger (the system's second moat table)
One table, one training surface for every future agent (packaging, topic picks, clip selection, X batch choices) — replies keep their earned dedicated ledger.
`id, agent_name, model, lever (validated), surface, subject_type (video|post|research_run|reply), subject_id uuid, mode (routine|experiment), decision jsonb {chosen, alternatives:[{option, why_rejected}], recipe_version}, reasoning, hypothesis, predicted_grade, prediction jsonb, experiment_id FK, ab_variant, decided_at, outcome_verdict, outcome_score, outcome_basis, outcome_metrics jsonb, outcome_note, prediction_hit bool, outcome_checked_at, created_at.`
Grants: INSERT + column-scoped UPDATE on `outcome_*`/`prediction_hit` only. Indexes: `(lever, surface, decided_at)`; partial on `outcome_checked_at IS NULL`.
*Tradeoff vs per-domain tables: one generic table is slightly looser typing, but one training surface + one grants pattern beats four near-identical tables at this volume.*

### NEW: `experiments` — the hypotheses layer
`id, lever, surface, hypothesis, prediction, method (yt_test_compare|sequential|ab_manual), arms jsonb, scope (e.g. "news-pillar videos"), min_n int, status (draft|active|concluded|abandoned), started_at, concluded_at, result_verdict (supported|refuted|inconclusive), result_note, learned_rule text, applied_to_recipe text, timestamps.`
**Enforcement (fixes the wrong-invariant index every critic flagged):**
- `CREATE UNIQUE INDEX one_active_experiment ON marketing.experiments ((1)) WHERE status='active' AND method <> 'yt_test_compare';` — **at most ONE active experiment globally**, because at 3 videos/week there is only one video pool and per-lever uniqueness permits exactly the multi-lever confound the rule exists to prevent.
- `CREATE UNIQUE INDEX one_active_tc ON marketing.experiments ((1)) WHERE status='active' AND method = 'yt_test_compare';` — plus at most one Test & Compare experiment, allowed to run concurrently **because it is within-video randomized** (the thumbnail exception).
- Store guard: a `title_wording` experiment cannot open while a T&C is active and vice versa — they share the CTR numerator.
- A thumbnail experiment = **one row per hypothesis spanning min_n videos** (each video an observation), not one row per video — fixes the "3 concurrent thumbnail experiments break the index" contradiction.

### NEW: `outcome_checks` — the analytics work queue (ships day 1)
`id, entity_table, entity_id uuid, window_label (24h|7d|28d), due_at, status (open|done|skipped), result jsonb (the raw pull), source (yt_api|manual|screenshot), checked_at.` Index `(status, due_at)`.
The dispatcher **opens** rows at publish (`published_at` arms the clock — the right causal anchor); the founder now / analytics agent later **closes** them. Overdue open rows are the nag that keeps hand-entry honest. `source` labels trust (hand-entered X numbers are queryably lower-trust).
*Tradeoff: merged the data design's `outcome_snapshots` into `checks.result` + `source` — loses re-pull history granularity, saves a whole table and its broken-upsert bug.*

### EXTEND: `content_calendar` → the posts ledger (X posts AND shorts)
Add: `video_id FK videos, post_type (x_post|x_thread|short|community_post|native_reddit), platform, pillar, hook_text, draft_text (write-once diff anchor), final_text, edit_delta, edit_ratio, edit_type, gate_status/gate_violations/gate_ruleset_version, typefully_draft_id, external_url, published_at, outcome_verdict/note/metrics/checked_at.`
**Same migration MUST fix grants** (critic-confirmed hole): revoke DELETE, replace the for-all policy, column-scope UPDATE — otherwise the lineage middle node is silently deletable under the anon key.
Shorts live here as `post_type='short'` rows with clip details in `payload` jsonb until 5–10 real shorts exist, then promote to a `shorts` table by migration — exactly how `reply_ledger` earned its columns. *(Fixes "schema built ahead of reps": no guessed clip columns, no clip-boundary-as-identity bug.)*
Edit-capture rule: the founder edits X drafts **in-session before pasting to Typefully** so `final_text` is real; edits made inside Typefully afterward are not captured — accepted blind spot, noted, not pretended away.

### EXTEND: `reply_candidates` / `reply_ledger`
Nullable `video_id` (future YouTube-comment replies attribute to their video). Revoke `predicted_grade` UPDATE (above).

### Views (`security_invoker = on`)
- `decision_calibration` — lever × surface × predicted_grade × avg outcome_score × prediction_hit rate × n, `mode='experiment'` rows only, with a `decided_at`/`created_at` divergence flag.
- `video_lineage` — research_run → video → calendar rows, one row per leaf, orphans visible.
- `agent_heartbeat` — view over `agent_execution_logs` (last success + hours-since per agent). A view, not a table: no dual-write drift.

### Lineage
`research_runs.id → videos.source_research_run_id → content_calendar.video_id`; `decisions.subject_*` points at any node. Roll-up is **one hop only**, and a research run's outcome aggregates **only the video derived from its topic pick**, not the whole week's slate — the other pillars' numbers would contaminate the run's verdict. Replies: honest no-upstream (channel + finder signal is their attribution); the nullable `video_id` is for comment-harvest replies later.

### Migrations (repo convention)
1. **`20260703000001_video_system.sql`** — `videos`, `decisions`, `experiments`, `outcome_checks`; indexes incl. the two partial-unique experiment guards; RLS on; column-scoped grants; views; `reply_ledger` predicted_grade revoke; `reply_candidates.video_id`.
2. **`20260703000002_content_calendar_posts.sql`** — content_calendar ALTERs + full grant re-scope (revoke DELETE, column-scoped UPDATE).
3. *Parked:* `reply_phase2` (distiller tables, at ~25 edits), `shorts` promotion (at 5–10 shorts), `hermes` role v2 (blocked on the secret-key path).

Store: `fleet/asset_store.py` — `create_video()`, `log_decision()` (validates lever, refuses published subjects, requires prediction iff mode='experiment'), `open_experiment()` (runs the collision guards), `open_checks()`, `close_check()`. Keyword args, write-only-non-None, mirrors `reply_store.py`. MCP tools on top so logging is conversational.

---

## 5. How a week runs

### Cron (one dispatcher, not one cron per agent)
Single Railway job: `python -m fleet.dispatch`, `0 13 * * *` daily (~9am ET — candidates fresh for the morning reply session).

`dispatch.py` (stdlib, ~100 lines): for each due job → run in try/except **with a per-job wall-clock timeout and `socket.setdefaulttimeout(30)`** → record heartbeat → continue on failure; `finally:` ping healthchecks.io (success vs /fail URL). *(Correction to the ops draft the critics caught: Railway blocks the next run when the container is **still running**, not when it exits nonzero — so the real discipline is per-job timeouts + always exit, and the external ping catches the hang case.)*

Due-job rules (hardcoded, readable):
- **Daily** — reply finder → judge → drafter (never sends).
- **Daily** — open `outcome_checks` for any video/post newly `published` with no checks yet; analytics sweep once built.
- **Sunday** — research run. **Cutover rule (fixes the double-run):** research stays OFF in the dispatcher behind a flag; delete the standalone `0 15 * * 0` cron and flip the flag in the same deploy; the local run remains the fallback door.
- **No Monday x-batch cron.** `x_run.py` stays a ~1-minute local in-session step — the founder must review the output anyway before Typefully; cloud-scheduling it saves nothing at 2 posts/day.

Per-video jobs (packaging, repurposing) are **sessions, not cron** — they need the founder's inputs.

### Founder gates (explicit wait-states; NULL is itself the alarm)

| Gate | State the system waits in | Founder action |
|---|---|---|
| Digest verdict | `research_runs.outcome_verdict IS NULL` | verdict at Sunday close |
| Content-pull pick | `content_calendar.status='idea'` | → `picked` |
| Build-or-content | `product_ideas.build_stage` | sets stage |
| Reply review | `reply_candidates.status='drafted'` | review CLI → rewrite → grade → hand-send |
| Packaging approval | `videos.status='packaged_draft'` | → `packaging_approved`, uploads by hand |
| Publish confirm | `published_at` + `youtube_video_id` NULL | **the one required manual entry — arms the outcome clock** |
| X batch | `content_calendar.status='draft'` | edit in-session → Typefully → `scheduled` |
| Weekly close | overdue `outcome_checks` | closes + L1 verdicts, Sunday |

### The week
- **Sun 13:00 UTC (cron)** — dispatcher: research → digest + content pull; heartbeat + healthchecks ping.
- **Sun eve (~45 min)** — close + open: status banner → close due 7d/28d checks (YT Studio numbers + hand-entered X 7d numbers) → L1 verdicts → conclude/continue experiments → read digest, verdict → pick 3 topics (`videos` → `picked`) → build gate.
- **Mon** — news video (perishable, ships first): script → record → silence-cut → transcribe → **deck** → packaging session (log `decisions` rows, predictions before upload) → approve → upload → set `published_at`. X batch: run `x_run.py` locally, edit in-session, Typefully.
- **Tue–Sat (daily, 12–17 min)** — reply CLI opens with the **morning banner** (heartbeat ages, drafted count, overdue checks, zero-streak warnings): triage → rewrite → guardrail → grade → hand-send. **Plus ~2 min:** close any due 24h checks from the banner (two numbers from the Studio app) — this is how 24h windows survive before the analytics agent exists; if it's ever skipped, the check goes overdue and nags, or gets honestly marked `skipped` rather than silently rotting.
- **Wed / Thu–Fri** — feature video + Ascent video, same ritual. Repurposing session per published video: 1–2 shorts cut by hand, logged as calendar rows + `clip_selection` decisions.
- **Any failure day** — missed healthchecks ping emails him; banner shows which agent is stale; every agent has a local door; the week degrades to the manual ritual, never to silence.

### Founder-hours budget (tracked artifact — re-measure after each agent lands)
Replies 6×15m ≈ 1.5h (the moat; unchanged by design). Videos 3 × (script+record+edit+package+log) + 1 deck ≈ **6.5–7.5h** (logging adds ~10 min/video; the deck is real and was undercounted before). X lane ~30m. Sunday close ~45m. Daily 24h glances ~10m/wk. **Now ≈ 10.5–11.5h/wk → after packager + repurposer + analytics ≈ 8h/wk.** The residual is recording — the founder is the face; it cannot be automated. **If hours run tight, the correct lever is fewer videos, not more automation.**

---

## 6. Analytics & outcome windows

**The analytics agent is the outcome-writer: it owns no levers, closes rows others opened.** Until it exists, the founder + the banner + the Sunday close ARE the analytics agent — and those manual closes are its manual reps, which is how "built last" and "schema day 1" coexist.

### Windows (opened by the dispatcher at publish; fixed, not Sunday-relative)
| Entity | Windows | Closed by (now → later) |
|---|---|---|
| Long video | 24h, 7d, 28d | founder banner/close → YT Analytics API |
| Short (calendar row) | 7d | founder → platform API where it exists |
| X post | 7d (fixed per-post — fixes the Wednesday-vs-Saturday drift) | hand-entered, `source='manual'`, forever (API paywalled) |
| Reply | 7d single check (op_replied, thread alive) | founder in review CLI |
| Research run | Sunday verdict + derived-video 7d roll-up | founder; agent computes roll-up later |
| T&C experiment | when YouTube resolves (weeks on a small channel) | **manual screenshot forever** — T&C results are not in the API |

### Per-lever outcome mapping (fixes the smeared-outcome confound — the top critic issue in all four designs)
A video-level metric written onto four lever rows identifies nothing. Each lever closes against **its own** metric, recorded in `decisions.outcome_basis`:

| Lever | Identifying metric | Honesty note |
|---|---|---|
| `thumbnail_composition` | T&C per-arm watch-time share | the only truly isolated packaging signal |
| `title_wording` | 7d CTR vs trailing pillar baseline | observational; flagged `confounded_by_tc` if a T&C ran in-window; real learning is sequential within-pillar and slow (~1 obs/pillar/week) |
| `description_seo` | search-sourced impressions at 28d (traffic-source breakdown) | API-only; NULL until OAuth |
| `upload_time` | first-24h views vs trailing pillar median | frozen for first 4 weeks anyway |
| `hook` (X) | impressions at 7d | manual, low trust |
| `clip_selection` | short views at 7d | per-short, reasonably clean |
| `reply_voice` | edit_ratio (leading) + op_replied 7d (lagging) | edit_ratio is the real signal |
| `topic_selection` | derived video 7d views vs pillar baseline | one hop only |

Rows with no identifying signal get `outcome_basis='none'` and are **excluded from calibration** — "[no signal]" is an explicit value, not a NULL that looks like laziness. This is also the measurability-bias defense: L1 founder verdicts are first-class on every surface, so the loop doesn't silently optimize only what YouTube can measure.

### Build note on OAuth (de-risked early, critic-flagged)
A GCP app in *testing* status expires refresh tokens every 7 days — the app must be pushed to production status or the founder re-authorizes weekly forever. This is a ~2-hour spike scheduled around day 30, decoupled from the agent build.

---

## 7. Experiment discipline

**One active experiment globally** (DB-enforced, §4) **plus at most one thumbnail Test & Compare** (allowed concurrently because T&C is within-video randomized — the locked "decouples thumbnail from title" mechanism). Title and thumbnail experiments may never overlap (shared CTR numerator; store-guarded).

How a hypothesis lives and dies:
1. **Born** at the Sunday close, usually from `decision_calibration` (a lever whose "strong" predictions keep landing "off" is miscalibrated → next candidate) or from a recipe hunch. Row opened with `hypothesis`, falsifiable `prediction`, `method`, `scope` (stratify by pillar — pillar baselines differ more than most lever effects), `min_n`.
2. **Lives** across units: each affected video/post logs its `decisions` row with `mode='experiment'`, `experiment_id`, `ab_variant`. One experiment row spans min_n observations — never one experiment per video.
3. **Observed** as checks close; T&C verdicts are watch-time share from the screenshot, not CTR.
4. **Dies** one of three ways: `concluded` at `min_n` with `result_verdict` (supported | refuted | inconclusive); `abandoned` explicitly; or **timeboxed** — any experiment still unresolved at 6 weeks concludes `inconclusive` so a stalled low-impression T&C can't camp on the active slot forever *(fixes the small-channel pileup the critics flagged)*.
5. **Leaves a body:** `learned_rule` text → the founder hand-pastes it into the versioned recipe markdown and bumps the version (`applied_to_recipe` records where). Machine proposes; human edits canon. Future `decisions` rows carry the new `recipe_version`, so outcomes stay interpretable across recipe changes.

Volume honesty: 3 videos/wk across 4 pillars = ≤1 within-pillar observation per week. Title/upload-time learning is measured in **months**. The system's job in Q3 is accumulating clean, labeled observations — not concluding fast. Experiment #1: `thumbnail_composition` T&C spanning the next 3 news-pillar videos, hypothesis from the Batch 3 tuning notes, min_n=3, timebox 6 weeks.

---

## 8. Build order

Ladder for every agent (locked): **manual reps → captured rulebook → agent applies the stable recipe → optimizer layer last.**

### This week (reps + rails, ~4–5h of build)
1. **Migrations 1–2 + `asset_store.py` + smoke test** (write-once round-trip assertions, per the existing pattern). Backfill V1–V3 as `legacy`.
2. **Reply cron door → dispatcher on Railway** (per-job timeouts, clean-exit discipline, healthchecks.io wired **before** this becomes cron #2). This immediately **tests the Reddit-RSS-from-Railway-IP risk** with the local run standing as first-class fallback; the zero-candidate-streak detector makes a block fail loudly.
3. **Packaging reps, all 3 videos:** `decisions` rows logged conversationally at the publish ritual — alternatives mandatory, predictions only where a real hypothesis exists (~2 min/video ceiling or reps die). Open experiment #1 (thumbnail T&C, above). Freeze `upload_time` + description format for 4 weeks.
4. **X replies, 3–5 hand-found**, pushed through the existing judge/drafter/gate with `platform="x"` (only the scanner is stubbed). Proves the channel seam, starts the X voice corpus, and writes the future scanner's rulebook from what the founder actually picks.
5. **Reddit replies at the honest pace (2–3/day sustained beats 5/day for a week)** — distiller gate (~25 edits) lands in ~4–8 weeks, not 2–3; that's fine.
6. **Sunday:** first banner-driven close against `outcome_checks`.

### Day 0–30 (doors + wiring, no new intelligence)
- Wire `x_run.py` → Supabase (read `research_runs`, write `content_calendar` with `draft_text`; kills the SQLite coupling). Local step, no cron.
- Shorts reps: 1–2/wk by hand from the news video (`.srt` corpus exists; Opus Clip end-only), logged as calendar rows + `clip_selection` decisions.
- **YouTube OAuth spike (~2h): GCP app to production status, read-only analytics scope, token refresh proven on Railway** — de-risks A6 weeks before its slot.
- Research cron folded into dispatcher (cutover rule in §5).

### Day 30–60 (close the loop)
- **Analytics agent v0**: daily sweep closes YT-sourced checks via `outcome_checks`, computes `prediction_hit`, writes per-lever `outcome_basis` per the §6 mapping; X/T&C stay manual. *Sequencing note: this is "built last" among the loop-closing infrastructure — its manual reps are the 4–6 Sunday closes already done, and outcome starvation is the bigger risk than building it late; the deciding agents below still come after their own reps.*
- **Reply distiller (Phase 2)** the week the ledger crosses ~25 edits.
- X finder scanner **only after** ~15–20 hand-fed X reps.

### Day 60–90 (first new deciding agents, on stable rulebooks)
- **Packager v1 — titles + descriptions only** (~15–20 logged reps with closed outcomes by then; thumbnails stay manual — recipe still tuning per-batch, founder's face + taste). Proposes 3 titles + description; founder picks/edits; diff captured like reply drafts.
- **Repurposer — only if** 15–20 manual shorts show a stable recipe; otherwise automate just the mechanical parts (crop/caption from `.srt`). Promote the `shorts` table when the shape is earned.
- Research-prompt optimizer (obs threshold met by now).

### Do-NOT-build-yet (as load-bearing as the build list)
| Item | Why not |
|---|---|
| Shorts table / auto-clipper | zero reps → guessed columns get sticky under write-once grants |
| X finder scanner (before ~15 hand reps) | the finder's value is encoding what the founder picks — data doesn't exist yet |
| Thumbnail-generation agent | recipe tuning per-batch; founder's face; T&C gives signal without automation |
| Monday x-batch cron | automates a 1-minute local step the founder must review anyway |
| X analytics API | paywalled; hand-entry at these volumes is minutes |
| Funnel / newsletter / ESP | attribution nearly blind; sequence after analytics exists |
| Dashboards | banner + views over real rows first; dashboards over empty tables are procrastination |
| Auto-tuning prompts / self-updating style guide | optimizer-last rule; canon is human-edit-only |
| Orchestrator | locked no |

---

## 9. Risks & open questions

### Risks
1. **Reddit RSS blocked from Railway's IP (unverified)** — the daily heartbeat of the flywheel. *Tested in week 1 by the dispatcher itself; zero-streak detector fails loudly; local run stays first-class; worst case the finder is local-manual (5 min/day) while everything else runs cloud.*
2. **The outcome loop never closes** — write-only ledgers kill the whole learning premise. *`outcome_checks` overdue rows nag in the daily banner; 24h entry is a budgeted 2-min daily line; OAuth spike at day ~30 not day 90; close-coverage is itself a tracked metric.*
3. **Silent cron death** — a hung dispatcher blocks every agent at once (concentration cost). *Per-job timeouts + socket default timeout + clean-exit discipline + external healthchecks ping (wired before cron #2) + per-agent heartbeat max-ages.*
4. **Anon-key ledger poisoning (H1)** — `research_runs` history is still fully overwritable; every new moat table ships column-scoped from day 1, `predicted_grade` leak closed, but the real fix is the parked `hermes` role. *Weekly pg_dump until then; prioritize the secret-key path before day 60.*
5. **Logging tax kills the reps** — >2 min/video and it silently stops, starving every future agent. *Claude-in-session is the scribe; alternatives = one sentence; predictions only where real; if a lever's rows stop appearing, drop the lever, don't let the habit die.*
6. **Small-channel signal starvation** — a June-2026 channel: T&C takes weeks to resolve, 6 graded title outcomes are noise, and the calibration view will look meaningful before it is. *min_n enforced before verdicts; timeboxed experiments; per-lever outcome_basis excludes unidentified rows; patience is a stated feature.*
7. **Automating the wrong bottleneck** — agents save ~2–3h/wk but recording 3 videos is the real cost and it can't be automated. *The hours budget is a tracked artifact; when hours run tight the answer is fewer videos, not more agents.*

### Open questions (founder calls)
1. **Video volume:** does 3 long videos/wk survive the honest 10.5–11.5h budget, or is 2/wk + better packaging the stronger play for the next quarter?
2. **Logging ceiling:** is ~10 min/video of decision logging acceptable as a permanent ritual cost, or should routine (`mode='routine'`) rows be trimmed to title + thumbnail only?
3. **CTR-lever precedence:** when a title hypothesis feels urgent while a thumbnail T&C is active, which one waits? (System enforces that one must.)
4. **OAuth account:** which Google account owns the GCP project pushed to production status (channel account vs the automation Gmail)?
5. **24h windows pre-analytics-agent:** commit to the 2-min daily banner entry, or explicitly `skip` 24h checks until the agent exists and accept 7d-only calibration for the first month?
6. **IDEAS_LOG.md retirement:** when the `videos` table has a quarter of history, does the md die or stay as prose commentary?
