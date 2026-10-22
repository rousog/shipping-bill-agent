"""AR/AP 判定：记账主体 + 签发方/接收方 + 收费方向。

LLM 可以在抽取时给 posting_type_candidate；
最终由本模块按记账主体规则复核，冲突/不明一律挂起。
"""

from __future__ import annotations

import re

from .config import Settings
from .models import (
    InternalBillRecord,
    PostingType,
    ValidationIssue,
)


def _normalize(value: str | None) -> str:
    text = (value or "").casefold()
    return re.sub(r"[^a-z0-9一-鿿]+", "", text)


def _matches_entity(value: str | None, entity: str, aliases: list[str]) -> bool:
    if not value:
        return False
    haystack = _normalize(value)
    for candidate in [entity, *aliases]:
        needle = _normalize(candidate)
        if needle and needle in haystack:
            return True
    return False


def rule_posting_type(
    record: InternalBillRecord,
    settings: Settings,
) -> tuple[PostingType | None, str]:
    issuer_is_entity = _matches_entity(
        record.transaction_party.issuer,
        settings.accounting_entity,
        settings.accounting_entity_aliases,
    )
    recipient_is_entity = _matches_entity(
        record.transaction_party.recipient,
        settings.accounting_entity,
        settings.accounting_entity_aliases,
    )

    if issuer_is_entity and not recipient_is_entity:
        return PostingType.AR, "issuer is accounting entity"
    if recipient_is_entity and not issuer_is_entity:
        return PostingType.AP, "recipient is accounting entity"
    if issuer_is_entity and recipient_is_entity:
        return None, "both parties are accounting entity"
    return None, "neither party is accounting entity"


def resolve_posting_type(
    record: InternalBillRecord,
    settings: Settings,
) -> tuple[PostingType | None, list[ValidationIssue]]:
    rule_result, reason = rule_posting_type(record, settings)
    candidate_value = (
        record.financial_attributes.posting_type.value
        if record.financial_attributes.posting_type
        else None
    )
    candidate = (
        PostingType(candidate_value)
        if candidate_value in ("AR", "AP")
        else None
    )

    issues: list[ValidationIssue] = []
    policy = (settings.ar_ap_policy or "hybrid").lower()

    if candidate and rule_result and candidate != rule_result:
        issues.append(
            ValidationIssue(
                code="AR_AP_CONFLICT",
                severity="hard",
                message=(
                    f"LLM 候选 {candidate.value} 与规则结果 {rule_result.value} 冲突"
                ),
            )
        )
        record.financial_attributes.posting_type = None
        return None, issues

    if candidate and rule_result is None:
        # hybrid / llm_first：规则判不了时采用 LLM，但记 soft 复核标记
        # rule_first：仍然挂起
        severity = "soft" if policy in ("hybrid", "llm_first") else "hard"
        # hybrid：采用 LLM 但要求人工确认；llm_first：采用 LLM 不强制复核；
        # rule_first：hard + 复核。
        review_required = policy in ("hybrid", "rule_first")
        issues.append(
            ValidationIssue(
                code="AR_AP_UNVERIFIED",
                severity=severity,
                message=(
                    f"LLM 给出 {candidate.value}，但记账主体规则无法确认"
                    f"（policy={policy}）"
                ),
                review_required=review_required,
            )
        )
        if policy == "rule_first":
            record.financial_attributes.posting_type = None
            return None, issues
        record.financial_attributes.posting_type = candidate
        return candidate, issues

    if rule_result is not None:
        record.financial_attributes.posting_type = rule_result
        return rule_result, issues

    code = (
        "AR_AP_INTERNAL"
        if reason == "both parties are accounting entity"
        else "AR_AP_UNKNOWN"
    )
    issues.append(
        ValidationIssue(
            code=code,
            severity="hard",
            message=f"无法判定 AR/AP：{reason}",
        )
    )
    record.financial_attributes.posting_type = None
    return None, issues
