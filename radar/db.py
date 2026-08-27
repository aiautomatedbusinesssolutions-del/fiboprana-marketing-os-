"""SQLite connection helper for the radar module.

Mirrors sourcing_web/db.py — separate DB file (radar/seen.db) so the radar
module has an independent lifecycle from the sourcing modules. When the
shared project DB lands, the radar_items table migrates over cleanly.
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
