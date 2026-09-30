"""SQLite access: connections, schema bootstrap and small query helpers.

Rows come back as dicts. Every connection enforces foreign keys, so the
relational model is checked on every write, not just documented.
"""
import sqlite3
from pathlib import Path

from . import config


def _dict_factory(cursor, row):
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


def connect(path=None, readonly=False):
    path = Path(path or config.DB_PATH)
    if readonly:
        conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True,
                               check_same_thread=False, timeout=10)
        conn.execute("PRAGMA query_only = ON")
    else:
        conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
    conn.row_factory = _dict_factory
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init_schema(conn):
    conn.executescript(config.SCHEMA_PATH.read_text())


def q(conn, sql, params=()):
    return conn.execute(sql, params).fetchall()


def q1(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()


def val(conn, sql, params=()):
    row = conn.execute(sql, params).fetchone()
    if row is None:
        return None
    return next(iter(row.values()))


def meta(conn, key, default=None):
    row = q1(conn, "SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else default


def as_of(conn):
    """The dataset's 'today' (YYYY-MM-DD). All logic uses this, never the wall clock."""
    return meta(conn, "as_of")


def now(conn):
    """The dataset's current instant (UTC ISO-8601)."""
    return meta(conn, "now_utc")
