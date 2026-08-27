"""SQLite connection helper for the Exa sourcing module.

Mirrors sourcing/db.py — separate DB file (sourcing_web/seen.db) so the
two sourcing modules have independent lifecycles and can be evolved or
swapped without coupling.
"""

from pathlib import Path
import sqlite3

MODULE_DIR = Path(__file__).resolve().parent
DB_PATH = MODULE_DIR / "seen.db"
SCHEMA_PATH = MODULE_DIR / "schema.sql"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn):
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    conn.commit()
