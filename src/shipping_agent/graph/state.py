"""LangGraph 状态定义。

`Annotated[list, operator.add]` 表示该字段用「追加」方式合并；
没有 reducer 的字段默认「后写覆盖前写」。
"""

from __future__ import annotations

import operator
from typing import Annotated, TypedDict

from ..models import (
    AssetRef,
    InternalBillRecord,
    MatchResult,
    OperationRecord,
    OutputRecord,
    RawDocument,
    ReviewItem,
    ValidationIssue,
)


def merge_dict(left: dict, right: dict) -> dict:
    return {**left, **right}


class AgentState(TypedDict, total=False):
    # 运行信息
    run_id: str
    thread_id: str

    # 输入 / 资产
    input_paths: list[str]
    pending_files: list[str]
    processed_files: Annotated[list[str], operator.add]
    assets: Annotated[list[AssetRef], operator.add]

    # 解析结果
    documents: Annotated[list[RawDocument], operator.add]

    # 抽取 / 标准化
    records: Annotated[list[InternalBillRecord], operator.add]
    issues: Annotated[list[ValidationIssue], operator.add]

    # 匹配 / 决策
    matches: Annotated[list[MatchResult], operator.add]
    operations: Annotated[list[OperationRecord], operator.add]

    # 输出 / 人工
    outputs: Annotated[list[OutputRecord], operator.add]
    review_queue: Annotated[list[ReviewItem], operator.add]
    review_decisions: Annotated[list[dict], operator.add]

    # 控制
    retry_counts: Annotated[dict[str, int], merge_dict]
    errors: Annotated[list[str], operator.add]
    trace: Annotated[list[dict], operator.add]
