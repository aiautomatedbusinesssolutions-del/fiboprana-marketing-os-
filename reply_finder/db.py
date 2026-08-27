"""SQLite connection helper for the reply_finder reply log.

Mirrors radar/db.py — a separate DB file
(reply_finder/reply_log.db) so the module stays self-contained. The scanner
(scanner.py) is still a pure, DB-free function; only sent replies are persisted,
through store.py. When the shared project DB lands, reply_log migrates over
cleanly (same columns, swapped connection).
"""

from pathlib import Path
import sqlite3

MODULE_DIR = Path(__file__).resolve().parent
DB_PATH = MODULE_DIR / "reply_log.db"
SCHEMA_PATH = MODULE_DIR / "schema.sql"


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn):
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
