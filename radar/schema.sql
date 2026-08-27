-- Radar items: competitor/niche signal aggregated from HN, RSS, and Exa.
-- URL is the natural unique key — the same story can surface on multiple
-- sources, but we want it once. INSERT OR IGNORE on (url) handles dedupe.
--
-- status drives the triage UI:
--   'new'     -> appears in /radar digest
--   'skipped' -> filtered out (kept for dedupe)
--   'saved'   -> appears in /radar/saved (worth revisiting)
--   'archived'-> read and dismissed (kept for dedupe)
CREATE TABLE IF NOT EXISTS radar_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    url               TEXT NOT NULL UNIQUE,
    title             TEXT,
    blurb             TEXT,
    source            TEXT NOT NULL,        -- 'hn' | 'rss' | 'exa'
    source_label      TEXT,                 -- HN query / RSS feed name / Exa query
    author            TEXT,
    published_at      TEXT,                 -- ISO8601, nullable
    score             REAL,                 -- HN points, Exa relevance, NULL for RSS
    status            TEXT NOT NULL DEFAULT 'new',
    first_seen_at     TEXT NOT NULL DEFAULT (datetime('now')),
    status_changed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_radar_status    ON radar_items(status);
CREATE INDEX IF NOT EXISTS idx_radar_source    ON radar_items(source);
CREATE INDEX IF NOT EXISTS idx_radar_published ON radar_items(published_at);

-- Cached per-topic feature summary. Keyed on (source, source_label) — the
-- same key the UI groups by. item_count snapshots the size of the batch the
-- summary was generated from, so the UI can flag staleness when new items
-- arrive in the topic without forcing an immediate regenerate.
CREATE TABLE IF NOT EXISTS radar_topic_summaries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    source_label  TEXT NOT NULL,
    summary       TEXT NOT NULL,
    item_count    INTEGER NOT NULL,
    generated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (source, source_label)
);

-- Weekly trend digest: a brand-voiced distillation of the week's NEW radar
-- pile (the cutting-edge + AI-in-fintech story), generated on demand and kept
-- as a growing journal. Distinct from radar_topic_summaries (which does
-- per-topic feature extraction). Each row stores the markdown plus a pointer to
-- the prior digest, so generation can suppress already-reported themes.
CREATE TABLE IF NOT EXISTS radar_digests (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    model             TEXT,
    digest_md         TEXT NOT NULL,
    source_item_count INTEGER NOT NULL DEFAULT 0,
    sources_json      TEXT,                 -- JSON: [{n, title, url, label}] resolved from [#N] citations
    prior_digest_id   INTEGER
);
