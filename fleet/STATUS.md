# Fleet Agent #1 (research) — Status & Next Steps

> **Inherited history (2026-08-26):** this journal was carried over from the source
> engine project. Entries below describe THAT repo's rollout — paths, accounts, MCP
> names, and Supabase project refer to the engine, not Fiboprana. Kept because the
> operational lessons transfer; new Fiboprana entries go on top.

Last worked: 2026-07-25 (metrics agent built — daily raw-numbers snapshots).

## 2026-07-25 update — metrics agent (daily snapshot collector)
- **Audit finding:** the outcome checker fetched the WHOLE account's Typefully
  analytics and discarded all but the matched reply rows; no account-level
  history existed anywhere. Snapshots can't be backfilled — so now they're kept.
- **New:** `supabase/migrations/20260725000001_metric_snapshots.sql`
  (`metric_snapshots` — one row per entity per UTC day, raw API payload
  verbatim, append-only across days, daily-merge idempotent) +
  `fleet/metrics_agent.py` (no LLM: every X post/reply incl. the never-before
  captured `link_clicks`, an x_account rollup, reddit karma + comment scores
  once creds return) wired into `fleet/dispatch.py` daily after the checks
  sweep. It also closes due reply/post `outcome_checks` windows by exact
  status-id / draft-id match, so a 7d window holds day-7 numbers.
- **Fix:** `outcome_checker_agent._close_queue_checks` now closes DUE windows
  only (it could previously close a 7d check with day-2 numbers).
- **Verified:** dry run against live APIs — 75 snapshot rows, 3/12 due windows
  matched (the other 9 are reddit replies, unreadable until the account is
  back — they correctly stay open). Typefully has NO follower-count endpoint
  (probed); the x_account rollup documents that gap instead of faking it.
- **APPLY ORDER:** paste the migration in Supabase Studio BEFORE deploying this
  code to Railway — the daily metrics job fails a run (heartbeat + healthchecks
  noise, no data loss) if the table isn't there yet.

## 2026-07-02 update — Marketing OS spine LIVE
- **System-level architecture landed:** `MARKETING_OS.md` (repo root) — the map all
  agents now build against (agent roster, learning contract, schema, cron + gates,
  build order). Designed via a 16-agent adversarial workflow; founder decisions
  baked in (3 videos/wk, thumbnails own the CTR experiment slot, 24h checks from
  day 1, log-more-at-the-start).
