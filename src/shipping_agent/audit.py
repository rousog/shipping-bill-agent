"""audit_trail 事实模板。

用结构化事实拼理由，不让 LLM 自由发挥。
"""

from __future__ import annotations

import re

from .models import (
    ChargeItem,
    InternalBillRecord,
    MatchResult,
    Operation,
    PostingType,
    RawDocument,
    ValidationIssue,
)


def build_reasons(
    *,
    doc: RawDocument,
    record: InternalBillRecord,
    charge: ChargeItem,
    operation: Operation,
    match: MatchResult | None = None,
    changed_fields: list[str] | None = None,
    issues: list[ValidationIssue] | None = None,
    posting_type: PostingType | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    reasons: list[str] = []

    file_name = doc.metadata.get("file_name") or doc.doc_id
    # API 上传会加 8 位 hash 前缀，审计里去掉，便于阅读
    file_name = re.sub(r"^[0-9a-f]{8}_", "", file_name)
    reasons.append(f"来源：{file_name}（{doc.source_type.value}）")

    if charge.raw_name and charge.std_name and charge.raw_name != charge.std_name:
        reasons.append(
            f"原始费用名 {charge.raw_name} 标准化为 {charge.std_name}"
        )

    if match and match.bill_id:
        reasons.append(
            f"通过 {match.level or '匹配键'} 匹配到已有账单 {match.bill_id}"
        )
    elif operation == Operation.CREATE:
        reasons.append("未匹配到已有账单，按新增处理")
    elif operation == Operation.PENDING_REVIEW:
        reasons.append(f"未自动入账：{match.reason if match else '需要人工复核'}")

    if changed_fields:
        reasons.append("变更字段：" + ", ".join(changed_fields))
    elif operation == Operation.NO_CHANGE:
        reasons.append("输入字段与已有账单一致，无需变更")

    if posting_type:
        reasons.append(
            f"AR/AP：{posting_type.value}（记账主体：{record.financial_attributes.accounting_entity}）"
        )
    else:
        reasons.append("AR/AP 未确定，转人工复核")

    if record.financial_attributes.ar_ap_reasoning:
        reasons.append(
            f"LLM AR/AP 理由：{record.financial_attributes.ar_ap_reasoning}"
        )

    if record.llm_used:
        reasons.append(
            f"LLM 自评置信度：{record.llm_confidence}；"
            f"证据覆盖率：{record.llm_evidence_coverage}"
        )

    if record.extraction_conflicts:
        reasons.append(
            "规则/LLM 抽取冲突：" + "; ".join(record.extraction_conflicts)
        )

    if issues:
        codes = sorted({issue.code for issue in issues})
        reasons.append("校验：" + ", ".join(codes))

    if extra:
        reasons.extend(extra)

    return reasons
