"""币种标准化与 ISO 4217 校验。"""

from __future__ import annotations


class CurrencyNormalizer:
    def __init__(self, config: dict | None = None):
        config = config or {}
        self.aliases = {
            str(key).upper().strip(): str(value).upper().strip()
            for key, value in (config.get("aliases") or {}).items()
        }
        self.iso_set = {
            str(code).upper().strip()
            for code in (config.get("iso4217") or [])
        }

    def normalize(self, raw: str | None) -> str | None:
        if not raw:
            return None
        value = str(raw).upper().strip().replace(" ", "")
        return self.aliases.get(value, value)

    def is_valid(self, code: str | None) -> bool:
        return bool(code) and code in self.iso_set