- **Migrations applied + verified 2026-07-02:** `20260703000001_video_system.sql`
  (videos / decisions / experiments / outcome_checks + views + predicted_grade
  revoke) and `20260703000002_content_calendar_posts.sql` (posts ledger + closes
  the stub's DELETE hole). `python -m fleet.asset_check --write` ALL GREEN:
  write-once moat, one-active-experiment indexes, refuse-if-published funnel.
- **New modules:** `fleet/levers.py` (lever vocabulary + identifying metrics),
  `fleet/asset_store.py` (the spine API), `fleet/asset_check.py` (smoke test),
  `fleet/dispatch.py` (the ONE daily cron: reply pipeline + checks sweep +
  research behind `DISPATCH_RESEARCH` flag) + `railway.dispatch.json`
  (daily `0 13 * * *`). Env additions in `.env.example`: `HEALTHCHECKS_URL`,
  `DISPATCH_RESEARCH`.
- **Videos backfilled** from `videos/IDEAS_LOG.md` (now retired, read-only): 9 rows,
  pre-OS tagged `legacy`. The `videos` table is the dedup memory now.
- **NEXT:** (1) commit + push; (2) create the SECOND Railway service on this repo
  (config path `railway.dispatch.json`, same 4 env vars + `HEALTHCHECKS_URL` from a
  free healthchecks.io check) — its first run doubles as the Reddit-from-datacenter-IP
  test; (3) daily reply reps via `REPLY_RUNBOOK.md` + first hand-fed X reps;
  (4) packaging `decisions` rows on the next publish + open experiment #1
  (thumbnail T&C spanning the next 3 news videos).

## 2026-07-02 update (earlier)
- **First autonomous Railway run SUCCEEDED** Sun 2026-06-28 15:00 UTC (1m 36s, green in
  Cron Runs). Still to do: confirm the `research_runs` row + heartbeat locally
  (`python -m fleet.check`) and give the run a verdict via `log_outcome`.
- **Railway detour resolved:** an externally-sourced "Hermes gateway" prompt (Gemini)
  led to cancelling/partially moving the cron; the service was restored same day. The
  cron schedule (`0 15 * * 0`), start command, and the 4 env vars were verified intact.
  Do NOT adopt the gateway/session-key blueprint — the agent's learning loop lives in
  the Supabase ledger and is trigger-agnostic (Railway cron vs an MCP client makes no
  difference). This commit also serves to redeploy the service onto latest `main`
  (an earlier dashboard redeploy had pinned an old pre-`8a6706e` image).

## Where you are
Agent #1 (the weekly **research** agent) is **built, tuned, committed, and live in an MCP client.**

- **Phase 1 — Database online ✅** Supabase `marketing` schema applied + exposed; `python -m fleet.check --write` passed.
- **Phase 2 — Agent validated ✅** `python -m fleet.research_run` produced a real digest + content pull. First run is in the ledger (`research_runs`, run_id `7e0eec47-5e26-44db-b282-7e26561f8bb1`) and seeded with a **strong** verdict. The content-pull prompt was at **v1.2** then (searchable feature_seed, no em-dashes); it has since been tuned to **v1.4** (untrusted-data framing + the advice-line tightening).
- **Phase 3 — an MCP client wired ✅** `mcp_servers.stackivate-research` added to `%LOCALAPPDATA%\hermes\config.yaml` (backup at `config.yaml.stackivate-bak`). Verified: Hermes sees `run_research` + `log_outcome`.
- **Phase 4 — Cloud cron ✅ DEPLOYED (Railway; first run Sun 2026-06-28 15:00 UTC).** Code is cloud-ready and pushed: Supabase-sourced digest dedup (the prior-digest TEXT is read from Supabase and fed to the model as a "do not repeat" block, so that part survives the cloud's fresh filesystem — a SOFT dedup, not a hard guarantee; the local item-level cutoff resets every cloud run, so cross-week dedup in the cloud rests on the model honoring that block) + a `Dockerfile`. Deployment switched Render → **Railway** (cheaper for a small weekly job, 2026-06-25); `render.yaml` is kept as a fallback. **Deployed 2026-06-26:** `railway.json` committed (dbc3168), GitHub repo connected, the 4 env vars set in the dashboard, and the cron scheduled for Sunday 15:00 UTC — Railway's new "Cron Runs" view confirms "next run Sunday 3 PM UTC." A green build/schedule means the deploy succeeded; the first **autonomous** run fires **Sun 2026-06-28**, and env-var correctness is unproven until that run writes its row.

## ▶ NEXT SESSION — start here (on/after Sun 2026-06-28)

**1. Confirm the first Railway cron run (Phase 4).** Deployed 2026-06-26 — `railway.json`
committed (dbc3168), repo connected, cron scheduled Sunday 15:00 UTC (= `0 15 * * 0`),
the **4 env vars** set in the dashboard (`SUPABASE_URL`, `SUPABASE_ANON_KEY`,
`ANTHROPIC_API_KEY`, `EXA_API_KEY`; **NOT** `SUPABASE_HERMES_JWT` — see the Parked note).
After the first run fires **Sun 2026-06-28 15:00 UTC**:
   - Confirm a new `research_runs` row **and** a fresh heartbeat in Supabase
     (`python -m fleet.check` and `python -m fleet.research_run --check-heartbeat`).
     This is the first proof the env vars are correct — a typo surfaces here, not before.
   - Give the run a verdict via `log_outcome` (the learning signal the next run reads).
   - With 2+ agents later, deploy a **dispatcher cron** (one cron loops all due
     agents from the ledger) instead of a cron each — AGENT_PLAYBOOK principle 12.
   - Wire an external monitor to `python -m fleet.research_run --check-heartbeat`
     (the dead-man's switch; exits 1 if no successful run in 8 days).

**2. Task #6 — doc-consistency audit ✅ DONE 2026-06-25.** Reworded the cron-dedup
claims (STATUS + AGENT_PLAYBOOK) to be explicit that cloud dedup is the model's SOFT
"previously reported" block, not the local item cutoff (which resets on the cloud's
ephemeral FS). README had no dedup claim — clean. Reconciled the 2026-06-25 docs:
SECURITY.md now leads with the asymmetric-key path (the HS256 `mint_hermes_token`
flow is demoted to a "legacy projects only" note, since it's a dead end here), prompt
ref bumped v1.3 → v1.4, and Render → Railway throughout. `mint_hermes_token.py`
carries a "not usable on this project" header. (Fiboprana note: canonical brand/compliance docs now live in
`../Fiboprana Marketing/` and this repo's `wiki/` — the Finance-Hub
cross-linking item was engine-repo housekeeping and doesn't apply here.)

### Parked / NOT blocking — H1 scoped key
The `SUPABASE_HERMES_JWT` path is a **dead end on this project**: it uses Supabase's
**modern asymmetric keys**, so a self-signed `hermes` JWT can't be minted — confirmed
2026-06-25 by a live 401 (`PGRST301: None of the keys was able to decode the JWT`). That
broken token had been breaking every DB call, so it was **commented out of local `.env`**
and the agent is back to working on the anon key. `mint_hermes_token.py` is therefore
**not usable here**. If/when we want H1 closed, use the correct method for asymmetric keys
(a direct scoped Postgres connection, or a Supabase **secret key** bound to a custom
`hermes` role) — verify against current Supabase docs before touching prod. The agent runs
fine on anon meanwhile; this was always optional.

## Good to remember
- **Hosting decision (2026-06-25 multi-provider research):** Railway Hobby (~$5/mo)
  is the validated pick. For tiny weekly crons cost is a non-differentiator (under
  ~$15/mo even at 100 agents) and lock-in is low (plain Dockerfile, Supabase-
  decoupled), so "pick Railway now, switch later" is safe. Use one **dispatcher
  cron** as agents pile up; re-aim the >$100 "go local" rule at always-on/heavy
  agents, NOT agent count. Avoid Modal + AWS-Lambda's container path (they bake
  provider code into the image). `research_run` now writes a **heartbeat** each run
  — read it with `--check-heartbeat`. Full reasoning: AGENT_PLAYBOOK principle 12.
- **`run_research` re-run locally this week** returns "no new items" (the local item cutoff already covers them) — correct; fresh digests come next week. This is LOCAL behavior: the cloud cron scans into a fresh DB each run, so it always sees items and relies on the model's "previously reported" block (a soft dedup) to avoid repeats.
- **Each week:** give the run a verdict + note via `log_outcome` (strong / ok / off). That's the learning signal the next run reads.
- **Two doors, one brain:** an MCP client (interactive) and the cloud cron (Railway, autonomous) both run the same `run_research`. Local `python -m fleet.research_run` still works too.
- **Undo the Hermes wiring** if ever needed: the engine machine's entry was `stackivate-research`; this clone's MCP server registers as `fiboprana-research` (`hermes mcp remove fiboprana-research` to unwire).

## Building the next agent
Follow **`fleet/AGENT_PLAYBOOK.md`** — one job at a time; an orchestrator only once 2–3 workers exist; the analytics/feedback loop comes last.

## Security (read before the cloud cron handles real data)
A multi-agent review (2026-06-25) cleared the foundation: no Critical/blocking
issues. Applied immediately: `urlopen` timeout, cron exit-code on partial runs,
guarded Supabase calls + scan, and untrusted-data framing on the content pull.

One known **interim exposure (H1)**: the agent shares the public anon key, so that
key can read/overwrite the `marketing` ledger. It does **not** block deployment and the
agent runs fine without closing it. NOTE: the staged `mint_hermes_token.py` path does
**not** work on this project (modern asymmetric Supabase keys — see the Parked note
above). The scoped-role migration (`20260625000001_scope_hermes_role.sql`) still applies;
only the credential-minting method changes. Runbook: `fleet/SECURITY.md`.

## Reference docs
- `fleet/SECURITY.md` — the H1 exposure + the scoped-role hardening (apply steps)
- `fleet/setup.html` — visual click-through runbook (saves progress in your browser)
- `fleet/AGENT_PLAYBOOK.md` — repeatable recipe for new agents
- `fleet/README.md` — the Supabase data layer
