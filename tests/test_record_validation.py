from __future__ import annotations

import pytest

from decimal import Decimal
from pathlib import Path

from shipping_agent.assets import LocalAssetStore
from shipping_agent.config import load_settings
from shipping_agent.extractors.record import build_record
from shipping_agent.extractors.rules import extract_with_rules
from shipping_agent.loaders.pdf_image import parse_pdf_image
from shipping_agent.models import ExtractedCharge, ExtractionResult
from shipping_agent.rules.registry import load_rules
from shipping_agent.validation.rules import validate_record

SAMPLES = Path(__file__).resolve().parents[1] / "data" / "samples"

SAMPLES_AVAILABLE = SAMPLES.exists() and any(SAMPLES.iterdir())
pytestmark = pytest.mark.skipif(not SAMPLES_AVAILABLE, reason="confidential samples not present")


def _pdf2_record(tmp_path: Path):
    store = LocalAssetStore(tmp_path / "assets")
    doc = parse_pdf_image(SAMPLES / "PDF 示例账单2.pdf", store, use_ocr=False)
    rules = load_rules()
    settings = load_settings()
    extraction = extract_with_rules(doc, rules)
    record = build_record(doc, extraction, rules, settings)
    return doc, rules, settings, record


def test_build_and_validate_pdf2(tmp_path: Path) -> None:
    doc, rules, settings, record = _pdf2_record(tmp_path)

    assert all(charge.alias_version == rules.alias_version for charge in record.charges)
    assert [charge.std_name for charge in record.charges] == [
        "Handling Charge",
        "Detention Free Time Charge",
        "Handling Charge",
    ]

    issues = validate_record(record, rules, settings, doc)
    codes = {issue.code for issue in issues}
    assert "TOTAL_MISMATCH" not in codes
    assert "SAME_NAME_DIFFERENT_AMOUNT" in codes
    assert record.confidence == 0.9


def test_credit_note_amount_is_negative(tmp_path: Path) -> None:
    doc, rules, settings, _ = _pdf2_record(tmp_path)
    extraction = ExtractionResult(
        document_type="Credit Note",
        charges=[ExtractedCharge(raw_name="HANDLING FEE", amount=Decimal("20.00"))],
    )
    record = build_record(doc, extraction, rules, settings)
    assert record.charges[0].amount == Decimal("-20.00")


def test_missing_currency_is_hard_issue(tmp_path: Path) -> None:
    doc, rules, settings, _ = _pdf2_record(tmp_path)
    extraction = ExtractionResult(
        charges=[ExtractedCharge(raw_name="HANDLING FEE", amount=Decimal("10.00"))]
    )
    record = build_record(doc, extraction, rules, settings)
    issues = validate_record(record, rules, settings, doc)

    assert any(
        issue.code == "CURRENCY_MISSING" and issue.severity == "hard"
        for issue in issues
    )


def test_total_mismatch_is_hard_issue(tmp_path: Path) -> None:
    doc, rules, settings, record = _pdf2_record(tmp_path)
    record.total_amount = Decimal("999.00")
    issues = validate_record(record, rules, settings, doc)

    assert any(
        issue.code == "TOTAL_MISMATCH" and issue.severity == "hard"
        for issue in issues
    )


def test_llm_confidence_and_conflict_affect_validation(
    tmp_path: Path,
) -> None:
    doc, rules, settings, record = _pdf2_record(tmp_path)
    record.llm_used = True
    record.llm_confidence = 0.2
    record.llm_evidence_coverage = 0.2
    record.extraction_conflicts = ["issuer: rule=A llm=B"]

    issues = validate_record(record, rules, settings, doc)
    codes = {issue.code for issue in issues}

    assert "LLM_LOW_CONFIDENCE" in codes
    assert "LLM_LOW_EVIDENCE" in codes
    assert "EXTRACTION_CONFLICT" in codes
    assert record.confidence < 0.9
    assert any(
        issue.code == "EXTRACTION_CONFLICT" and issue.review_required
        for issue in issues
    )
