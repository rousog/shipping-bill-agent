from __future__ import annotations

import pytest

from datetime import date
from decimal import Decimal
from pathlib import Path

from shipping_agent.assets import LocalAssetStore
from shipping_agent.config import load_settings
from shipping_agent.db.connection import init_db
from shipping_agent.db.repositories import (
    OperationRepository,
    ReviewRepository,
    SqliteBillRepository,
)
from shipping_agent.decide import DecisionEngine
from shipping_agent.extractors.record import build_record
from shipping_agent.extractors.rules import extract_with_rules
from shipping_agent.loaders.pdf_image import parse_pdf_image
from shipping_agent.models import (
    ChargeItem,
    DocumentInfo,
    DocumentType,
    ExistingBill,
    FinancialAttributes,
    InternalBillRecord,
    Operation,
    PartyInfo,
    PaymentTerm,
    PostingType,
    RawDocument,
    SourceType,
)
from shipping_agent.rules.registry import load_rules
from shipping_agent.validation.rules import validate_record

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"

SAMPLES_AVAILABLE = SAMPLES.exists() and any(SAMPLES.iterdir())
pytestmark = pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")
SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "shipping_agent"
    / "db"
    / "schema.sql"
)


def _record(tmp_path: Path):
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_pdf_image(SAMPLES / "PDF 示例账单2.pdf", store, use_ocr=False)
    rules = load_rules()
    settings = load_settings()
    extraction = extract_with_rules(doc, rules)
    record = build_record(doc, extraction, rules, settings)
    validate_record(record, rules, settings, doc)
    return doc, record, rules, settings


def _engine(tmp_path: Path):
    conn = init_db(tmp_path / "test.db", SCHEMA)
    bill_repo = SqliteBillRepository(conn)
    engine = DecisionEngine(
        bill_repository=bill_repo,
        operation_repository=OperationRepository(conn),
        review_repository=ReviewRepository(conn),
        settings=load_settings(),
    )
    return conn, bill_repo, engine


def _bill(amounts: tuple[Decimal, Decimal, Decimal], bill_id: str = "BILL-DC-108478") -> ExistingBill:
    charges = [
        ChargeItem(
            charge_id=f"{bill_id}-CHG-001",
            line_no=1,
            raw_name="HANDLING CHARGE",
            std_name="Handling Charge",
            amount=amounts[0],
            currency="USD",
            payment_term=PaymentTerm.CC,
        ),
        ChargeItem(
            charge_id=f"{bill_id}-CHG-002",
            line_no=2,
            raw_name="4 ADD'L DETENTION FREE TIME CHARGE",
            std_name="Detention Free Time Charge",
            amount=amounts[1],
            currency="USD",
            payment_term=PaymentTerm.CC,
        ),
        ChargeItem(
            charge_id=f"{bill_id}-CHG-003",
            line_no=3,
            raw_name="HANDLING CHARGE",
            std_name="Handling Charge",
            amount=amounts[2],
            currency="USD",
            payment_term=PaymentTerm.CC,
        ),
    ]
    return ExistingBill(
        bill_id=bill_id,
        version=1,
        document_info=DocumentInfo(
            document_type=DocumentType.DEBIT_NOTE,
            reference_no="DC-108478",
            document_date=date(2026, 10, 24),
            bl_no="MAEU275752922",
            hbl_no="CGT260801188",
            container_nos=["MRSU2571577", "TCLU8619685"],
        ),
        transaction_party=PartyInfo(
            issuer="湃乐多（上海）航运科技有限公司",
            recipient="AEBO TCO IMPORT LLC",
        ),
        charges=charges,
        total_amount=sum(amounts, Decimal("0")),
        total_currency="USD",
        financial_attributes=FinancialAttributes(
            accounting_entity=load_settings().accounting_entity,
            posting_type=PostingType.AR,
        ),
    )


