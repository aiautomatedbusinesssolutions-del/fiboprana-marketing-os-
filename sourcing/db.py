"""SQLite connection helper for the sourcing module.

Mirrors capture/db.py so the two modules behave the same way at the
connection layer. Separate DB file (sourcing/seen.db) because the data
has a different lifecycle — most rows get skipped and never matter again.
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
