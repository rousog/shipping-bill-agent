"""Phase 0 的 ingest 占位节点。

后续会替换为：
- 文件路由
- sha256 计算
- AssetStore 落盘
- assets 表登记
"""

from __future__ import annotations

from ..state import AgentState


def ingest(state: AgentState) -> dict:
    paths = state.get("pending_files") or state.get("input_paths", [])
    return {
        "pending_files": [],
        "processed_files": list(paths),
        "trace": [{"node": "ingest", "files": len(paths)}],
    }
