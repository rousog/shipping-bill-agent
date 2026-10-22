"""Phase 0 的 finalize 占位节点。"""

from __future__ import annotations

from ..state import AgentState


def finalize(state: AgentState) -> dict:
    return {"trace": [{"node": "finalize", "outputs": len(state.get("outputs", []))}]}
