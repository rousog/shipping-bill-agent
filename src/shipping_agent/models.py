"""Pydantic 数据模型。

分两层：
- Internal*：内部完整模型，保留匹配键、原始费用名、置信度、来源等；
- Output*：严格按题目要求的 JSON schema，一条费用一条记录。
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class SourceType(StrEnum):
    EML = "eml"
    PDF = "pdf"
    IMAGE = "image"
    EXCEL = "excel"
    WORD = "word"
    JSON = "json"
    OTHER = "other"


class DocumentType(StrEnum):
    DEBIT_NOTE = "Debit Note"
    CREDIT_NOTE = "Credit Note"
    INVOICE = "Invoice"
    ARRIVAL_NOTICE = "Arrival Notice"
    PRE_ALERT = "Pre-Alert"
    OTHER = "Other"


class Operation(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    NO_CHANGE = "NO_CHANGE"
    PENDING_REVIEW = "PENDING_REVIEW"


class PostingType(StrEnum):
    AR = "AR"
    AP = "AP"


class PaymentTerm(StrEnum):
    PP = "PP"
    CC = "CC"


# ---------------------------------------------------------------------------
# 资产 / 解析中间表示
# ---------------------------------------------------------------------------


class AssetRef(BaseModel):
    asset_id: str
    sha256: str
    file_name: str
    mime_type: str | None = None
    size_bytes: int | None = None
    storage_backend: str = "local"
    storage_key: str
    parent_asset_id: str | None = None
    source_type: SourceType | None = None
    is_inline: bool = False
    metadata: dict = Field(default_factory=dict)


class RawDocument(BaseModel):
    doc_id: str
    asset_id: str
    source_type: SourceType
    text: str | None = None
    tables: list[list[list[str]]] = Field(default_factory=list)
    metadata: dict = Field(default_factory=dict)
    attachments: list["RawDocument"] = Field(default_factory=list)
    is_inline: bool = False
    parse_notes: list[str] = Field(default_factory=list)


RawDocument.model_rebuild()


class ValidationIssue(BaseModel):
    code: str
    severity: Literal["hard", "soft"]
    message: str
    charge_id: str | None = None
    field: str | None = None
    evidence: str | None = None
    review_required: bool = False


class OCRCharge(BaseModel):
    raw_name: str | None = None
    amount: Decimal | None = None
    currency: str | None = None
    payment_term: str | None = None
    raw: dict = Field(default_factory=dict)


class OCRResult(BaseModel):
    file_type: str | None = None
    order_type: str | None = None
    shipper: str | None = None
    consignee: str | None = None
    mbl_number: str | None = None
    invoice_number: str | None = None
    text: str | None = None
    page_texts: list[str] = Field(default_factory=list)
    charges: list[OCRCharge] = Field(default_factory=list)
    containers: list[str] = Field(default_factory=list)
    suspicious: bool = False
    requires_review: bool = False
    raw: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 抽取结果（规则 / LLM 共用）
# ---------------------------------------------------------------------------


class ExtractedCharge(BaseModel):
    raw_name: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None
    currency: str | None = None
    payment_term: str | None = None
    evidence: str | None = None


class ExtractionResult(BaseModel):
    document_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None
    issuer: str | None = None
    recipient: str | None = None
    shipper: str | None = None
    consignee: str | None = None
    bl_no: str | None = None
    hbl_no: str | None = None
    booking_no: str | None = None
    container_nos: list[str] = Field(default_factory=list)
    posting_type_candidate: Literal["AR", "AP"] | None = None
    ar_ap_reasoning: str | None = None
    # LLM 自评置信度，仅作为弱信号；最终置信度由校验层结合证据/规则计算
    confidence: float | None = None
    charges: list[ExtractedCharge] = Field(default_factory=list)
    total_amount: Decimal | None = None
    total_currency: str | None = None
    evidence: dict[str, str] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 内部账单模型
# ---------------------------------------------------------------------------


class ChargeItem(BaseModel):
    charge_id: str | None = None
    line_no: int | None = None
    raw_name: str | None = None
    std_name: str | None = None
    alias_version: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None = None  # Credit Note 用负数
    currency: str | None = None
    payment_term: PaymentTerm | None = None
    evidence: str | None = None
    confidence: float = 0.0
    issues: list[ValidationIssue] = Field(default_factory=list)


class DocumentInfo(BaseModel):
    document_type: DocumentType | None = None
    reference_no: str | None = None
    document_date: date | None = None

    # 以下字段只用于内部匹配，不进入输出 schema
    bl_no: str | None = None
    hbl_no: str | None = None
    booking_no: str | None = None
    invoice_no: str | None = None
    container_nos: list[str] = Field(default_factory=list)


class PartyInfo(BaseModel):
    issuer: str | None = None
    recipient: str | None = None
    shipper: str | None = None
    consignee: str | None = None


class FinancialAttributes(BaseModel):
    accounting_entity: str | None = None
    posting_type: PostingType | None = None
    ar_ap_reasoning: str | None = None


class InternalBillRecord(BaseModel):
    record_id: str
    source: AssetRef | None = None
    document_info: DocumentInfo = Field(default_factory=DocumentInfo)
    transaction_party: PartyInfo = Field(default_factory=PartyInfo)
    charges: list[ChargeItem] = Field(default_factory=list)
    total_amount: Decimal | None = None
    total_currency: str | None = None
    financial_attributes: FinancialAttributes = Field(
        default_factory=FinancialAttributes
    )
    confidence: float = 0.0
    issues: list[ValidationIssue] = Field(default_factory=list)

    # LLM 参与度与信号（只用于内部校验/审计，不进入输出 schema）
    llm_used: bool = False
    llm_confidence: float | None = None
    llm_evidence_coverage: float | None = None
    extraction_conflicts: list[str] = Field(default_factory=list)


class ExistingBill(BaseModel):
    bill_id: str
    version: int = 1
    document_info: DocumentInfo = Field(default_factory=DocumentInfo)
    transaction_party: PartyInfo = Field(default_factory=PartyInfo)
    charges: list[ChargeItem] = Field(default_factory=list)
    total_amount: Decimal | None = None
    total_currency: str | None = None
    financial_attributes: FinancialAttributes = Field(
        default_factory=FinancialAttributes
    )
    updated_at: str | None = None


# ---------------------------------------------------------------------------
# 匹配 / 操作 / 人工复核
# ---------------------------------------------------------------------------


class MatchResult(BaseModel):
    charge_id: str | None = None
    bill_id: str | None = None
    level: str | None = None
    candidates: list[str] = Field(default_factory=list)
    ambiguous: bool = False
    reason: str | None = None


class OperationRecord(BaseModel):
    operation_id: str
    charge_id: str | None = None
    matched_bill_id: str | None = None
    operation: Operation
    changed_fields: list[str] = Field(default_factory=list)
    output: "OutputRecord | None" = None
    reasons: list[str] = Field(default_factory=list)


class ReviewItem(BaseModel):
    review_id: str
    charge_id: str | None = None
    reason: str
    candidates: list[str] = Field(default_factory=list)
    status: Literal["pending", "accepted", "rejected", "updated"] = "pending"
    decision: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 严格输出模型（题目 schema）
# ---------------------------------------------------------------------------


class OutputDocumentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str | None
    reference_no: str | None
    document_date: str | None


class OutputParty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer: str | None
    recipient: str | None


class OutputCharge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charge_item: str | None
    amount: float | None
    currency: str | None
    payment_term: str | None


class OutputFinancialAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounting_entity: str | None
    posting_type: str | None


class AuditTrail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_reasoning: list[str] = Field(default_factory=list)


class OutputRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Operation
    document_info: OutputDocumentInfo
    transaction_party: OutputParty
    charge_details: OutputCharge
    financial_attributes: OutputFinancialAttributes
    audit_trail: AuditTrail


OperationRecord.model_rebuild()
