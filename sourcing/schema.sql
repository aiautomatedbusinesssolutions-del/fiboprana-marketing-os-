-- Sourcing candidates scanned from Reddit. One row per unique reddit_id
-- (PRAW's id without the t3_ prefix). status drives the triage UI:
--   'new'          -> appears in /sourcing digest
--   'skipped'      -> filtered out (kept for dedupe)
--   'saved_later'  -> appears in /sourcing/saved
--   'extracted'    -> followed through to an /observations/new submission
CREATE TABLE IF NOT EXISTS seen_posts (
    reddit_id         TEXT PRIMARY KEY,
    subreddit         TEXT,
    title             TEXT,
    url               TEXT,
    author            TEXT,
    score             INTEGER,
    matched_triggers  TEXT,             -- comma-separated phrases that hit
    comment_count     INTEGER,
    posted_at         TEXT,             -- ISO8601 UTC from PRAW's created_utc
    status            TEXT NOT NULL,
    first_seen_at     TEXT NOT NULL DEFAULT (datetime('now')),
    status_changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_seen_status    ON seen_posts(status);
CREATE INDEX IF NOT EXISTS idx_seen_subreddit ON seen_posts(subreddit);
CREATE INDEX IF NOT EXISTS idx_seen_score     ON seen_posts(score);
