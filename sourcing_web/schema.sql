-- Exa-surfaced web results awaiting triage. URL is the natural unique key
-- (Exa results don't have a stable id like Reddit's t3_); we use an
-- autoincrement id for routing (URLs aren't safe path components).
-- status drives the triage UI:
--   'new'         -> appears in /sourcing/web digest
--   'skipped'     -> filtered out (kept for dedupe)
--   'saved_later' -> appears in /sourcing/web/saved
--   'extracted'   -> followed through to an /observations/new submission
CREATE TABLE IF NOT EXISTS web_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    url               TEXT NOT NULL UNIQUE,
    title             TEXT,
    highlights        TEXT,             -- JSON-encoded list[str]
    exa_score         REAL,
    published_date    TEXT,             -- ISO8601, nullable
    query             TEXT NOT NULL,
    query_kind        TEXT NOT NULL,    -- 'template' | 'adhoc'
    query_label       TEXT,             -- human-readable when query_kind='template'
    subreddit         TEXT,             -- parsed from URL when reddit.com
    status            TEXT NOT NULL DEFAULT 'new',
    first_seen_at     TEXT NOT NULL DEFAULT (datetime('now')),
    status_changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_web_status    ON web_results(status);
CREATE INDEX IF NOT EXISTS idx_web_subreddit ON web_results(subreddit);
CREATE INDEX IF NOT EXISTS idx_web_query     ON web_results(query);
