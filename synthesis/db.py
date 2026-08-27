"""SQLite connection helper for the synthesis module.

Mirrors sourcing/db.py and attribution/db.py. Separate DB file because the
data here (regeneration history of SYNTHESIS.md) has a different lifecycle
than research data or operational click logs.
"""

from pathlib import Path
import sqlite3

MODULE_DIR = Path(__file__).resolve().parent
DB_PATH = MODULE_DIR / "runs.db"
SCHEMA_PATH = MODULE_DIR / "schema.sql"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn):
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
