"""把 ExtractionResult 转成 InternalBillRecord，并做业务标准化。"""

from __future__ import annotations

from ..config import Settings
from ..models import (
    ChargeItem,
    DocumentInfo,
    DocumentType,
    ExtractionResult,
    FinancialAttributes,
    InternalBillRecord,
    PartyInfo,
    PaymentTerm,
    PostingType,
    RawDocument,
    ValidationIssue,
)
from ..rules.registry import RuleRegistry
from ..utils.dates import parse_date


def _document_type(value: str | None) -> DocumentType:
    if not value:
        return DocumentType.OTHER
    try:
        return DocumentType(value)
    except ValueError:
        return DocumentType.OTHER


def _posting_type(value: str | None) -> PostingType | None:
    if value in ("AR", "AP"):
        return PostingType(value)
    return None


def _evidence_coverage(extraction: ExtractionResult) -> float | None:
    """LLM 证据覆盖率：有值的字段/费用行中，有多少带了 evidence。"""
    values: list[str] = []
    covered = 0

    for field_name in (
        "document_type",
        "reference_no",
        "document_date",
        "issuer",
        "recipient",
        "bl_no",
        "booking_no",
    ):
        if getattr(extraction, field_name):
            values.append(field_name)
            if extraction.evidence.get(field_name):
                covered += 1

    for charge in extraction.charges:
        values.append("charge")
        if charge.evidence:
            covered += 1

    if not values:
        return None
    return round(covered / len(values), 4)


def build_record(
    doc: RawDocument,
    extraction: ExtractionResult,
    rules: RuleRegistry,
    settings: Settings,
    *,
    index: int = 0,
    llm_used: bool = False,
    llm_confidence: float | None = None,
    extraction_conflicts: list[str] | None = None,
) -> InternalBillRecord:
    document_type = _document_type(extraction.document_type)
    charges: list[ChargeItem] = []

    for line_no, extracted in enumerate(extraction.charges, 1):
        std_name, match_status = rules.charges.normalize_with_status(
            extracted.raw_name
        )
        currency = rules.currency.normalize(extracted.currency)
        payment_term = rules.payment_terms.normalize(extracted.payment_term)

        amount = extracted.amount
        if (
            document_type == DocumentType.CREDIT_NOTE
            and amount is not None
            and amount > 0
        ):
            amount = -amount

        issues: list[ValidationIssue] = []
        if match_status == "unknown":
            issues.append(
                ValidationIssue(
                    code="UNKNOWN_CHARGE",
                    severity="soft",
                    message=f"费用名未命中别名表: {extracted.raw_name}",
                    field="raw_name",
                    evidence=extracted.evidence,
                )
            )

        charges.append(
            ChargeItem(
                charge_id=f"{doc.doc_id}-CHG-{index + 1:03d}-{line_no:02d}",
                line_no=line_no,
                raw_name=extracted.raw_name,
                std_name=std_name,
                alias_version=rules.alias_version,
                quantity=extracted.quantity,
                unit_price=extracted.unit_price,
                amount=amount,
                currency=currency,
                payment_term=payment_term,
                evidence=extracted.evidence,
                confidence=1.0 if match_status == "exact" else 0.8,
                issues=issues,
            )
        )

    return InternalBillRecord(
        record_id=f"{doc.doc_id}-REC-{index + 1:03d}",
        document_info=DocumentInfo(
            document_type=document_type,
            reference_no=extraction.reference_no,
            document_date=parse_date(extraction.document_date),
            bl_no=extraction.bl_no,
            hbl_no=extraction.hbl_no,
            booking_no=extraction.booking_no,
            container_nos=extraction.container_nos,
        ),
        transaction_party=PartyInfo(
            issuer=extraction.issuer,
            recipient=extraction.recipient,
            shipper=extraction.shipper,
            consignee=extraction.consignee,
        ),
        charges=charges,
        total_amount=extraction.total_amount,
        total_currency=extraction.total_currency,
        financial_attributes=FinancialAttributes(
            accounting_entity=settings.accounting_entity,
            posting_type=_posting_type(extraction.posting_type_candidate),
            # 只有 LLM 真的给了候选时才保留理由，避免“无法判断”的噪音
            ar_ap_reasoning=(
                extraction.ar_ap_reasoning
                if extraction.posting_type_candidate
                else None
            ),
        ),
        confidence=1.0,
        issues=[],
        llm_used=llm_used,
        llm_confidence=llm_confidence if llm_used else None,
        llm_evidence_coverage=(
            _evidence_coverage(extraction) if llm_used else None
        ),
        extraction_conflicts=list(extraction_conflicts or []),
    )
