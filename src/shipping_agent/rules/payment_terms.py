"""付款方式标准化：PP / CC。"""

from __future__ import annotations

import re

from ..models import PaymentTerm


def _normalize(value: str | None) -> str:
    text = (value or "").upper().strip()
    text = re.sub(r"[\s_\-/]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class PaymentTermNormalizer:
    def __init__(self, config: dict | None = None):
        config = config or {}
        self.pp_set = {_normalize(item) for item in (config.get("PP") or [])}
        self.cc_set = {_normalize(item) for item in (config.get("CC") or [])}

    def normalize(self, raw: str | None) -> PaymentTerm | None:
        key = _normalize(raw)
        if not key:
            return None
        if key in self.pp_set:
            return PaymentTerm.PP
        if key in self.cc_set:
            return PaymentTerm.CC
        return None
