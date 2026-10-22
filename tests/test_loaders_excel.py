from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from shipping_agent.assets import LocalAssetStore
from shipping_agent.loaders.router import route_path


def test_parse_xlsx_with_header(tmp_path: Path) -> None:
    path = tmp_path / "bill.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Charges"
    sheet.append(["单据号", "提单号", "费用名称", "金额", "币种", "付款方式"])
    sheet.append(["DC-001", "BL-001", "HANDLING FEE", 65, "USD", "CC"])
    sheet.append(["DC-001", "BL-001", "DOC FEE", 35, "USD", "CC"])
    workbook.save(path)

    store = LocalAssetStore(tmp_path / "assets")
    doc = route_path(path, store)

    assert doc.source_type.value == "excel"
    assert "HANDLING FEE" in (doc.text or "")
    assert "DOC FEE" in (doc.text or "")
    assert len(doc.tables) == 1
    assert doc.metadata["sheet_count"] == 1
    headers = doc.metadata["sheets"][0]["column_mapping"]
    assert headers["reference_no"] == 0
    assert headers["charge_item"] == 2
    assert headers["amount"] == 3
    assert headers["currency"] == 4
