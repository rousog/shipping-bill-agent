"""Phase 4 完整工作流状态。"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class PipelineState(TypedDict, total=False):
    run_id: str
    thread_id: str
    input_paths: list[str]

    # 各阶段结果（存 dict，便于 checkpoint 序列化）
    documents: list[dict]
    records: list[dict]
    operations: list[dict]
    outputs: list[dict]
    review_decisions: dict

    auto_approve_pending: bool

    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]
