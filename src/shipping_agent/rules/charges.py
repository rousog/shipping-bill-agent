"""费用名称标准化。"""

from __future__ import annotations

import re
from difflib import SequenceMatcher


def normalize_key(value: str | None) -> str:
    text = (value or "").upper().strip()
    text = re.sub(r"[\s\-/.,:;()\[\]]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


class ChargeNormalizer:
    def __init__(self, mapping: dict[str, list[str]] | None = None):
        self.alias_to_std: dict[str, str] = {}
        self.std_names: set[str] = set()
        for std_name, aliases in (mapping or {}).items():
            self.std_names.add(std_name)
            for alias in aliases or []:
                key = normalize_key(alias)
                if key:
                    self.alias_to_std[key] = std_name

    def normalize(self, raw_name: str | None) -> str | None:
        result, _ = self.normalize_with_status(raw_name)
        return result

    def normalize_with_status(
        self, raw_name: str | None, cutoff: float = 0.9
    ) -> tuple[str | None, str]:
        key = normalize_key(raw_name)
        if not key:
            return None, "empty"
        if key in self.alias_to_std:
            return self.alias_to_std[key], "exact"

        best_key = None
        best_ratio = 0.0
        for alias in self.alias_to_std:
            ratio = SequenceMatcher(None, key, alias).ratio()
            if ratio > best_ratio:
                best_key, best_ratio = alias, ratio
        if best_key and best_ratio >= cutoff:
            return self.alias_to_std[best_key], "fuzzy"
        return None, "unknown"
