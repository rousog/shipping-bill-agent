from __future__ import annotations

import pytest

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from langgraph.types import Command

from shipping_agent.asset_service import AssetService
from shipping_agent.assets import LocalAssetStore
from shipping_agent.config import load_settings
from shipping_agent.db.connection import init_db
from shipping_agent.db.repositories import (
    AssetRepository,
    OperationRepository,
    ReviewRepository,
    SqliteBillRepository,
)
from shipping_agent.decide import DecisionEngine
from shipping_agent.extractors.llm import LLMExtractor
from shipping_agent.graph.checkpoints import sqlite_checkpointer
from shipping_agent.graph.context import GraphContext
from shipping_agent.graph.pipeline import build_pipeline
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


def _context(tmp_path: Path):
    db_path = tmp_path / "graph.db"
    conn = init_db(db_path, SCHEMA)
    settings = load_settings()
    store = LocalAssetStore(tmp_path / "assets")
    asset_service = AssetService(store, AssetRepository(conn))
    bill_repo = SqliteBillRepository(conn)
    engine = DecisionEngine(
        bill_repository=bill_repo,
        operation_repository=OperationRepository(conn),
        review_repository=ReviewRepository(conn),
        settings=settings,
    )
    context = GraphContext(
        settings=settings,
        rules=load_rules(),
        asset_service=asset_service,
        ocr_client=None,
        llm_extractor=LLMExtractor(None),
        engine=engine,
        conn=conn,
        output_path=tmp_path / "output.json",
        summary_path=tmp_path / "summary.json",
        use_ocr=False,
    )
    return context, conn, bill_repo, db_path


def _seed_exact(bill_repo: SqliteBillRepository) -> None:
    amounts = (Decimal("65.00"), Decimal("1310.40"), Decimal("35.00"))
    charges = [
        ChargeItem(
            charge_id=f"BILL-DC-108478-CHG-{index + 1:03d}",
            line_no=index + 1,
            raw_name=raw_name,
            std_name=std_name,
            amount=amount,
            currency="USD",
            payment_term=PaymentTerm.CC,
        )
        for index, (raw_name, std_name, amount) in enumerate(
            [
                ("HANDLING CHARGE", "Handling Charge", amounts[0]),
                (
                    "4 ADD'L DETENTION FREE TIME CHARGE",
                    "Detention Free Time Charge",
                    amounts[1],
                ),
                ("HANDLING CHARGE", "Handling Charge", amounts[2]),
            ]
        )
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
            total_amount=sum(amounts, Decimal("0")),
            total_currency="USD",
            financial_attributes=FinancialAttributes(
                accounting_entity=load_settings().accounting_entity,
                posting_type=PostingType.AR,
            ),
        )
    )


def test_graph_no_pending(tmp_path: Path) -> None:
    context, conn, bill_repo, db_path = _context(tmp_path)
    _seed_exact(bill_repo)

    with sqlite_checkpointer(db_path) as saver:
        graph = build_pipeline(context, checkpointer=saver)
        result = graph.invoke(
            {
                "run_id": "GRAPH-T1",
                "thread_id": "GRAPH-T1",
                "input_paths": [str(SAMPLES / "PDF 示例账单2.pdf")],
                "trace": [],
                "errors": [],
            },
            config={"configurable": {"thread_id": "GRAPH-T1"}},
        )

    assert not result.get("__interrupt__")
    outputs = json.loads(context.output_path.read_text(encoding="utf-8"))
    assert len(outputs) == 3
    assert all(record["operation"] == "NO_CHANGE" for record in outputs)
    conn.close()


def test_graph_interrupt_and_resume(tmp_path: Path) -> None:
    context, conn, _, db_path = _context(tmp_path)

    with sqlite_checkpointer(db_path) as saver:
        graph = build_pipeline(context, checkpointer=saver)
        result = graph.invoke(
            {
                "run_id": "GRAPH-T2",
                "thread_id": "GRAPH-T2",
                "input_paths": [str(SAMPLES / "PDF示例账单1.pdf")],
                "trace": [],
                "errors": [],
            },
            config={"configurable": {"thread_id": "GRAPH-T2"}},
        )
        assert result.get("__interrupt__")

        pending = context.engine.review_repository.list_pending(
            run_id="GRAPH-T2"
        )
        decisions = {
            "decisions": {
                item.charge_id: {"action": "accept"} for item in pending
            }
        }
        result = graph.invoke(
            Command(resume=decisions),
            config={"configurable": {"thread_id": "GRAPH-T2"}},
        )

    outputs = json.loads(context.output_path.read_text(encoding="utf-8"))
    assert len(outputs) == 1
    assert outputs[0]["operation"] == "CREATE"
    assert (
        context.engine.review_repository.list_pending(run_id="GRAPH-T2") == []
    )
    conn.close()
