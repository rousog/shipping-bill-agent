"""SQLite 连接与初始化。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(db_path: str | Path, schema_path: str | Path) -> sqlite3.Connection:
    conn = connect(db_path)
    schema = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(schema)
    conn.execute(
        "INSERT OR IGNORE INTO schema_version(version, applied_at) VALUES (?, ?)",
        (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
