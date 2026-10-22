"""写入一张 PDF2 对应的已有账单，用于演示 NO_CHANGE / UPDATE。

用法：
    python scripts/seed_existing_bills.py
    python scripts/seed_existing_bills.py --variant update

variant:
- exact   : 65 / 1310.40 / 35，跑 PDF2 应得到 NO_CHANGE
- update  : 60 / 1300 / 30，跑 PDF2 应得到部分 UPDATE/PENDING
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from shipping_agent.config import load_settings  # noqa: E402
from shipping_agent.db.connection import init_db, transaction  # noqa: E402
from shipping_agent.db.repositories import SqliteBillRepository  # noqa: E402
from shipping_agent.models import (  # noqa: E402
    ChargeItem,
    DocumentInfo,
    DocumentType,
    ExistingBill,
    FinancialAttributes,
    PartyInfo,
    PaymentTerm,
    PostingType,
)

SCHEMA = Path(__file__).resolve().parents[1] / "src" / "shipping_agent" / "db" / "schema.sql"


def build_bill(variant: str, accounting_entity: str) -> ExistingBill:
    if variant == "update":
        amounts = {
            "handling_1": Decimal("65.00"),
            "detention": Decimal("1300.00"),
            "handling_2": Decimal("35.00"),
        }
    else:
        amounts = {
            "handling_1": Decimal("65.00"),
            "detention": Decimal("1310.40"),
            "handling_2": Decimal("35.00"),
        }

    charges = [
        ChargeItem(
            charge_id="BILL-DC-108478-CHG-001",
            line_no=1,
            raw_name="HANDLING CHARGE",
            std_name="Handling Charge",
            amount=amounts["handling_1"],
            currency="USD",
            payment_term=PaymentTerm.CC,
            confidence=1.0,
        ),
        ChargeItem(
            charge_id="BILL-DC-108478-CHG-002",
            line_no=2,
            raw_name="4 ADD'L DETENTION FREE TIME CHARGE",
            std_name="Detention Free Time Charge",
            amount=amounts["detention"],
            currency="USD",
            payment_term=PaymentTerm.CC,
            confidence=1.0,
        ),
        ChargeItem(
            charge_id="BILL-DC-108478-CHG-003",
            line_no=3,
            raw_name="HANDLING CHARGE",
            std_name="Handling Charge",
            amount=amounts["handling_2"],
            currency="USD",
            payment_term=PaymentTerm.CC,
            confidence=1.0,
        ),
    ]

    return ExistingBill(
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
            shipper="YIWU JINBAIREN IMPORT AND EXPORT CO., LTD",
            consignee="AEBO TCO IMPORT LLC",
        ),
        charges=charges,
        total_amount=sum((charge.amount for charge in charges), Decimal("0")),
        total_currency="USD",
        financial_attributes=FinancialAttributes(
            accounting_entity=accounting_entity,
            posting_type=PostingType.AR,
        ),
    )


def reset_business_tables(conn) -> None:
    with transaction(conn):
        conn.execute("DELETE FROM operations")
        conn.execute("DELETE FROM review_items")
        conn.execute("DELETE FROM validation_issues")
        conn.execute("DELETE FROM existing_bills")
        conn.execute("DELETE FROM charges")
        conn.execute("DELETE FROM containers")
        conn.execute("DELETE FROM documents")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--variant", choices=("exact", "update"), default="exact")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="清空已有账单/费用/操作，保证可重复演示",
    )
    args = parser.parse_args()

    settings = load_settings()
    settings.ensure_directories()
    conn = init_db(settings.db_path, SCHEMA)
    if args.reset:
        reset_business_tables(conn)
    repository = SqliteBillRepository(conn)

    bill = build_bill(args.variant, settings.accounting_entity)
    repository.upsert(bill)
    conn.close()

    print(
        f"Seeded {bill.bill_id} ({args.variant}), "
        f"charges={[str(c.amount) for c in bill.charges]}"
    )


if __name__ == "__main__":
    main()
