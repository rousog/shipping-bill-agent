from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from shipping_agent.config import load_settings
from shipping_agent.extractors.llm import LLMExtractor, build_llm_extractor
from shipping_agent.models import ExtractedCharge, ExtractionResult


class FakeChain:
    def invoke(self, inputs):
        assert "context" in inputs
        return ExtractionResult(
            document_type="Debit Note",
            reference_no="DC-LLM-001",
            charges=[
                ExtractedCharge(
                    raw_name="HANDLING FEE",
                    amount=Decimal("65.00"),
                    currency="USD",
                    payment_term="CC",
                )
            ],
        )


def test_llm_extractor_with_fake_chain() -> None:
    extractor = LLMExtractor(FakeChain())
    assert extractor.available is True

    result = extractor.extract("document context")
    assert result.reference_no == "DC-LLM-001"
    assert result.charges[0].raw_name == "HANDLING FEE"
    assert result.charges[0].amount == Decimal("65.00")


def test_build_extractor_respects_missing_key() -> None:
    settings = load_settings()
    extractor = build_llm_extractor(settings)
    assert extractor.available is settings.is_llm_configured


def test_build_extractor_with_key_builds_chain() -> None:
    settings = replace(
        load_settings(),
        llm_api_key="sk-test",
        llm_model="gpt-4o-mini",
    )
    extractor = build_llm_extractor(settings)
    assert extractor.available is True
    assert hasattr(extractor.chain, "invoke")
