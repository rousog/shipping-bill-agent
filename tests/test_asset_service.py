from __future__ import annotations

import pytest

from pathlib import Path

from shipping_agent.asset_service import AssetService
from shipping_agent.assets import LocalAssetStore
from shipping_agent.db.connection import init_db
from shipping_agent.db.repositories import AssetRepository
from shipping_agent.loaders.eml import parse_eml

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


def test_asset_service_persists_eml_tree(tmp_path: Path) -> None:
    conn = init_db(tmp_path / "test.db", SCHEMA)
    store = LocalAssetStore(tmp_path / "assets")
    service = AssetService(store, AssetRepository(conn))

    doc = parse_eml(next(SAMPLES.glob("*.eml")), service)

    rows = conn.execute("SELECT * FROM assets").fetchall()
    assert len(rows) == 4
    by_sha = {row["sha256"]: row for row in rows}

    root = by_sha[doc.asset_id]
    assert root["parent_asset_id"] is None
    assert root["source_type"] == "eml"

    children = [row for row in rows if row["parent_asset_id"] == doc.asset_id]
    assert len(children) == 3
    assert {row["source_type"] for row in children} == {"pdf", "image"}
    assert any(row["is_inline"] for row in children)

    conn.close()
