from __future__ import annotations

from dataclasses import replace

from shipping_agent.ar_ap import resolve_posting_type
from shipping_agent.config import load_settings
from shipping_agent.models import (
    DocumentInfo,
    DocumentType,
    FinancialAttributes,
    InternalBillRecord,
    PartyInfo,
    PostingType,
)


def _record(
    issuer: str | None,
    recipient: str | None,
    candidate: PostingType | None = None,
) -> InternalBillRecord:
    settings = load_settings()
    return InternalBillRecord(
        record_id="REC-1",
        document_info=DocumentInfo(
            document_type=DocumentType.DEBIT_NOTE,
            reference_no="DC-1",
        ),
        transaction_party=PartyInfo(issuer=issuer, recipient=recipient),
        charges=[],
        financial_attributes=FinancialAttributes(
            accounting_entity=settings.accounting_entity,
            posting_type=candidate,
        ),
    )


def test_ar_when_issuer_is_entity() -> None:
    posting, issues = resolve_posting_type(
        _record("湃乐多（上海）航运科技有限公司", "AEBO TCO IMPORT LLC"),
        load_settings(),
    )
    assert posting == PostingType.AR
    assert issues == []


def test_ap_when_recipient_is_entity() -> None:
    posting, issues = resolve_posting_type(
        _record("NEPTUNE SHIPPING LIMITED", "湃乐多（上海）航运科技有限公司"),
        load_settings(),
    )
    assert posting == PostingType.AP
    assert issues == []


def test_internal_parties_go_to_review() -> None:
    posting, issues = resolve_posting_type(
        _record("luka@pltplt.com", "li@pltplt.com"),
        load_settings(),
    )
    assert posting is None
    assert any(issue.code == "AR_AP_INTERNAL" for issue in issues)


def test_unknown_parties_go_to_review() -> None:
    posting, issues = resolve_posting_type(
        _record("NEPTUNE SHIPPING LIMITED", "ZIM INTEGRATED SHIPPING"),
        load_settings(),
    )
    assert posting is None
    assert any(issue.code == "AR_AP_UNKNOWN" for issue in issues)


def test_llm_candidate_conflict() -> None:
    posting, issues = resolve_posting_type(
        _record(
            "湃乐多（上海）航运科技有限公司",
            "AEBO TCO IMPORT LLC",
            candidate=PostingType.AP,
        ),
        load_settings(),
    )
    assert posting is None
    assert any(issue.code == "AR_AP_CONFLICT" for issue in issues)


def test_hybrid_uses_llm_when_rule_unknown() -> None:
    settings = replace(load_settings(), ar_ap_policy="hybrid")
    posting, issues = resolve_posting_type(
        _record(
            "NEPTUNE SHIPPING LIMITED",
            "ZIM INTEGRATED SHIPPING",
            candidate=PostingType.AP,
        ),
        settings,
    )
    assert posting == PostingType.AP
    assert any(
        issue.code == "AR_AP_UNVERIFIED"
        and issue.severity == "soft"
        and issue.review_required
        for issue in issues
    )


def test_llm_first_uses_candidate() -> None:
    settings = replace(load_settings(), ar_ap_policy="llm_first")
    posting, issues = resolve_posting_type(
        _record(
            "NEPTUNE SHIPPING LIMITED",
            "ZIM INTEGRATED SHIPPING",
            candidate=PostingType.AP,
        ),
        settings,
    )
    assert posting == PostingType.AP
    assert any(
        issue.code == "AR_AP_UNVERIFIED" and not issue.review_required
        for issue in issues
    )


def test_rule_first_rejects_unverified_llm_candidate() -> None:
    settings = replace(load_settings(), ar_ap_policy="rule_first")
    posting, issues = resolve_posting_type(
        _record(
            "NEPTUNE SHIPPING LIMITED",
            "ZIM INTEGRATED SHIPPING",
            candidate=PostingType.AP,
        ),
        settings,
    )
    assert posting is None
    assert any(
        issue.code == "AR_AP_UNVERIFIED"
        and issue.severity == "hard"
        and issue.review_required
        for issue in issues
    )
