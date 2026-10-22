"""规则抽取：不依赖 LLM。"""

from __future__ import annotations

from ..models import ExtractionResult, RawDocument, SourceType
from ..rules.registry import RuleRegistry


def extract_with_rules(doc: RawDocument, rules: RuleRegistry) -> ExtractionResult:
    text = doc.text or ""
    subject = doc.metadata.get("subject") or ""
    scan_text = "\n".join(part for part in (subject, text) if part)
    file_name = doc.metadata.get("file_name") or ""

    result = ExtractionResult()
    result.document_type = rules.extraction.infer_document_type(scan_text, file_name)

    bl_numbers = rules.extraction.extract_bl_numbers(scan_text)
    result.bl_no = bl_numbers.get("bl_no")
    result.hbl_no = bl_numbers.get("hbl_no")

    result.reference_no = (
        rules.extraction.extract_reference_no(scan_text)
        or result.bl_no
        or result.hbl_no
    )
    result.document_date = rules.extraction.extract_date_text(scan_text)
    result.booking_no = rules.extraction.extract_booking_no(scan_text)
    result.container_nos = rules.extraction.extract_containers(scan_text)

    if doc.source_type == SourceType.EML:
        result.issuer = doc.metadata.get("from")
        result.recipient = doc.metadata.get("to")
    else:
        parties = rules.extraction.extract_parties(text)
        result.issuer = parties.get("issuer")
        result.recipient = parties.get("recipient")
        result.shipper = parties.get("shipper")
        result.consignee = parties.get("consignee")

    total_amount, total_currency = rules.extraction.extract_total(scan_text)
    document_currency = (
        total_currency or rules.extraction.extract_currency(scan_text)
    )
    document_payment_term = (
        rules.extraction.extract_document_payment_term(scan_text)
    )

    sheet_mappings = doc.metadata.get("sheets")
    table_charges = rules.extraction.extract_table_charges(
        doc.tables,
        sheet_mappings=sheet_mappings,
        default_currency=document_currency,
        default_payment_term=document_payment_term,
    )
    text_charges = rules.extraction.extract_text_charges(
        text,
        default_currency=document_currency,
        default_payment_term=document_payment_term,
    )
    ocr_charges = rules.extraction.extract_ocr_charges(doc.metadata.get("ocr"))

    if doc.source_type in (SourceType.EXCEL, SourceType.WORD) and table_charges:
        charges = table_charges
    elif text_charges:
        charges = text_charges
    elif table_charges:
        charges = table_charges
    else:
        charges = ocr_charges

    for charge in charges:
        if not charge.currency:
            charge.currency = document_currency
        if not charge.payment_term:
            charge.payment_term = document_payment_term

    result.charges = charges
    result.total_amount = total_amount
    result.total_currency = total_currency or document_currency
    result.evidence = {
        key: value
        for key, value in {
            "reference_no": result.reference_no,
            "document_date": result.document_date,
            "bl_no": result.bl_no,
            "hbl_no": result.hbl_no,
            "booking_no": result.booking_no,
            "total": str(result.total_amount) if result.total_amount else None,
        }.items()
        if value
    }
    return result
