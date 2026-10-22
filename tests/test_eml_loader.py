from __future__ import annotations

import pytest

from email.message import EmailMessage
from pathlib import Path

from shipping_agent.assets import LocalAssetStore
from shipping_agent.loaders.eml import parse_eml
from shipping_agent.loaders.router import route_path
from shipping_agent.models import SourceType

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"

SAMPLES_AVAILABLE = SAMPLES.exists() and any(SAMPLES.iterdir())
pytestmark = pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")


def _sample_eml() -> Path:
    return next(SAMPLES.glob("*.eml"))


def test_parse_sample_eml_with_banner_filter(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_eml(_sample_eml(), store)

    assert doc.source_type is SourceType.EML
    assert "PRE-ALERT" in (doc.metadata["subject"] or "")
    assert "H/C $50" in (doc.text or "")

    names = [attachment.metadata["file_name"] for attachment in doc.attachments]
    assert len(doc.attachments) == 2
    assert any("MBL" in name for name in names)
    assert any("HBL" in name for name in names)
    assert all(attachment.source_type is SourceType.PDF for attachment in doc.attachments)
    assert all(attachment.text for attachment in doc.attachments)
    assert any(
        "BILL OF LADING" in (attachment.text or "").upper()
        for attachment in doc.attachments
    )

    skipped = doc.metadata["skipped_inline_images"]
    assert len(skipped) == 1
    assert "InsertPic" in skipped[0]["file_name"]
    assert "banner" in skipped[0]["reason"].lower() or "内嵌" in skipped[0]["reason"]
    assert any("banner" in note for note in doc.parse_notes)


def _nested_eml(tmp_path: Path) -> Path:
    inner = EmailMessage()
    inner["Subject"] = "Inner EML"
    inner["From"] = "a@example.com"
    inner["To"] = "b@example.com"
    inner.set_content("inner body H/C $10")

    outer = EmailMessage()
    outer["Subject"] = "Outer EML"
    outer["From"] = "a@example.com"
    outer["To"] = "b@example.com"
    outer.set_content("outer body")
    outer.add_attachment(inner)

    path = tmp_path / "outer.eml"
    path.write_bytes(outer.as_bytes())
    return path


def test_nested_eml_recursion(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_eml(_nested_eml(tmp_path), store, max_depth=3)

    assert len(doc.attachments) == 1
    child = doc.attachments[0]
    assert child.source_type is SourceType.EML
    assert child.metadata["subject"] == "Inner EML"
    assert "inner body" in (child.text or "")


def test_nested_eml_max_depth(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_eml(_nested_eml(tmp_path), store, max_depth=0)

    assert len(doc.attachments) == 1
    child = doc.attachments[0]
    assert child.metadata.get("max_depth_reached") is True
    assert child.attachments == []


def test_route_pdf_parsed(tmp_path: Path) -> None:
    store = LocalAssetStore(tmp_path / "assets")
    doc = route_path(SAMPLES / "PDF 示例账单2.pdf", store)

    assert doc.source_type is SourceType.PDF
    assert "DC-108478" in (doc.text or "")
    assert doc.metadata["page_count"] == 1
    assert doc.metadata["has_text_layer"] is True
    assert doc.metadata["is_scanned"] is False
