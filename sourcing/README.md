# sourcing/

Reddit scanner that surfaces beginner-investor threads for manual triage into the Observations pipeline. Rule-based scoring (no AI), manual scan trigger (no scheduling), Reddit only.

## Files

```
sourcing/
├── config.yaml        # subreddits, trigger/exclusion phrases, scoring weights
├── config.py          # loads config.yaml
├── reddit_client.py   # PRAW client factory; raises SourcingConfigError on missing creds
├── scanner.py         # scan_subreddits() + fetch_thread_markdown()
├── schema.sql         # seen_posts table (status: new/skipped/saved_later/extracted)
├── db.py              # connection helpers (mirrors capture/db.py)
└── seen.db            # created on first scan; git-ignored
```

## Usage

1. Add Reddit credentials to `.env` (see top-level [`README.md`](../README.md) §Setup step 3).
2. Open <http://localhost:5000/sourcing>.
3. Click **Scan Now**. The scanner reads `config.yaml`, pulls `limit_per_subreddit` recent posts from each listed subreddit, filters by trigger/exclusion phrases, scores, and inserts surviving candidates as `status='new'`.
4. Triage each candidate:
   - **Extract** — fetches the full thread + top-level comments, formats as markdown, redirects to `/observations/new` with `raw_content`, `source_url`, `source`, and `source_detail` pre-filled. Marks the post `extracted`.
   - **Save for later** — moves it to `/sourcing/saved` for a second pass.
   - **Skip** — drops it from the digest (row is retained for dedupe).

## Tuning

Edit `config.yaml` directly; changes take effect on the next scan (no restart). Useful levers:

- `trigger_phrases` — grows as new beginner-investor vocabulary surfaces
- `exclusion_phrases` — prunes noise (crypto, day-trading, etc.)
- `scoring.trigger_match` / `multiple_triggers` — how much weight a matched phrase gets
- `scan.min_score_to_display` — raise to tighten the digest
- `scoring.max_age_hours` — how fresh a post must be to count

## Not included (by design)

Scheduled scanning, Discord / Twitter / other platforms, auto-replies or DMs, AI-based scoring, Conversations-module integration. See [`../DECISIONS.md`](../DECISIONS.md) for the reasoning.
