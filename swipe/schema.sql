-- Swipe file: the founder's content-signal capture in two layers, mirroring the
-- capture -> synthesis pattern. The raw inbox is allowed to be junky; the
-- promoted signals are not.
--
-- swipe_raw: frictionless in-the-moment dump (a link + platform + a note on why
-- it worked). status: 'new' (awaiting triage) | 'promoted' | 'discarded'.
-- draft_json caches the AI pre-structure so the triage page doesn't re-call Claude.
CREATE TABLE IF NOT EXISTS swipe_raw (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    url         TEXT,
    platform    TEXT,
    why_note    TEXT,
    status      TEXT NOT NULL DEFAULT 'new',
    draft_json  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_swipe_raw_status ON swipe_raw(status);

-- swipe_signals: the curated layer the content engine reads. One row per
-- promoted raw item, structured for reuse as "shape inspiration, never copy".
CREATE TABLE IF NOT EXISTS swipe_signals (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_id                INTEGER,
    platform              TEXT,
    hook_type             TEXT,
    why_it_landed         TEXT,
    nates_angle           TEXT,
    recognizable_specific TEXT,
    tags                  TEXT,
    active                INTEGER NOT NULL DEFAULT 1,
    created_at            TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_swipe_signals_platform ON swipe_signals(platform);
CREATE INDEX IF NOT EXISTS idx_swipe_signals_active   ON swipe_signals(active);
