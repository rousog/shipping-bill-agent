from __future__ import annotations

from shipping_agent.models import PaymentTerm
from shipping_agent.rules.registry import load_rules


def test_normalizers() -> None:
    rules = load_rules()

    assert rules.charges.normalize("H/C") == "Handling Charge"
    assert rules.charges.normalize("HANDLING FEE") == "Handling Charge"
    assert rules.charges.normalize("WHARFAGE ADMINISTRATION FEE") == (
        "Wharfage Administration Fee"
    )
    assert rules.charges.normalize("完全未知的费用") is None

    assert rules.currency.normalize("US$") == "USD"
    assert rules.currency.normalize("RMB") == "CNY"
    assert rules.currency.is_valid("USD") is True
    assert rules.currency.is_valid("XYZ") is False

    assert rules.payment_terms.normalize("C") == PaymentTerm.CC
    assert rules.payment_terms.normalize("PREPAID") == PaymentTerm.PP
    assert rules.payment_terms.normalize("COD") is None

    assert rules.alias_version
    assert len(rules.alias_version) == 12
