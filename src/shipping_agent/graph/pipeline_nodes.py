"""Phase 4 工作流节点。"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from langgraph.types import interrupt

from ..extractors.context import build_extraction_context
from ..extractors.merge import merge_extraction
from ..extractors.record import build_record
from ..extractors.rules import extract_with_rules
from ..loaders.router import route_path
from ..models import (
    ExtractionResult,
    InternalBillRecord,
    Operation,
    RawDocument,
)
from ..validation.rules import validate_record
from .context import GraphContext
from .pipeline_state import PipelineState


def _iter_documents(doc: RawDocument):
    yield doc
    for child in doc.attachments:
        yield from _iter_documents(child)


def ingest_node(state: PipelineState, *, context: GraphContext) -> dict:
    documents: list[dict] = []
    errors: list[str] = []

    for input_path in state.get("input_paths", []):
        try:
            root = route_path(
                input_path,
                context.asset_service,
                max_depth=context.max_depth,
                ocr_client=context.ocr_client,
                use_ocr=context.use_ocr,
            )
        except Exception as exc:
            errors.append(f"{input_path}: {type(exc).__name__}: {exc}")
            continue
        documents.extend(
            doc.model_dump(mode="json") for doc in _iter_documents(root)
        )

    return {
        "documents": documents,
        "errors": errors,
        "trace": [{"node": "ingest", "documents": len(documents)}],
    }


def extract_node(state: PipelineState, *, context: GraphContext) -> dict:
    records: list[dict] = []
    errors: list[str] = []

    for index, doc_dict in enumerate(state.get("documents", [])):
        doc = RawDocument.model_validate(doc_dict)
        extraction_context = build_extraction_context(doc, context.rules)
        rule_result = extract_with_rules(doc, context.rules)

        llm_result = ExtractionResult()
        llm_error = None
        if context.llm_extractor.available:
            try:
                llm_result = context.llm_extractor.extract(extraction_context)
            except Exception as exc:
                llm_error = f"{type(exc).__name__}: {exc}"
                errors.append(f"{doc.doc_id}: LLM 抽取失败: {llm_error}")

        llm_used = context.llm_extractor.available and llm_error is None
        merged = merge_extraction(
            rule_result, llm_result, llm_used=llm_used
        )
        record = build_record(
            doc,
            merged.result,
            context.rules,
            context.settings,
            index=index,
            llm_used=llm_used,
            llm_confidence=merged.llm_confidence,
            extraction_conflicts=merged.conflicts,
        )
        validate_record(record, context.rules, context.settings, doc)

        records.append(
            {
                "doc": doc_dict,
                "record": record.model_dump(mode="json"),
                "conflicts": merged.conflicts,
                "llm_used": llm_used,
                "llm_error": llm_error,
            }
        )

    return {
        "records": records,
        "errors": errors,
        "trace": [{"node": "extract", "records": len(records)}],
    }


def decide_node(state: PipelineState, *, context: GraphContext) -> dict:
    operations: list[dict] = []
    errors: list[str] = []
    run_id = state.get("run_id", "RUN-UNKNOWN")

    for item in state.get("records", []):
        try:
            doc = RawDocument.model_validate(item["doc"])
            record = InternalBillRecord.model_validate(item["record"])
            decision = context.engine.decide(record, doc, run_id)
        except Exception as exc:
            errors.append(f"{item.get('record', {}).get('record_id')}: {exc}")
            continue

        for operation in decision.operations:
            operations.append(
                {
                    "operation_id": operation.operation_id,
                    "charge_id": operation.charge_id,
                    "operation": operation.operation.value,
                    "matched_bill_id": operation.matched_bill_id,
                    "changed_fields": operation.changed_fields,
                    "output": (
                        operation.output.model_dump(mode="json")
                        if operation.output
                        else None
                    ),
                    "reasons": operation.reasons,
                    "rejected": False,
                }
            )

    return {
        "operations": operations,
        "errors": errors,
        "trace": [{"node": "decide", "operations": len(operations)}],
    }


def route_review(state: PipelineState) -> str:
    pending = [
        operation
        for operation in state.get("operations", [])
        if operation.get("operation") == Operation.PENDING_REVIEW.value
        and not operation.get("rejected")
    ]
    return "review" if pending else "output"


def _apply_human_decision(operation: dict, decision: dict) -> dict:
    action = (decision or {}).get("action", "accept")
    if action == "reject":
        return {**operation, "rejected": True, "output": None}

    output = operation.get("output") or {}
    fields = (decision or {}).get("fields") or {}
    for section in (
        "document_info",
        "transaction_party",
        "charge_details",
        "financial_attributes",
    ):
        if section in fields and isinstance(fields[section], dict):
            output[section] = {
                **(output.get(section) or {}),
                **fields[section],
            }

    if operation.get("matched_bill_id"):
        new_operation = (
            "UPDATE" if operation.get("changed_fields") else "NO_CHANGE"
        )
    else:
        new_operation = "CREATE"

    if output:
        output["operation"] = new_operation

    return {
        **operation,
        "operation": new_operation,
        "output": output or None,
    }


def review_node(state: PipelineState, *, context: GraphContext) -> dict:
    operations = state.get("operations", [])
    pending = [
        operation
        for operation in operations
        if operation.get("operation") == Operation.PENDING_REVIEW.value
        and not operation.get("rejected")
    ]
    if not pending:
        return {"trace": [{"node": "review", "pending": 0}]}

    if state.get("auto_approve_pending"):
        decisions = {
            (operation.get("charge_id") or operation.get("operation_id")): {
                "action": "accept"
            }
            for operation in pending
        }
    else:
        decisions = interrupt(
            {
                "pending": pending,
                "run_id": state.get("run_id"),
                "thread_id": state.get("thread_id"),
            }
        )
        if isinstance(decisions, dict) and "decisions" in decisions:
            decisions = decisions["decisions"]

    if not isinstance(decisions, dict):
        decisions = {}

    updated: list[dict] = []
    review_items = {
        item.charge_id: item
        for item in context.engine.review_repository.list_pending(
            run_id=state.get("run_id")
        )
    }

    for operation in operations:
        is_pending = (
            operation.get("operation") == Operation.PENDING_REVIEW.value
            and not operation.get("rejected")
        )
        if not is_pending:
            updated.append(operation)
            continue

        key = operation.get("charge_id") or operation.get("operation_id")
        decision = decisions.get(key)
        if decision is None:
            # 没有给决定：保持 pending，不输出
            updated.append(operation)
            continue

        updated_operation = _apply_human_decision(operation, decision)
        updated.append(updated_operation)

        item = review_items.get(operation.get("charge_id"))
        if item is not None:
            status = (
                "rejected"
                if updated_operation.get("rejected")
                else "accepted"
            )
            context.engine.review_repository.resolve(
                item.review_id, status, decision
            )

    return {
        "operations": updated,
        "review_decisions": decisions,
        "trace": [{"node": "review", "pending": len(pending)}],
    }


def output_node(state: PipelineState, *, context: GraphContext) -> dict:
    outputs = [
        operation["output"]
        for operation in state.get("operations", [])
        if operation.get("output")
        and operation.get("operation") != Operation.PENDING_REVIEW.value
        and not operation.get("rejected")
    ]

    context.output_path.parent.mkdir(parents=True, exist_ok=True)
    context.output_path.write_text(
        json.dumps(outputs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return {
        "outputs": outputs,
        "trace": [{"node": "output", "outputs": len(outputs)}],
    }


def finalize_node(state: PipelineState, *, context: GraphContext) -> dict:
    counts = Counter(
        operation.get("operation")
        for operation in state.get("operations", [])
        if operation.get("output") and not operation.get("rejected")
    )
    summary = {
        "run_id": state.get("run_id"),
        "thread_id": state.get("thread_id"),
        "documents": len(state.get("documents", [])),
        "records": len(state.get("records", [])),
        "operations": len(state.get("operations", [])),
        "outputs": len(state.get("outputs", [])),
        "operation_counts": dict(counts),
        "errors": state.get("errors", []),
    }

    if context.summary_path:
        context.summary_path.parent.mkdir(parents=True, exist_ok=True)
        context.summary_path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    return {"trace": [{"node": "finalize", "summary": summary}]}
