"""黄金用例：用 PLTP 给的样例做端到端回归。

说明：
- 固定输入 + 人工确认过的关键字段；
- 只比较稳定字段（单据号、费用、金额、币种、操作类型）；
- 不比较 operation_id、audit 文案、时间戳等易变字段；
- 图片样例依赖 OCR，单独在 loader 测试里覆盖。
"""

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
from shipping_agent.loaders.router import route_path
from shipping_agent.models import (
    ChargeItem,
    DocumentInfo,
    DocumentType,
    ExistingBill,
    FinancialAttributes,
    PartyInfo,
    PaymentTerm,
    PostingType,
)
from shipping_agent.rules.registry import load_rules
from shipping_agent.validation.rules import validate_record

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = ROOT / "data" / "samples"
SYNTHETIC = ROOT / "tests" / "fixtures" / "synthetic"
SAMPLES_AVAILABLE = SAMPLES.exists() and any(SAMPLES.iterdir())
SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "shipping_agent"
    / "db"
    / "schema.sql"
)


def _prepare(tmp_path: Path, path: Path):
    store = LocalAssetStore(tmp_path / "assets")
    doc = route_path(path, store, use_ocr=False)
    rules = load_rules()
    settings = load_settings()
    extraction = extract_with_rules(doc, rules)
    record = build_record(doc, extraction, rules, settings)
    validate_record(record, rules, settings, doc)
    conn = init_db(tmp_path / "test.db", SCHEMA)
    bill_repo = SqliteBillRepository(conn)
    engine = DecisionEngine(
        bill_repository=bill_repo,
        operation_repository=OperationRepository(conn),
        review_repository=ReviewRepository(conn),
        settings=settings,
    )
    return doc, record, bill_repo, engine, conn


def _seed_pdf2(bill_repo: SqliteBillRepository, detention: str) -> None:
    amounts = {
        "h1": Decimal("65.00"),
        "detention": Decimal(detention),
        "h2": Decimal("35.00"),
    }
    charges = [
        ChargeItem(
            charge_id="BILL-DC-108478-CHG-001",
            line_no=1,
            raw_name="HANDLING CHARGE",
            std_name="Handling Charge",
            amount=amounts["h1"],
            currency="USD",
            payment_term=PaymentTerm.CC,
        ),
        ChargeItem(
            charge_id="BILL-DC-108478-CHG-002",
            line_no=2,
            raw_name="4 ADD'L DETENTION FREE TIME CHARGE",
            std_name="Detention Free Time Charge",
            amount=amounts["detention"],
            currency="USD",
            payment_term=PaymentTerm.CC,
        ),
        ChargeItem(
            charge_id="BILL-DC-108478-CHG-003",
            line_no=3,
            raw_name="HANDLING CHARGE",
            std_name="Handling Charge",
            amount=amounts["h2"],
            currency="USD",
            payment_term=PaymentTerm.CC,
        ),
    ]
    bill_repo.upsert(
        ExistingBill(
            bill_id="BILL-DC-108478",
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
            total_amount=sum(
                (charge.amount for charge in charges), Decimal("0")
            ),
            total_currency="USD",
            financial_attributes=FinancialAttributes(
                accounting_entity=load_settings().accounting_entity,
                posting_type=PostingType.AR,
            ),
        )
    )


def _charge_keys(record) -> list[tuple]:
    return [
        (
            charge.std_name,
            str(charge.amount),
            charge.currency,
            charge.payment_term.value if charge.payment_term else None,
        )
        for charge in record.charges
    ]


@pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")
def test_golden_pdf2_create(tmp_path: Path) -> None:
    doc, record, _, engine, conn = _prepare(
        tmp_path, SAMPLES / "PDF 示例账单2.pdf"
    )
    result = engine.decide(record, doc, "GOLDEN-PDF2-CREATE")

    assert [op.operation.value for op in result.operations] == [
        "CREATE",
        "CREATE",
        "CREATE",
    ]
    assert record.document_info.document_type.value == "Debit Note"
    assert record.document_info.reference_no == "DC-108478"
    assert record.document_info.bl_no == "MAEU275752922"
    assert record.total_amount == Decimal("1410.40")
    assert _charge_keys(record) == [
        ("Handling Charge", "65.00", "USD", "CC"),
        ("Detention Free Time Charge", "1310.40", "USD", "CC"),
        ("Handling Charge", "35.00", "USD", "CC"),
    ]
    conn.close()


@pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")
def test_golden_pdf2_no_change(tmp_path: Path) -> None:
    doc, record, bill_repo, engine, conn = _prepare(
        tmp_path, SAMPLES / "PDF 示例账单2.pdf"
    )
    _seed_pdf2(bill_repo, "1310.40")

    result = engine.decide(record, doc, "GOLDEN-PDF2-NOCHANGE")

    assert all(op.operation.value == "NO_CHANGE" for op in result.operations)
    conn.close()


@pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")
def test_golden_pdf2_update(tmp_path: Path) -> None:
    doc, record, bill_repo, engine, conn = _prepare(
        tmp_path, SAMPLES / "PDF 示例账单2.pdf"
    )
    _seed_pdf2(bill_repo, "1300.00")

    result = engine.decide(record, doc, "GOLDEN-PDF2-UPDATE")

    assert [op.operation.value for op in result.operations] == [
        "NO_CHANGE",
        "UPDATE",
        "NO_CHANGE",
    ]
    assert result.operations[1].changed_fields == ["amount"]
    conn.close()


@pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")
def test_golden_pdf1_pending(tmp_path: Path) -> None:
    doc, record, _, engine, conn = _prepare(
        tmp_path, SAMPLES / "PDF示例账单1.pdf"
    )
    result = engine.decide(record, doc, "GOLDEN-PDF1")

    assert len(result.operations) == 1
    assert result.operations[0].operation.value == "PENDING_REVIEW"
    assert _charge_keys(record) == [
        ("On Carriage Charge", "245.0000", "USD", "CC")
    ]
    assert any(
        issue.code == "AR_AP_UNKNOWN" for issue in record.issues
    )
    conn.close()


@pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")
def test_golden_eml_pending(tmp_path: Path) -> None:
    doc, record, _, engine, conn = _prepare(
        tmp_path, next(SAMPLES.glob("*.eml"))
    )
    result = engine.decide(record, doc, "GOLDEN-EML")

    assert len(result.operations) == 1
    assert result.operations[0].operation.value == "PENDING_REVIEW"
    assert _charge_keys(record) == [
        ("Handling Charge", "50", "USD", "PP")
    ]
    assert any(
        issue.code == "AR_AP_INTERNAL" for issue in record.issues
    )
    conn.close()


def test_golden_excel_charges(tmp_path: Path) -> None:
    doc, record, _, engine, conn = _prepare(
        tmp_path, SYNTHETIC / "charges.xlsx"
    )
    result = engine.decide(record, doc, "GOLDEN-XLSX")

    assert _charge_keys(record) == [
        ("Handling Charge", "65", "USD", "CC"),
        ("Documentation Fee", "35", "USD", "CC"),
    ]
    assert len(result.operations) == 2
    conn.close()


def test_golden_word_charges(tmp_path: Path) -> None:
    doc, record, _, engine, conn = _prepare(
        tmp_path, SYNTHETIC / "debit_note.docx"
    )
    result = engine.decide(record, doc, "GOLDEN-DOCX")

    assert record.document_info.document_type.value == "Debit Note"
    assert _charge_keys(record) == [
        ("Handling Charge", "65.00", "USD", "CC")
    ]
    assert len(result.operations) == 1
    conn.close()
