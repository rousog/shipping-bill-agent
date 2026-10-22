from __future__ import annotations

import pytest

from decimal import Decimal
from pathlib import Path

from shipping_agent.assets import LocalAssetStore
from shipping_agent.loaders.pdf_image import parse_pdf_image
from shipping_agent.models import OCRCharge, OCRResult

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"

SAMPLES_AVAILABLE = SAMPLES.exists() and any(SAMPLES.iterdir())
pytestmark = pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")


def test_parse_text_pdf(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_pdf_image(
        SAMPLES / "PDF 示例账单2.pdf", store, use_ocr=False
    )

    assert "DC-108478" in (doc.text or "")
    assert doc.metadata["page_count"] == 1
    assert doc.metadata["has_text_layer"] is True
    assert doc.metadata["is_scanned"] is False
    assert doc.tables


def test_parse_arrival_notice_pdf(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_pdf_image(
        SAMPLES / "PDF示例账单1.pdf", store, use_ocr=False
    )

    assert "CMDUCHN4053601" in (doc.text or "")
    assert doc.metadata["page_count"] == 3


class FakeOCRClient:
    def process_asset(self, asset_id: str, asset_store) -> OCRResult:
        return OCRResult(
            file_type="INVOICE",
            text="ZIM INVOICE ORFR1618731 WHARFAGE 21.90",
            charges=[
                OCRCharge(
                    raw_name="WHARFAGE",
                    amount=Decimal("21.90"),
                    currency="USD",
                )
            ],
            containers=["JXLU4504463"],
        )


def test_parse_image_with_ocr(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_pdf_image(
        SAMPLES / "图片示例.png",
        store,
        ocr_client=FakeOCRClient(),
        use_ocr=True,
    )

    assert doc.source_type.value == "image"
    assert "ZIM INVOICE" in (doc.text or "")
    assert doc.metadata["ocr_used"] is True
    assert doc.metadata["ocr_charge_count"] == 1
    assert doc.metadata["ocr_file_type"] == "INVOICE"


def test_parse_image_without_ocr(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_pdf_image(
        SAMPLES / "图片示例.png", store, ocr_client=None, use_ocr=True
    )

    assert doc.text is None
    assert any("OCR client 未配置" in note for note in doc.parse_notes)
