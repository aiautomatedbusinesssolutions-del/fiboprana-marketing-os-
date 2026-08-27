"""SQLite connection helper for the capture module.

Two functions:
    get_connection()    -> sqlite3.Connection bound to capture/conversations.db
    ensure_schema(conn) -> applies schema.sql (idempotent)

Both are safe to call from any working directory because paths are
resolved relative to this file.
"""

from pathlib import Path
import sqlite3

MODULE_DIR = Path(__file__).resolve().parent
DB_PATH = MODULE_DIR / "conversations.db"
SCHEMA_PATH = MODULE_DIR / "schema.sql"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # SQLite has FK enforcement OFF by default per-connection. The
    # product_idea_observations join table declares ON DELETE CASCADE,
    # which only fires when FK enforcement is on; turn it on here so the
    # cascades actually run on idea/observation deletion.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _ensure_columns(conn, table, columns):
    """Idempotent ADD COLUMN: skip columns that already exist on `table`.

    SQLite's `ALTER TABLE ... ADD COLUMN` predates the IF NOT EXISTS variant
    on older builds, so we introspect via PRAGMA before issuing the ALTER.
    """
    existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    for name, decl in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def ensure_schema(conn):
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    # In-place migrations for columns added after the original CREATE TABLE.
    # Fresh DBs already have them via schema.sql; older DBs get them ALTERed in.
    _ensure_columns(conn, "conversations", {
        "follow_up_status_changed_at": "TEXT",
    })
    # Build pipeline columns on product_ideas (WORKFLOW.md Step 4 -> 5). See the
    # table comment in schema.sql. All nullable TEXT; build_stage's vocabulary is
    # enforced in ideas/routes.py, not by a CHECK, so it can change without a rebuild.
    _ensure_columns(conn, "product_ideas", {
        "build_stage":           "TEXT",
        "gate_week":             "TEXT",
        "registry_match":        "TEXT",
        "target_query":          "TEXT",
        "differentiation_angle": "TEXT",
        "quality_notes":         "TEXT",
        "app_url":               "TEXT",
        "sitemap_added":         "TEXT",
        "video_url":             "TEXT",
    })
    # Built here (not in schema.sql) so the column is guaranteed to exist first:
    # on an existing DB the executescript above runs before the ALTER that adds
    # build_stage, so a CREATE INDEX on it in schema.sql would fail.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_product_ideas_build_stage "
        "ON product_ideas(build_stage)"
    )
    conn.commit()
