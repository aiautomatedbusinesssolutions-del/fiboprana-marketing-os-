# Building a fleet agent — the repeatable recipe

> How we build each agent in the Fiboprana marketing fleet. Agent #1 (weekly
> research) was built this way on 2026-06-24; follow the same path for the next one.
> Companion docs: `fleet/README.md` (the data layer), `fleet/setup.html` (agent #1's
> click-through runbook), `WORKFLOW.md` (the weekly process the agents automate).

## The architecture in one picture

```
an MCP client (chat, MCP stdio) ─┐
Cloud cron (weekly, autonomous)  ─┤→  fleet/<job>_agent.py ──→ existing modules
                                  │      (the brain: one fn)     (radar, ...) + Claude
                                  │             │
                                  │             ▼
                                  └────────→ fleet/store.py ──→ Supabase (marketing schema)
                                             (one write API)      shared ledger, all agents
```

- **Runtime:** an MCP client runs and chats the agents via MCP over local stdio. We write the tools; the MCP client executes them.
- **Integration:** the Next.js app and the Python agents decouple at the app layer and meet ONLY through the shared Supabase DB.
- **Home:** every agent is Python in THIS repo. The `fleet/` package holds the shared layer; each agent adds its own brain + prompt.

## Core principles (the rulebook)

1. **One agent = one JOB**, not one per data source. Split only when a job has a genuinely different method, access problem, or judgment.
2. **Store decision + reasoning + outcome**, not just the action. Rows are self-contained training examples (denormalize context onto the row).
3. **Outcomes are updateable later** and stamped (`outcome_checked_at`). Two layers: L1 = your manual verdict (now); L2 = downstream performance (later, via an analytics agent).
4. **One store API** that both you-in-chat and the agent call (`fleet/store.py`). The agent reads its own history to improve.
5. **Two doors, one brain:** the real logic is a plain `run_<job>()` function; the MCP server and the cron entrypoint are thin wrappers (interactive vs autonomous).
6. **stdlib over deps.** The Supabase client is `urllib`, not `supabase-py`. Add a dependency only with a clear reason (e.g. the `mcp` SDK).
7. **Sequence: rulebook → agent → optimization.** Nail the manual recipe and capture it as a prompt first; build the agent to apply it; add the learning/analytics loop LAST.
8. **No premature structure:** no orchestrator until 2–3 workers exist; no multi-provider routing until a job demonstrably needs it.
9. **Prompt-tuning loop:** plan → edit → regenerate → evaluate; don't auto-commit between rounds. Cheap iteration = re-run only the model step against existing inputs (no scan, no ledger write).
10. **Provenance:** the run id is the anchor; content made from a run stores that id + which seed, so performance attributes back.
11. **Security:** isolated `marketing` schema; anon key + RLS (not service-role); ledger tables are append/update-only (no DELETE granted to anon) so history can't be wiped.
12. **One dispatcher cron, not one per agent (cloud).** As the fleet grows, schedule a SINGLE cron that loops over all "due" agents read from the Supabase ledger, instead of a cron service per agent. Keeps cost flat (dodges per-job floors), makes a provider switch recreate one trigger not N, and composes with the heartbeat dead-man's switch. Corollary: apply the ">$100/mo → run it local" rule to *does this agent need to STAY RUNNING / is it heavy?*, NOT to agent count — tiny scheduled jobs never approach $100; always-on services cross it fast. (Hosting decision, 2026-06-25 multi-provider research: **Railway Hobby (~$5/mo)**; lock-in is low because every agent is just the repo Dockerfile's CMD decoupled through Supabase, so "pick now, switch later" is safe. **Avoid Modal and AWS-Lambda's container path** — both bake provider code into the image and make switch-cost grow with the fleet.)

## The build recipe (per agent)

