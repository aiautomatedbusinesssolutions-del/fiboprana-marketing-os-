# Fiboprana Marketing OS

Internal marketing system for **Fiboprana** — the mind-state layer on top of the wearables you already own. This repo is the *engine*: research radar, Reddit listening + reply pipeline, content generators, attribution, and the agent fleet. The *production* side (faceless video chain, live landing page + Resend funnel, brand docs) lives in the sibling repo **`../Fiboprana Marketing/`**.

> Provenance: cloned 2026-08-26 from the proven marketing engine built for the founder's other business, with all business content swapped to Fiboprana. Business facts live in `wiki/` (the fleet's open-book exam) — prompts interpolate wiki pages at runtime, so rebranding = editing the wiki, not the code.

## Quick start

- **Double-click `Start Fiboprana Marketing.bat`** — starts the main dashboard at http://localhost:5000 (localhost only, debug on).
- **Double-click `Start Fleet Dashboard.bat`** — the fleet mission-control pages at http://127.0.0.1:8765.
- Manual: `python app.py` (add `--network` for phone access on home WiFi; disables debug).
- Stop with `Ctrl+C` or close the window.

## First-time setup

```powershell
# from the project root
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
cp .env.example .env   # then fill in keys
```

Minimum keys to be useful: `OPENROUTER_API_KEY` (or `ANTHROPIC_API_KEY` as the direct fallback — one key enables every AI feature; see `fleet/llm.py`). Add per-module keys as you switch modules on: `EXA_API_KEY` (radar/sourcing), `REDDIT_*` (sourcing + outcome checks), `APIFY_TOKEN` (reply finder), Supabase keys (fleet ledger). All documented in `.env.example`.

If venv activation is blocked: `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned` once, then retry.

## The shape of the system

| Piece | What it is |
|---|---|
| `wiki/` | **The business wiki** — brand, market, product, compliance, channels. Agents load pages by id at runtime (`fleet/wiki.py`). Edit here to change what every agent believes. |
| `app.py` + `templates/` | Flask dashboard (port 5000): capture, observations, sourcing, radar, ideas, synthesis, swipe, attribution, content generators. |
| `fleet/` | The agent fleet: reply finder → judge → drafter pipeline, research agent, distiller, metrics, outcome checker + `fleet/dispatch.py` (the cron entrypoint) and the fleet dashboard (port 8765). Nothing ever auto-posts (`fleet/publish.py` raises by design). |
| `radar/`, `reply_finder/`, `sourcing/`, `sourcing_web/` | Scanners. Each has a `config.yaml` (wellness-niche sources, subreddits, queries) reloaded per run. |
| `content/` | Per-platform generators (X, TikTok, LinkedIn, shorts, video scripts, the "Notice" email) with the wellness-line guardrails check. |
| `attribution/` | Short-link minting + click logging. Public `/r/<code>` redirect ships in `fiboprana-site` (TODO). |
| `supabase/migrations/` | The `marketing` schema for the fleet ledger — one shared Fiboprana Supabase project, agents scoped to this schema. See `SUPABASE_SETUP.md`. |
| `videos/` | Footage utilities (silence-cut, transcribe). Mostly idle here — Fiboprana's video production is the faceless chain in the sibling repo (see `wiki/channels/video-kit.md`). |

## The operating docs

- **`WORKFLOW.md`** — the lean weekly loop as actually run by hand with Claude in the repo.
- **`WEEKLY_RUNBOOK.md`** — the fuller dashboard-automated version of the same machine.
- **`MARKETING_OS.md`** — the fleet's architecture doctrine (ledger, levers, learning loops).
- **`SUPABASE_SETUP.md`** — standing up the database.

## Security notes

- `--network` mode has no auth: trusted home WiFi only.
- `.env` is git-ignored; never commit it. `.env.example` carries names only.
- Fleet DB access should use the scoped `hermes` role (migration `20260625000001`, runbook `fleet/SECURITY.md`) — marketing agents never hold product-data credentials.
