"""LangGraph 组装。

Phase 0 只包含 ingest -> finalize 的最小图，用来验证依赖和状态流转；
后续按 docs/graph.md 逐步加入 parse / extract / validate / match / review。
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from .nodes.finalize import finalize
from .nodes.ingest import ingest
from .state import AgentState


def build_graph(checkpointer=None):
    builder = StateGraph(AgentState)

    builder.add_node("ingest", ingest)
    builder.add_node("finalize", finalize)

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
