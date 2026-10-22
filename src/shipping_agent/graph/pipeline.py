"""Phase 4 完整工作流组装。"""

from __future__ import annotations

from functools import partial

from langgraph.graph import END, START, StateGraph

from .context import GraphContext
from .pipeline_nodes import (
    decide_node,
    extract_node,
    finalize_node,
    ingest_node,
    output_node,
    review_node,
    route_review,
)
from .pipeline_state import PipelineState


def build_pipeline(context: GraphContext, checkpointer=None):
    builder = StateGraph(PipelineState)

    builder.add_node("ingest", partial(ingest_node, context=context))
    builder.add_node("extract", partial(extract_node, context=context))
    builder.add_node("decide", partial(decide_node, context=context))
    builder.add_node("review", partial(review_node, context=context))
    builder.add_node("output", partial(output_node, context=context))
    builder.add_node("finalize", partial(finalize_node, context=context))

    builder.add_edge(START, "ingest")
    builder.add_edge("ingest", "extract")
    builder.add_edge("extract", "decide")
    builder.add_conditional_edges(
        "decide",
        route_review,
        {"review": "review", "output": "output"},
    )
    builder.add_edge("review", "output")
    builder.add_edge("output", "finalize")
    builder.add_edge("finalize", END)

    return builder.compile(checkpointer=checkpointer)