0. **Nail the manual task + write the rulebook.** Do the job by hand a few times; capture the recipe as a cornerstone prompt. (Research = WORKFLOW.md step 2.)
1. **Schema** — new migration `supabase/migrations/NNN_<name>.sql`: a learning-ledger table (id, agent_name, status, `inputs` jsonb, the output columns, `reasoning`, `outcome_*` + `outcome_checked_at`, timestamps). Reuse `agent_execution_logs` for ops. Enable RLS + policies; no DELETE for anon on ledgers. Apply in Studio; add the schema to API → Exposed schemas.
2. **Store API** — functions in `fleet/store.py`: `log_<job>()`, `update_outcome()`, `recent_<job>_for_learning()`. Keyword args; write only non-None fields.
3. **Agent core** — `fleet/<job>_agent.py` with `run_<job>()`: read recent verdicts → do the work (reuse existing modules + one Claude call) → persist via store → return a result dict + `format_for_chat()`. Put the cornerstone prompt in `fleet/<job>_prompt.py` (with a tuning log). Add the model constant to `app.py`.
4. **Two doors** — add the tool to `fleet/mcp_server.py` (interactive) and a `fleet/<job>_run.py` cron entrypoint (autonomous). Both just call `run_<job>()`. The shared `Dockerfile` serves both.
5. **Validate locally** — `python -m fleet.check` (DB), then `python -m fleet.<job>_run`, confirm the row synced in Studio, then tune the prompt (cheap pull-only re-runs).
6. **Wire an MCP client** — config.yaml entry: `command` = the **venv** python (so deps resolve), `cwd` = repo root (so `import app`/`fleet` work), `env` = the secrets. Confirm `python -m fleet.mcp_server` boots first.
7. **Cloud** — GitHub → Railway (chosen 2026-06-25; see principle 12). The weekly run is a **CRON JOB**, not a web service (stdio MCP is on-demand/local). Set env vars in the dashboard; Docker is the isolation boundary. With 2+ agents, run one **dispatcher cron** over the ledger rather than a cron each. Wire an external monitor (uptime cron / healthchecks.io / a monitoring agent) to `python -m fleet.research_run --check-heartbeat` so a silently-failing cron is noticed.
8. **Later** — close the outcome loop (fill `outcome_metrics` from real performance); add the next agent; introduce an orchestrator only once 2–3 workers exist.

## Per-agent file checklist
- [ ] `supabase/migrations/NNN_<name>.sql` (ledger table + RLS)
- [ ] `fleet/store.py` (+ `log_<job>`, `recent_<job>_for_learning`)
- [ ] `fleet/<job>_prompt.py` (cornerstone + tuning log)
- [ ] `fleet/<job>_agent.py` (`run_<job>` + `format_for_chat`)
- [ ] `fleet/<job>_run.py` (cron door)
- [ ] tool added to `fleet/mcp_server.py` (MCP door)
- [ ] model constant in `app.py`
- [ ] tested: check → run → row synced → tuned

## Lessons from agent #1 (gotchas)
- **Expose the schema:** add `marketing` to Supabase → Settings → API → Exposed schemas, or the anon key 404s.
- **DNS fail = bad `SUPABASE_URL`:** a Supabase project ref is exactly 20 chars; a typo gives "getaddrinfo failed." Re-copy, don't retype.
- **Anon key, not service-role.** RLS governs it (policies live in the migration).
- **No em-dashes** in generated copy (brand voice + copy guardrails) — instruct prompts explicitly.
- **`feature_seed` must be a standalone, searchable tool** (its own URL + a `search_phrase`), not an in-app element — that's what the build-or-content gate needs.
- **`content_pull` is an object** (the five WORKFLOW fields), not a list.
- **Windows console:** prefix `PYTHONIOENCODING=utf-8` to see Unicode instead of `�`.
- **MCP client config:** point `command` at the venv python and set `cwd` to the repo root, or imports fail.
- **Cloud cron must exit cleanly:** a one-shot run has to return / exit 0 (close Supabase + HTTP connections) or the platform treats the container as "still running" and silently blocks the next scheduled run. `research_run` already exits cleanly and records a **heartbeat** to `agent_execution_logs` each run (dead-man's switch; read it with `--check-heartbeat`, which exits 1 if no success in 8 days).
- **Don't re-run the full job just to tune:** locally the digest dedupes (the item cutoff persists in the local radar DB), so a second same-week local run reports "no new items." (In the cloud the DB is ephemeral, so dedup there is the model's soft "previously reported" block, not this cutoff.) Tune with a pull-only re-run against the stored digest.
