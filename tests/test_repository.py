from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from shipping_agent.db.connection import init_db
from shipping_agent.db.repositories import (
    ReviewRepository,
    SqliteBillRepository,
)
from shipping_agent.models import (
    ChargeItem,
    DocumentInfo,
    DocumentType,
    ExistingBill,
    FinancialAttributes,
    PartyInfo,
    PaymentTerm,
    PostingType,
    ReviewItem,
)

SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "shipping_agent"
    / "db"
    / "schema.sql"
)


def _bill() -> ExistingBill:
    return ExistingBill(
        bill_id="BILL-001",
        document_info=DocumentInfo(
            document_type=DocumentType.DEBIT_NOTE,
            reference_no="DC-001",
            document_date=date(2026, 1, 1),
            bl_no="BL-001",
            booking_no="BK-001",
            container_nos=["TIIU8975975"],
        ),
        transaction_party=PartyInfo(issuer="Pilot Aiship", recipient="Customer"),
        charges=[
            ChargeItem(
                std_name="Handling Charge",
                amount=Decimal("65.00"),
                currency="USD",
                payment_term=PaymentTerm.CC,
            )
        ],
        financial_attributes=FinancialAttributes(
            accounting_entity="Pilot Aiship",
            posting_type=PostingType.AR,
        ),
    )


def test_bill_upsert_get_and_match(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db", SCHEMA)
    repo = SqliteBillRepository(conn)

    repo.upsert(_bill())
    loaded = repo.get("BILL-001")

    assert loaded is not None
    assert loaded.document_info.reference_no == "DC-001"
    assert loaded.document_info.container_nos == ["TIIU8975975"]
    assert loaded.charges[0].std_name == "Handling Charge"
    assert loaded.charges[0].amount == Decimal("65.0")

    assert repo.list_candidates(
        {"reference_no": "DC-001", "std_name": "Handling Charge"}
    ) == ["BILL-001"]
    assert repo.list_candidates(
        {
            "bl_no": "BL-001",
            "std_name": "Handling Charge",
            "currency": "USD",
        }
    ) == ["BILL-001"]
    conn.close()


def test_review_queue(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db", SCHEMA)
    repo = ReviewRepository(conn)

    repo.enqueue(
        "RUN-1",
        ReviewItem(
            review_id="REV-1",
            charge_id="CHG-1",
            reason="counterparty unclear",
            candidates=["BILL-001"],
        ),
    )
    pending = repo.list_pending()
    assert len(pending) == 1
    assert pending[0].review_id == "REV-1"

    repo.resolve("REV-1", "accepted", {"action": "accept"})
    assert repo.list_pending() == []
    conn.close()
