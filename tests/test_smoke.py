from __future__ import annotations

from pathlib import Path

import pytest

from shipping_agent.assets import LocalAssetStore, sniff_source_type
from shipping_agent.config import load_settings
from shipping_agent.db.connection import init_db
from shipping_agent.models import (
    AuditTrail,
    Operation,
    OutputCharge,
    OutputDocumentInfo,
    OutputFinancialAttributes,
    OutputParty,
    OutputRecord,
    SourceType,
)

SCHEMA = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "shipping_agent"
    / "db"
    / "schema.sql"
)


def test_settings_load() -> None:
    settings = load_settings()
    assert settings.project_root.exists()
    assert settings.accounting_entity
    assert settings.ocr_url.endswith("/process")


def test_output_model_is_strict_and_numeric() -> None:
    record = OutputRecord(
        operation=Operation.CREATE,
        document_info=OutputDocumentInfo(
            document_type="Debit Note",
            reference_no="DC-001",
            document_date="2026-01-01",
        ),
        transaction_party=OutputParty(issuer="A", recipient="B"),
        charge_details=OutputCharge(
            charge_item="Handling Charge",
            amount=65.0,
            currency="USD",
            payment_term="CC",
        ),
        financial_attributes=OutputFinancialAttributes(
            accounting_entity="A", posting_type="AR"
        ),
        audit_trail=AuditTrail(ai_reasoning=["test"]),
    )
    data = record.model_dump(mode="json")
    assert data["operation"] == "CREATE"
    assert isinstance(data["charge_details"]["amount"], float)
    assert set(data) == {
        "operation",
        "document_info",
        "transaction_party",
        "charge_details",
        "financial_attributes",
        "audit_trail",
    }


def test_init_db(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db", SCHEMA)
    tables = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "assets",
        "documents",
        "charges",
        "existing_bills",
        "operations",
        "review_items",
        "runs",
    } <= tables
    conn.close()


def test_sniff_source_type_by_magic_bytes() -> None:
    assert sniff_source_type("unknown.bin", "application/octet-stream", b"%PDF-1.4 test") is SourceType.PDF
    assert sniff_source_type("unknown.bin", "application/octet-stream", b"\x89PNG\r\n\x1a\nxxx") is SourceType.IMAGE
    assert sniff_source_type("bill.pdf", "application/octet-stream") is SourceType.PDF


def test_local_asset_store(tmp_path: Path) -> None:
    source = tmp_path / "bill.pdf"
    source.write_bytes(b"%PDF-1.4 test")
    store = LocalAssetStore(tmp_path / "assets")

    asset = store.put(source)
    assert store.get(asset.asset_id) == b"%PDF-1.4 test"
    assert asset.mime_type == "application/pdf"
    assert store.meta(asset.asset_id)["sha256"] == asset.sha256

    with pytest.raises(RuntimeError):
        store.url(asset.asset_id)


def test_graph_smoke() -> None:
    from shipping_agent.graph.build import build_graph

    graph = build_graph()
    result = graph.invoke(
        {
            "run_id": "RUN-TEST",
            "input_paths": ["a.pdf"],
            "pending_files": ["a.pdf"],
            "processed_files": [],
            "trace": [],
        }
    )
    assert result["processed_files"] == ["a.pdf"]
    assert result["trace"][0]["node"] == "ingest"
    assert result["trace"][-1]["node"] == "finalize"
