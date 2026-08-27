-- reply_log: every reply I actually send, stored as a SELF-CONTAINED training
-- example for the future reply agent (Hermes). The scanner (scanner.py) is a
-- pure function that surfaces candidates and persists nothing; this is where a
-- *sent* reply gets recorded so the agent can learn two loops over time:
--   * "what's working"        -> reply_text + angle + rationale  ->  outcome_*
--   * "find replies better"   -> finder_score + flips + soft_triggers (the
--                                scanner's signal at selection time) -> outcome_*
--
-- Design notes:
--   * The post is DENORMALIZED onto the row (post_url/community/title/...) so each
--     example stands alone without a join — what an agent wants to train on.
--   * Outcome fields are nullable and filled in LATER (by me or a scraper agent);
--     outcome_checked_at stamps the last update. This is the supervision signal.
--   * idea_id is a SOFT reference to capture/conversations.db product_ideas(id)
--     (cross-DB, so no FK enforcement here); it becomes a real FK when both tables
--     land in one Supabase/Postgres schema.
--   * No CHECK constraints on platform/op_replied/etc. on purpose — the vocab is
--     kept consistent in store.py, so it can evolve without an SQLite table rebuild.
CREATE TABLE IF NOT EXISTS reply_log (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,

    -- when + where
    replied_at         TEXT,                              -- ISO date/datetime I posted the reply
    platform           TEXT NOT NULL DEFAULT 'reddit',     -- reddit | x | youtube | forum | ...

    -- the post I replied to (denormalized: self-contained training example)
    post_url           TEXT,
    community          TEXT,                               -- subreddit / handle / channel, e.g. 'r/Meditation'
    post_title         TEXT,
    post_excerpt       TEXT,                               -- the question/pain in their words
    post_author        TEXT,

    -- the scanner's signal at selection time (lets the agent learn the finder)
    finder_score       INTEGER,                            -- score_reply_target() when chosen
    flips              TEXT,                               -- matched flip phrases, comma-separated
    soft_triggers      TEXT,                               -- matched beginner phrases, comma-separated
    angle              TEXT,                               -- strategy/template used (e.g. 'pre_decision flip')

    -- my reply (the moat — kept verbatim)
    reply_text         TEXT NOT NULL,
    reply_url          TEXT,                               -- permalink to my reply, once known
    rationale          TEXT,                               -- WHY this post + WHY this angle
    predicted_grade    TEXT,                               -- my pre-send call: strong|ok|off, set BEFORE seeing engagement (the calibration anchor)

    -- outcome / performance (the 'what's working' loop; filled/updated after posting)
    outcome_score      INTEGER,                            -- upvotes/karma on the reply
    op_replied         TEXT,                               -- yes | no | unknown
    led_to_profile     TEXT,                               -- yes | no | unknown (profile visit / follow / app click)
    outcome_notes      TEXT,
    outcome_checked_at TEXT,

    -- soft link into the rest of the graph (real FK once promoted to one DB)
    idea_id            INTEGER,                            -- product_ideas.id if this reply sparked/relates to a feature

    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reply_log_replied_at ON reply_log(replied_at);
CREATE INDEX IF NOT EXISTS idx_reply_log_platform   ON reply_log(platform);
CREATE INDEX IF NOT EXISTS idx_reply_log_angle      ON reply_log(angle);
