"""LangGraph checkpointer。

使用 SQLiteSaver，把图状态写入 data/agent.db。
人工复核（interrupt）需要 checkpointer 才能暂停/恢复。
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver


@contextmanager
def sqlite_checkpointer(db_path: str | Path):
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with SqliteSaver.from_conn_string(str(path)) as saver:
        yield saver
