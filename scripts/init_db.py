"""初始化 SQLite 数据库。

用法：
    python scripts/init_db.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shipping_agent.config import load_settings  # noqa: E402
from shipping_agent.db.connection import init_db  # noqa: E402

SCHEMA = Path(__file__).resolve().parents[1] / "src" / "shipping_agent" / "db" / "schema.sql"


def main() -> None:
    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    conn.close()
    print(f"DB: {settings.db_path}")
    print("Tables: " + ", ".join(row["name"] for row in tables))


if __name__ == "__main__":
    main()
