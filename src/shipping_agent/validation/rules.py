"""硬/软校验与置信度。

硬校验失败：金额、币种、合计不一致、无费用。
软校验：缺付款方式、未知费用名、同名不同额、OCR 混淆。
"""

from __future__ import annotations

import re
from decimal import Decimal

from ..config import Settings
from ..models import InternalBillRecord, RawDocument, ValidationIssue
from ..rules.registry import RuleRegistry
from .confidence import score_confidence

_TOLERANCE = Decimal("0.01")


def _add(
    issues: list[ValidationIssue],
    code: str,
    severity: str,
    message: str,
    *,
    charge_id: str | None = None,
    field: str | None = None,
    evidence: str | None = None,
    review_required: bool = False,
) -> None:
    issues.append(
        ValidationIssue(
            code=code,
            severity=severity,  # type: ignore[arg-type]
            message=message,
            charge_id=charge_id,
            field=field,
            evidence=evidence,
            review_required=review_required,
        )
    )


def validate_record(
    record: InternalBillRecord,
    rules: RuleRegistry,
    settings: Settings,
    doc: RawDocument | None = None,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    if not record.charges:
        _add(issues, "NO_CHARGES", "hard", "没有抽取到任何费用行")

    if not any(
        [
            record.document_info.reference_no,
            record.document_info.bl_no,
            record.document_info.booking_no,
            record.document_info.container_nos,
        ]
    ):
        _add(
            issues,
            "NO_MATCH_KEY",
            "soft",
            "单据号 / B/L / 订舱号 / 箱号全部缺失，难以匹配已有账单",
        )

    for charge in record.charges:
        name = charge.raw_name or charge.std_name or "?"
        if charge.amount is None:
            _add(
                issues,
                "AMOUNT_INVALID",
                "hard",
                f"费用 {name} 金额无效",
                charge_id=charge.charge_id,
                field="amount",
                evidence=charge.evidence,
            )
        elif charge.amount == 0:
            _add(
                issues,
                "ZERO_AMOUNT",
                "soft",
                f"费用 {name} 金额为 0",
                charge_id=charge.charge_id,
                field="amount",
                evidence=charge.evidence,
            )

        if not charge.currency:
            _add(
                issues,
                "CURRENCY_MISSING",
                "hard",
                f"费用 {name} 缺少币种",
                charge_id=charge.charge_id,
                field="currency",
                evidence=charge.evidence,
            )
        elif not rules.currency.is_valid(charge.currency):
            _add(
                issues,
                "INVALID_CURRENCY",
                "hard",
                f"币种 {charge.currency} 不在 ISO 4217 白名单",
                charge_id=charge.charge_id,
                field="currency",
                evidence=charge.evidence,
            )

        if not charge.std_name:
            _add(
                issues,
                "UNKNOWN_CHARGE",
                "soft",
                f"费用名未标准化: {charge.raw_name}",
                charge_id=charge.charge_id,
                field="raw_name",
                evidence=charge.evidence,
            )

        if charge.payment_term is None:
            _add(
                issues,
                "PAYMENT_TERM_UNKNOWN",
                "soft",
                f"费用 {name} 付款方式未知",
                charge_id=charge.charge_id,
                field="payment_term",
                evidence=charge.evidence,
            )

    if record.total_amount is not None and record.charges:
        currencies = {charge.currency for charge in record.charges}
        if (
            len(currencies) == 1
            and None not in currencies
            and record.total_currency == next(iter(currencies))
        ):
            charge_total = sum(
                (charge.amount or Decimal("0") for charge in record.charges),
                Decimal("0"),
            )
            if abs(charge_total - record.total_amount) > _TOLERANCE:
                _add(
                    issues,
                    "TOTAL_MISMATCH",
                    "hard",
                    f"明细合计 {charge_total} 与单据总计 {record.total_amount} 不一致",
                    field="total_amount",
                )

    # 完全相同 + 同名不同额
    buckets: dict[tuple, list[str | None]] = {}
    by_name: dict[str, set[Decimal]] = {}
    for charge in record.charges:
        name = charge.std_name or charge.raw_name or ""
        key = (name, str(charge.amount), charge.currency)
        buckets.setdefault(key, []).append(charge.charge_id)
        if name and charge.amount is not None:
            by_name.setdefault(name, set()).add(charge.amount)

    for (name, amount, currency), charge_ids in buckets.items():
        if len(charge_ids) > 1:
            _add(
                issues,
                "DUPLICATE_CHARGE",
                "soft",
                f"疑似重复费用: {name} / {amount} / {currency} × {len(charge_ids)}",
                charge_id=charge_ids[0],
            )
    for name, amounts in by_name.items():
        if len(amounts) > 1:
            _add(
                issues,
                "SAME_NAME_DIFFERENT_AMOUNT",
                "soft",
                f"同名费用金额不同: {name} -> {sorted(str(a) for a in amounts)}",
            )

    # LLM 信号：自评置信度、证据覆盖率、规则/LLM 冲突
    if record.llm_used:
        if record.llm_confidence is None:
            _add(
                issues,
                "LLM_CONFIDENCE_MISSING",
                "soft",
                "LLM 参与抽取但未给出置信度",
            )
        elif record.llm_confidence < 0.5:
            _add(
                issues,
                "LLM_LOW_CONFIDENCE",
                "soft",
                f"LLM 自评置信度偏低: {record.llm_confidence}",
                review_required=True,
            )

        if (
            record.llm_evidence_coverage is not None
            and record.llm_evidence_coverage < 0.5
        ):
            _add(
                issues,
                "LLM_LOW_EVIDENCE",
                "soft",
                f"LLM 证据覆盖率偏低: {record.llm_evidence_coverage}",
                review_required=record.llm_evidence_coverage < 0.3,
            )

    for conflict in record.extraction_conflicts:
        _add(
            issues,
            "EXTRACTION_CONFLICT",
            "soft",
            f"规则/LLM 抽取冲突: {conflict}",
            review_required=True,
        )

    # OCR 相关
    if doc is not None and doc.metadata.get("ocr_used"):
        ocr = doc.metadata.get("ocr") or {}
        ocr_charges = ocr.get("charges") or []
        if ocr_charges and len(ocr_charges) != len(record.charges):
            _add(
                issues,
                "OCR_TEXT_DIVERGENCE",
                "soft",
                f"OCR 费用行 {len(ocr_charges)} 条，本地文本 {len(record.charges)} 条",
            )
        if ocr.get("requires_review"):
            _add(
                issues,
                "OCR_REVIEW_REQUIRED",
                "soft",
                "OCR 标记 requiresReview",
            )
        reference = record.document_info.reference_no or ""
        if re.search(r"Q", reference):
            _add(
                issues,
                "OCR_CONFUSION",
                "soft",
                f"单号 {reference} 含 Q，可能是 O/Q 识别混淆",
                field="reference_no",
            )

    confidence = score_confidence(issues)
    record.confidence = confidence
    if confidence < settings.confidence_threshold:
        _add(
            issues,
            "LOW_CONFIDENCE",
            "soft",
            f"置信度 {confidence} 低于阈值 {settings.confidence_threshold}",
        )

    record.issues.extend(issues)
    return issues
