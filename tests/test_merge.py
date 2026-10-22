from __future__ import annotations

from decimal import Decimal

from shipping_agent.extractors.merge import merge_extraction
from shipping_agent.models import ExtractedCharge, ExtractionResult


def test_merge_ignores_format_only_differences() -> None:
    rule = ExtractionResult(
        reference_no="DC-001",
        issuer="Pilot Aiship Tech co., Ltd.",
        charges=[
            ExtractedCharge(raw_name="HANDLING FEE", amount=Decimal("65.00"))
        ],
    )
    llm = ExtractionResult(
        reference_no="DC-001",
        issuer="Pilot Aiship Tech co., Ltd.(PLTP)",
        charges=[
            ExtractedCharge(raw_name="HANDLING CHARGE", amount=Decimal("65"))
        ],
    )

    merged = merge_extraction(rule, llm, llm_used=True)
    assert merged.conflicts == []


def test_merge_prefers_rule_charges_and_fills_llm_fields() -> None:
    rule = ExtractionResult(
        reference_no="DC-001",
        charges=[
            ExtractedCharge(raw_name="HANDLING CHARGE", amount=Decimal("65.00")),
            ExtractedCharge(raw_name="HANDLING CHARGE", amount=Decimal("35.00")),
        ],
    )
    llm = ExtractionResult(
        issuer="Pilot Aiship",
        posting_type_candidate="AR",
        ar_ap_reasoning="issuer is accounting entity",
        confidence=0.72,
        charges=[
            ExtractedCharge(raw_name="HANDLING CHARGE", amount=Decimal("100.00"))
        ],
    )

    merged = merge_extraction(rule, llm, llm_used=True)

    assert merged.result.reference_no == "DC-001"
    assert merged.result.issuer == "Pilot Aiship"
    assert merged.result.posting_type_candidate == "AR"
    assert merged.result.confidence == 0.72
    assert merged.llm_used is True
    assert merged.llm_confidence == 0.72
    assert len(merged.result.charges) == 2
    assert merged.conflicts
