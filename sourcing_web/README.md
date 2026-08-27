# sourcing_web/

Exa-powered web search for finding audience-fit content. v1 hunts Reddit
threads from POSITIONING's secondary audience: 10-20yr self-directed
investors refining their practice (Bogleheads, FIRE, ChubbyFire, leanFire).

This module runs **parallel** to `sourcing/` (the PRAW Reddit scanner). They
have separate SQLite files, separate routes, separate templates. The Reddit
module remains the canonical reference; this module mirrors its layout and
triage flow exactly so they're swappable later.

## Files

- `exa_client.py` — Exa client factory; raises `SourcingWebConfigError` if
  `EXA_API_KEY` is missing.
- `scanner.py` — `scan_with_templates(...)` and `scan_with_query(...)` —
  the two entry points called from `app.py`.
- `extractor.py` — `fetch_content(url)` calls Exa `/contents` to pull a
  Reddit thread's text for prefilling `/observations/new`.
- `db.py`, `schema.sql` — SQLite connection + idempotent schema for the
  `web_results` table. DB lives at `sourcing_web/seen.db` (gitignored via
  the project's `*.db` rule).
- `config.py`, `config.yaml` — fresh-read YAML config with search params and
  query templates. Edit `config.yaml` and changes take effect on the next
  scan; no restart needed.

## Triage flow

Same as `sourcing/`: scan populates the `new` queue; user clicks Skip /
Save for later / Extract per row. Extract calls Exa `/contents`, formats as
markdown, and redirects to `/observations/new` with prefilled query params
(same handoff pattern as `sourcing/<reddit_id>/extract`).

## Exa quota awareness

Each **Scan all templates** makes one API call per template (5 calls with
the v1 config), plus one content-extraction call per **Extract** action.
Monitor usage at https://dashboard.exa.ai. If you're approaching quota,
tune `search.num_results` (default 8) or trim the template list in
`config.yaml`.

## Reddit content via Exa vs PRAW

Exa's `/contents` returns the page text but may not include a flattened
comment tree the way `sourcing/scanner.py:fetch_thread_markdown` does via
PRAW. v1 accepts thinner content; if it's a problem, options are:

1. Bump the `max_characters` arg in `extractor.fetch_content`
2. Force a livecrawl by setting `search.max_age_hours: 0` in `config.yaml`
3. Once Reddit API approval lands, swap the extractor to PRAW for
   `reddit.com` URLs (the `subreddit` column is parsed from the URL on
   insert, so this is a one-function change)
