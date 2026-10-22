"""严格输出投影：InternalBillRecord -> OutputRecord（题目 schema）。"""

from __future__ import annotations

from .audit import build_reasons
from .models import (
    AuditTrail,
    ChargeItem,
    DocumentInfo,
    ExistingBill,
    InternalBillRecord,
    MatchResult,
    Operation,
    OutputCharge,
    OutputDocumentInfo,
    OutputFinancialAttributes,
    OutputParty,
    OutputRecord,
    PartyInfo,
    RawDocument,
    ValidationIssue,
)
from .utils.dates import to_iso


def _document_info(info: DocumentInfo) -> OutputDocumentInfo:
    return OutputDocumentInfo(
        document_type=info.document_type.value if info.document_type else None,
        reference_no=info.reference_no,
        document_date=to_iso(info.document_date),
    )


def _party(party: PartyInfo) -> OutputParty:
    return OutputParty(issuer=party.issuer, recipient=party.recipient)


def _charge(charge: ChargeItem) -> OutputCharge:
    return OutputCharge(
        charge_item=charge.std_name or charge.raw_name,
        amount=float(charge.amount) if charge.amount is not None else None,
        currency=charge.currency,
        payment_term=charge.payment_term.value if charge.payment_term else None,
    )


def project_output(
    *,
    doc: RawDocument,
    record: InternalBillRecord,
    charge: ChargeItem,
    operation: Operation,
    match: MatchResult | None = None,
    matched_bill: ExistingBill | None = None,
    changed_fields: list[str] | None = None,
    issues: list[ValidationIssue] | None = None,
    extra_reasons: list[str] | None = None,
) -> OutputRecord:
    # UPDATE / NO_CHANGE 以被匹配的账单为基准；其余用输入记录
    use_bill = matched_bill is not None and operation in (
        Operation.UPDATE,
        Operation.NO_CHANGE,
    )
    document_info = (
        matched_bill.document_info if use_bill else record.document_info
    )
    party = (
        matched_bill.transaction_party if use_bill else record.transaction_party
    )

    reasons = build_reasons(
        doc=doc,
        record=record,
        charge=charge,
        operation=operation,
        match=match,
        changed_fields=changed_fields,
        issues=issues,
        posting_type=record.financial_attributes.posting_type,
        extra=extra_reasons,
    )

    return OutputRecord(
        operation=operation,
        document_info=_document_info(document_info),
        transaction_party=_party(party),
        charge_details=_charge(charge),
        financial_attributes=OutputFinancialAttributes(
            accounting_entity=record.financial_attributes.accounting_entity,
            posting_type=(
                record.financial_attributes.posting_type.value
                if record.financial_attributes.posting_type
                else None
            ),
        ),
        audit_trail=AuditTrail(ai_reasoning=reasons),
    )
