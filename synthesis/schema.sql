-- synthesis_runs: one row per regeneration of SYNTHESIS.md.
--
-- The file on disk (project-root SYNTHESIS.md) is what other modules read
-- (the idea proposer in particular reads it as text). This table is the
-- history layer: every regeneration appends a row so the dashboard can
-- show "how have patterns shifted over time" without rummaging through git
-- history.
--
-- observation_ids is a JSON array of the specific observation ids included
-- in this run, so future audits can reproduce / verify what was synthesized.

CREATE TABLE IF NOT EXISTS synthesis_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    generated_at        TEXT    NOT NULL,            -- ISO datetime, UTC
    observation_count   INTEGER NOT NULL,
    observation_ids     TEXT    NOT NULL,            -- JSON array of ids
    model               TEXT,                         -- e.g. claude-sonnet-4-6
    synthesis_md        TEXT    NOT NULL,            -- full markdown output
    input_token_count   INTEGER,                      -- optional, for cost tracking
    output_token_count  INTEGER
);

CREATE INDEX IF NOT EXISTS idx_synthesis_runs_generated_at
    ON synthesis_runs(generated_at DESC);
