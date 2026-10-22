"""规则抽取与 LLM 抽取的合并。

原则：
- 规则结果优先（文本 PDF 能保留行级明细）；
- LLM 补规则缺失的字段；
- 两者冲突不自动裁决，记 conflict，交给校验/复核。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

from ..models import ExtractedCharge, ExtractionResult
from ..utils.dates import parse_date

_SCALAR_FIELDS = (
    "document_type",
    "reference_no",
    "document_date",
    "issuer",
    "recipient",
    "shipper",
    "consignee",
    "bl_no",
    "hbl_no",
    "booking_no",
)


@dataclass
class MergeResult:
    result: ExtractionResult
    conflicts: list[str] = field(default_factory=list)
    llm_used: bool = False
    llm_confidence: float | None = None


def _normalize_text(value: object) -> str:
    text = str(value or "").casefold()
    return re.sub(r"[^a-z0-9一-鿿]+", "", text)


def _equivalent_scalar(field_name: str, left: object, right: object) -> bool:
    if field_name == "document_date":
        left_date = parse_date(str(left))
        right_date = parse_date(str(right))
        if left_date and right_date:
            return left_date == right_date

    left_text = _normalize_text(left)
    right_text = _normalize_text(right)
    if not left_text or not right_text:
        return True
    if left_text == right_text:
        return True

    # 公司名可能出现“中文 + 英文括号”的差异，只对交易方允许包含关系
    if field_name in {"issuer", "recipient", "shipper", "consignee"}:
        return left_text in right_text or right_text in left_text
    return False


def _deci_to_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _charge_amount_key(charge: ExtractedCharge) -> tuple:
    amount = charge.amount
    if isinstance(amount, Decimal):
        amount_text = _deci_to_text(amount)
    elif amount is not None:
        amount_text = str(amount)
    else:
        amount_text = None
    return (amount_text, (charge.currency or "").upper())


def merge_extraction(
    rule_result: ExtractionResult,
    llm_result: ExtractionResult,
    *,
    llm_used: bool = False,
) -> MergeResult:
    merged = ExtractionResult()
    conflicts: list[str] = []

    for field_name in _SCALAR_FIELDS:
        rule_value = getattr(rule_result, field_name)
        llm_value = getattr(llm_result, field_name)
        if (
            rule_value
            and llm_value
            and not _equivalent_scalar(field_name, rule_value, llm_value)
        ):
            conflicts.append(
                f"{field_name}: rule={rule_value!r} llm={llm_value!r}"
            )
        setattr(merged, field_name, rule_value or llm_value)

    merged.container_nos = list(
        dict.fromkeys(rule_result.container_nos + llm_result.container_nos)
    )
    merged.total_amount = rule_result.total_amount or llm_result.total_amount
    merged.total_currency = (
        rule_result.total_currency or llm_result.total_currency
    )
    merged.posting_type_candidate = llm_result.posting_type_candidate
    merged.ar_ap_reasoning = llm_result.ar_ap_reasoning
    merged.confidence = llm_result.confidence if llm_used else None
    merged.evidence = {**llm_result.evidence, **rule_result.evidence}

    if rule_result.charges and llm_result.charges:
        rule_keys = {_charge_amount_key(charge) for charge in rule_result.charges}
        llm_keys = {_charge_amount_key(charge) for charge in llm_result.charges}
        if (
            len(rule_result.charges) != len(llm_result.charges)
            or rule_keys != llm_keys
        ):
            conflicts.append(
                "charges: rule="
                f"{len(rule_result.charges)} 条, llm={len(llm_result.charges)} 条"
            )
        merged.charges = rule_result.charges
    else:
        merged.charges = rule_result.charges or llm_result.charges

    return MergeResult(
        result=merged,
        conflicts=conflicts,
        llm_used=llm_used,
        llm_confidence=llm_result.confidence if llm_used else None,
    )
