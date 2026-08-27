CREATE TABLE IF NOT EXISTS conversations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    date                 TEXT NOT NULL,    -- YYYY-MM-DD
    source               TEXT,             -- reddit / discord / twitter / ...
    source_detail        TEXT,             -- subreddit, server name, etc.
    handle               TEXT,
    contact_info         TEXT,
    segment              TEXT,             -- beginner / intermediate / has-broker / ...
    experience_level     TEXT,
    stated_goal          TEXT,
    pain_points          TEXT,
    quotes               TEXT,
    feature_implications TEXT,
    downloaded_app       TEXT,             -- yes / no / unknown + notes
    feedback_on_app      TEXT,
    follow_up_status     TEXT,             -- none / awaiting-reply / converted / lost
    follow_up_status_changed_at TEXT,      -- ISO datetime; bumped when follow_up_status changes
    notes                TEXT,
    tags                 TEXT,             -- comma-separated, lowercased on insert
    raw_transcript       TEXT,             -- full pasted conversation (paste entry path); nullable
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_conversations_date    ON conversations(date);
CREATE INDEX IF NOT EXISTS idx_conversations_source  ON conversations(source);
CREATE INDEX IF NOT EXISTS idx_conversations_segment ON conversations(segment);


-- Observations: passive research on existing online threads where I'm not a
-- participant. Distinct from conversations (active exchanges) because the data
-- shape differs: no handle, no contact_info, no follow_up, plus interpretive
-- fields (segment_guess, content_hooks) that only make sense for outside reads.
CREATE TABLE IF NOT EXISTS observations (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    date_observed        TEXT NOT NULL,    -- YYYY-MM-DD
    source               TEXT,             -- reddit / twitter / youtube / forum / ...
    source_detail        TEXT,             -- subreddit, channel, etc.
    source_url           TEXT,             -- direct link to the thread/post
    raw_content          TEXT,             -- post + replies I want to preserve
    segment_guess        TEXT,             -- my guess at their segment
    pain_points          TEXT,
    notable_quotes       TEXT,             -- verbatim phrases for content inspiration
    feature_implications TEXT,
    content_hooks        TEXT,             -- potential TikTok/X hook ideas
    tags                 TEXT,             -- comma-separated, lowercased on insert
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_observations_date   ON observations(date_observed);
CREATE INDEX IF NOT EXISTS idx_observations_source ON observations(source);


-- Product ideas: features for Fiboprana (the product app), captured manually
-- or proposed by AI scanning observations + SYNTHESIS.md. Each idea gets an
-- automatic guardrails check on creation and can be re-checked on demand.
-- guardrails_check_result holds JSON: {"status":"ok|conflict|error","rules_violated":[...],"explanation":"..."}.
--
-- Two orthogonal lifecycles live on this table:
--   * status      (idea/approved/rejected) — the brainstorm/approval inbox.
--   * build_stage (NULL -> captured -> gated -> building -> live, or
--                  content-instead) — the WORKFLOW.md Step 4 -> 5 build pipeline.
-- An idea is just an inbox row until it clears the Step 4 gate; then build_stage
-- starts tracking it through to a shipped tool. The gate fields (registry_match,
-- target_query, differentiation_angle, quality_notes) record the Step 4 decision;
-- app_url/sitemap_added/video_url track the Step 5 ship. build_stage has NO CHECK
-- constraint on purpose — the vocabulary is enforced in ideas/routes.py so it can
-- evolve without an SQLite table rebuild (status's baked-in CHECK is exactly the
-- migration pain we're avoiding). The app project (fiboprana-site) reads
-- `WHERE build_stage IS NOT NULL` to get the decided build queue, not the inbox.
CREATE TABLE IF NOT EXISTS product_ideas (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    title                    TEXT NOT NULL,
    description              TEXT NOT NULL,
    source                   TEXT,
    status                   TEXT NOT NULL DEFAULT 'idea'
                                 CHECK (status IN ('idea', 'approved', 'rejected')),
    tags                     TEXT,
    proposed_by              TEXT NOT NULL DEFAULT 'manual'
                                 CHECK (proposed_by IN ('manual', 'ai')),
    guardrails_check_result  TEXT,
    guardrails_checked_at    TEXT,
    -- Build pipeline (WORKFLOW.md Step 4 -> 5). All nullable; NULL build_stage =
    -- brainstorm only. See the table comment above for the lifecycle.
    build_stage              TEXT,             -- captured | gated | building | live | content-instead
    gate_week                TEXT,             -- Monday (YYYY-MM-DD) of the week this idea cleared the gate;
                                               -- stamped once by ideas/routes.py when build_stage first passes
                                               -- the gate. THE label for "which week's feature is this" —
                                               -- cards badge the row matching the current week as THIS WEEK.
    registry_match           TEXT,             -- Step 4.1: existing app feature/tool? 'no' or 'yes: <tool>'
    target_query             TEXT,             -- Step 4.2: the ONE distinct SEO/AEO query (no cannibalization)
    differentiation_angle    TEXT,             -- Step 4.3: the know-yourself / personalized-style framing
    quality_notes            TEXT,             -- Step 4.4: can we hold the bar? interactive + FAQs + funnel hook
    app_url                  TEXT,             -- Step 5: live tool URL in the app
    sitemap_added            TEXT,             -- Step 5: sitemap.ts updated? '' | 'yes' | 'no' (easy to forget)
    video_url                TEXT,             -- Step 5: feature video URL once shipped
    created_at               TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at               TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_product_ideas_status      ON product_ideas(status);
CREATE INDEX IF NOT EXISTS idx_product_ideas_proposed_by ON product_ideas(proposed_by);
CREATE INDEX IF NOT EXISTS idx_product_ideas_created     ON product_ideas(created_at);
-- NB: the idx_product_ideas_build_stage index is created in db.py, not here.
-- On an existing DB this executescript runs before _ensure_columns ALTERs in
-- build_stage, so a CREATE INDEX on that column here would fail; db.py builds it
-- after the column is guaranteed to exist.


-- Join table linking product_ideas back to the observations that motivated
-- them. AI-proposed ideas reliably cite their sources as "Obs #N" /
-- "Observation #N" / "Observations #N, #M" strings in product_ideas.source;
-- this table is populated by parsing those mentions out of source on insert.
-- Manual ideas can populate the same way by typing "Obs #N" in their source
-- field. UNIQUE prevents duplicate links when the same observation is cited
-- multiple times in one source blob.
CREATE TABLE IF NOT EXISTS product_idea_observations (
    idea_id        INTEGER NOT NULL REFERENCES product_ideas(id) ON DELETE CASCADE,
    observation_id INTEGER NOT NULL REFERENCES observations(id)  ON DELETE CASCADE,
    UNIQUE (idea_id, observation_id)
);

CREATE INDEX IF NOT EXISTS idx_pio_idea        ON product_idea_observations(idea_id);
CREATE INDEX IF NOT EXISTS idx_pio_observation ON product_idea_observations(observation_id);
