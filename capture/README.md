# capture/

Log conversations with potential Fiboprana users (from Reddit, Discord, in-person chats, etc.) into a local SQLite database so patterns can be surfaced later by the `synthesis/` module. v1 is intentionally minimal — the schema will evolve as we learn which fields actually earn their keep.

> **Web UI is now the primary interface.** Run `python app.py` from the project root and open http://localhost:5000/capture. The CLI scripts below still work and share the same `conversations.db`; they're handy for quick terminal ops but the web UI is where day-to-day logging happens. See the top-level [`README.md`](../README.md) for web setup and phone-access instructions.

> A sibling module at [`../sourcing/`](../sourcing) scans Reddit for candidate threads. Extracted threads feed into `/observations/new` via query-param pre-fill — there is no direct integration with `capture/`'s conversations table.

## Layout

```
capture/
├── schema.sql            # table definition, version-controlled
├── db.py                 # shared connection + schema helpers
├── add_conversation.py   # interactive CLI to add one entry
├── view_conversations.py # browse + filter
├── export.py             # dump DB to markdown + CSV
├── conversations.db      # created on first run; git-ignored
└── exports/              # created on first export; git-ignored
```

Python stdlib only — no `pip install`, no virtualenv.

## First-time setup

There is no explicit setup. The first time you run `add_conversation.py` (or any script), it reads `schema.sql` and creates `conversations.db` automatically.

```
cd capture
python add_conversation.py
```

## Scripts

### `add_conversation.py`

Interactive CLI. Prompts through every field one at a time. Blank is allowed for any field except `date` (which defaults to today — just hit ENTER).

- **Single-line fields** (source, handle, segment, etc.) show a hint in brackets; type anything.
- **Multi-line fields** (stated_goal, pain_points, quotes, feature_implications, feedback_on_app, notes) accept lines one at a time; hit ENTER on a blank line to finish that field.
- **Tags** are comma-separated and lowercased on save.

After all prompts, a summary is printed and you confirm with `Y/n` before the row is inserted.

```
python add_conversation.py
```

### `view_conversations.py`

Read-only browse with optional filters. Default (no args) shows the most recent 20 entries, newest first. Long free-text fields are truncated to ~200 chars — pass `--full` or `--id N` to see everything.

| Flag | What it does |
|------|--------------|
| `--tag TAG` | substring match on the tags column |
| `--source SOURCE` | exact match on source |
| `--segment SEGMENT` | exact match on segment |
| `--from YYYY-MM-DD` | entries on or after this date |
| `--to YYYY-MM-DD` | entries on or before this date |
| `--limit N` | change the row cap (default 20) |
| `--id N` | show a single entry in full |
| `--full` | don't truncate long fields |

```
python view_conversations.py --tag broker-question
python view_conversations.py --from 2026-04-01 --to 2026-04-30 --source reddit
python view_conversations.py --id 7
```

### `export.py`

Writes timestamped snapshots of the whole DB for review / sharing:

```
capture/exports/conversations_2026-04-20.md
capture/exports/conversations_2026-04-20.csv
```

Re-running the same day overwrites that day's files. Pass `--out-dir PATH` to write elsewhere.

```
python export.py
python export.py --out-dir ../review-packets
```

## Tagging conventions

Tags are free-form on both tables (`conversations.tags` and `observations.tags`) — store whatever is useful — but a few labels have a specific meaning worth standardizing so queries stay consistent over time.

- `product-insight` — observation where the AI-extracted feature implications contain at least one concrete, directly-actionable change for the Fiboprana app itself (not just marketing content). Use sparingly — reserve for clear product opportunities, not every observation.
- `sensitive-context` — source thread contains personal/sensitive context (mental health, caregiving, family illness, major life hardship). Tag so these observations can be filtered out when searching for content ideas later.

New reserved tags should be added here when introduced, with a one-line definition. Topical tags (e.g., `roth-ira`, `index-funds`) don't need to be listed — only pattern/meta tags that carry a shared meaning across entries.

## Evolving the schema

`schema.sql` uses `CREATE TABLE IF NOT EXISTS`, which means adding columns there **will not update an existing DB**. When you want to change the schema, the simplest paths are:

1. **Early (few rows, nothing precious):** delete `conversations.db` and re-run any script — it will be recreated from the updated `schema.sql`. Export first (`python export.py`) if you want a record.
2. **Later (real data you care about):** write a short migration script in this folder (e.g., `migrate_2026_05_add_channel.py`) that opens the DB and runs `ALTER TABLE`. Commit both `schema.sql` and the migration.

When a migration becomes the second or third one, consider promoting to a simple `migrations/` folder with numbered scripts.
