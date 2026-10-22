from __future__ import annotations

import pytest

from pathlib import Path

from shipping_agent.assets import LocalAssetStore
from shipping_agent.extractors.context import build_extraction_context
from shipping_agent.loaders.pdf_image import parse_pdf_image
from shipping_agent.rules.registry import load_rules

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"

SAMPLES_AVAILABLE = SAMPLES.exists() and any(SAMPLES.iterdir())
pytestmark = pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")


def test_build_context_from_pdf(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_pdf_image(SAMPLES / "PDF 示例账单2.pdf", store, use_ocr=False)
    context = build_extraction_context(doc, load_rules())

    assert context.source_type == "pdf"
    assert context.document_type_hint == "Debit Note"
    assert "DC-108478" in context.text
    assert context.tables
    rendered = context.render()
    assert "# Document" in rendered
    assert "# Text" in rendered
    assert "DC-108478" in rendered