def test_create_when_no_existing_bill(tmp_path: Path) -> None:
    doc, record, _, _ = _record(tmp_path)
    conn, bill_repo, engine = _engine(tmp_path)

    result = engine.decide(record, doc, "RUN-CREATE")

    assert [op.operation for op in result.operations] == [
        Operation.CREATE,
        Operation.CREATE,
        Operation.CREATE,
    ]
    created = bill_repo.get("BILL-DC-108478")
    assert created is not None
    assert len(created.charges) == 3
    assert created.total_amount == Decimal("1410.40")
    conn.close()


def test_no_change_with_exact_seed(tmp_path: Path) -> None:
    doc, record, _, _ = _record(tmp_path)
    conn, bill_repo, engine = _engine(tmp_path)
    bill_repo.upsert(_bill((Decimal("65.00"), Decimal("1310.40"), Decimal("35.00"))))

    result = engine.decide(record, doc, "RUN-NOCHANGE")

    assert all(op.operation == Operation.NO_CHANGE for op in result.operations)
    conn.close()


def test_update_when_amount_differs(tmp_path: Path) -> None:
    doc, record, _, _ = _record(tmp_path)
    conn, bill_repo, engine = _engine(tmp_path)
    bill_repo.upsert(_bill((Decimal("65.00"), Decimal("1300.00"), Decimal("35.00"))))

    result = engine.decide(record, doc, "RUN-UPDATE")

    assert result.operations[1].operation == Operation.UPDATE
    assert result.operations[1].changed_fields == ["amount"]
    updated = bill_repo.get("BILL-DC-108478")
    assert updated is not None
    assert updated.version == 2
    assert updated.charges[1].amount == Decimal("1310.40")
    conn.close()


def test_ambiguous_match_goes_to_review(tmp_path: Path) -> None:
    doc, record, _, _ = _record(tmp_path)
    conn, bill_repo, engine = _engine(tmp_path)
    bill_repo.upsert(_bill((Decimal("65.00"), Decimal("1310.40"), Decimal("35.00")), "BILL-A"))
    bill_repo.upsert(_bill((Decimal("65.00"), Decimal("1310.40"), Decimal("35.00")), "BILL-B"))

    result = engine.decide(record, doc, "RUN-AMBIGUOUS")

    assert all(op.operation == Operation.PENDING_REVIEW for op in result.operations)
    conn.close()


def test_ar_ap_unverified_soft_triggers_review(tmp_path: Path) -> None:
    conn, _, engine = _engine(tmp_path)
    doc = RawDocument(
        doc_id="DOC-ARAP",
        asset_id="ASSET-ARAP",
        source_type=SourceType.EML,
        text="",
        metadata={"file_name": "test.eml"},
    )
    record = InternalBillRecord(
        record_id="REC-ARAP",
        document_info=DocumentInfo(
            document_type=DocumentType.OTHER,
            reference_no="REF-ARAP",
        ),
        transaction_party=PartyInfo(
            issuer="NEPTUNE SHIPPING LIMITED",
            recipient="ZIM INTEGRATED SHIPPING",
        ),
        charges=[
            ChargeItem(
                charge_id="CHG-ARAP",
                std_name="Handling Charge",
                amount=Decimal("10.00"),
                currency="USD",
                payment_term=PaymentTerm.CC,
            )
        ],
        financial_attributes=FinancialAttributes(
            accounting_entity=load_settings().accounting_entity,
            posting_type=PostingType.AP,
        ),
        confidence=1.0,
    )

    result = engine.decide(record, doc, "RUN-ARAP")

    assert result.operations[0].operation == Operation.PENDING_REVIEW
    assert result.operations[0].output.financial_attributes.posting_type == "AP"
    conn.close()


def test_output_is_strict_schema(tmp_path: Path) -> None:
    doc, record, _, _ = _record(tmp_path)
    conn, _, engine = _engine(tmp_path)

    result = engine.decide(record, doc, "RUN-SCHEMA")
    payload = result.operations[0].output.model_dump(mode="json")

    assert set(payload) == {
        "operation",
        "document_info",
        "transaction_party",
        "charge_details",
        "financial_attributes",
        "audit_trail",
    }
    assert isinstance(payload["charge_details"]["amount"], float)
    conn.close()
