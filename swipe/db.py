"""SQLite connection helper for the swipe-file module.

Mirrors radar/db.py — a separate DB file (swipe/swipe.db) so the swipe file has
an independent lifecycle. When the shared project DB lands, swipe_raw and
swipe_signals migrate over cleanly.
"""

from pathlib import Path
import sqlite3

MODULE_DIR = Path(__file__).resolve().parent
DB_PATH = MODULE_DIR / "swipe.db"
SCHEMA_PATH = MODULE_DIR / "schema.sql"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn):
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
