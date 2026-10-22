"""RuleRegistry：启动时加载 YAML，构建标准化器和版本号。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from ..utils.hashing import sha256_text
from .charges import ChargeNormalizer
from .currency import CurrencyNormalizer
from .extraction import ExtractionRules
from .payment_terms import PaymentTermNormalizer

DEFAULT_CONFIG_DIR = Path(__file__).resolve().parents[3] / "config"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class RuleRegistry:
    charges: ChargeNormalizer
    currency: CurrencyNormalizer
    payment_terms: PaymentTermNormalizer
    extraction: ExtractionRules
    alias_version: str
    config_dir: Path


def load_rules(config_dir: str | Path | None = None) -> RuleRegistry:
    directory = Path(config_dir) if config_dir else DEFAULT_CONFIG_DIR

    charge_text = _read_text(directory / "charge_aliases.yaml")
    currency_text = _read_text(directory / "currency.yaml")
    payment_text = _read_text(directory / "payment_terms.yaml")
    extraction_text = _read_text(directory / "extraction_rules.yaml")

    alias_version = sha256_text(
        charge_text + currency_text + payment_text + extraction_text
    )[:12]

    return RuleRegistry(
        charges=ChargeNormalizer(_load_yaml(directory / "charge_aliases.yaml")),
        currency=CurrencyNormalizer(_load_yaml(directory / "currency.yaml")),
        payment_terms=PaymentTermNormalizer(
            _load_yaml(directory / "payment_terms.yaml")
        ),
        extraction=ExtractionRules(),
        alias_version=alias_version,
        config_dir=directory,
    )
