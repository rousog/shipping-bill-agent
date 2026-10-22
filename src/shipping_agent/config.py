"""配置加载。

原则：
- 敏感信息（API key、OSS AK/SK、记账主体）只从 .env 读取；
- 可公开的阈值和默认值放 config/*.yaml；
- .env 不提交到 Git。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
ENV_FILE = PROJECT_ROOT / ".env"

load_dotenv(ENV_FILE, override=False)


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _split_aliases(raw: str) -> list[str]:
    # 用分号分隔，避免公司名里的逗号造成歧义
    return [item.strip() for item in raw.split(";") if item.strip()]


def _resolve_path(raw: str, default: str) -> Path:
    value = Path(raw or default)
    return value if value.is_absolute() else (PROJECT_ROOT / value)


def _load_yaml(name: str) -> dict:
    path = CONFIG_DIR / name
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@dataclass(frozen=True)
class Settings:
    project_root: Path

    # LLM
    llm_provider: str
    llm_model: str
    llm_api_key: str
    llm_base_url: str | None
    llm_temperature: float

    # OCR
    ocr_url: str
    ocr_task: str
    ocr_timeout_connect: int
    ocr_timeout_read: int
    ocr_max_retries: int
    ocr_public_base_url: str | None

    # 记账主体 / AR-AP
    accounting_entity: str
    accounting_entity_aliases: list[str]
    ar_ap_policy: str

    # 存储
    db_path: Path
    asset_root: Path
    asset_backend: str
    asset_public_base_url: str | None

    # 运行时
    log_level: str
    confidence_threshold: float
    max_retries: int
    app_config: dict = field(default_factory=dict)

    @property
    def is_llm_configured(self) -> bool:
        key = self.llm_api_key
        return bool(key and not key.startswith("your-"))

    def ensure_directories(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.asset_root.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def load_settings() -> Settings:
    app_config = _load_yaml("app.yaml")
    app_section = app_config.get("app", {}) if isinstance(app_config, dict) else {}

    return Settings(
        project_root=PROJECT_ROOT,
        llm_provider=_env("LLM_PROVIDER", "openai"),
        llm_model=_env("LLM_MODEL", "your-model-name"),
        llm_api_key=_env("LLM_API_KEY", "your-api-key"),
        llm_base_url=_env("LLM_BASE_URL") or None,
        llm_temperature=_env_float("LLM_TEMPERATURE", 0.0),
        ocr_url=_env("OCR_URL", "http://36.103.199.11:5002/process"),
        ocr_task=_env("OCR_TASK", "orderChange"),
        ocr_timeout_connect=_env_int("OCR_TIMEOUT_CONNECT", 10),
        ocr_timeout_read=_env_int("OCR_TIMEOUT_READ", 300),
        ocr_max_retries=_env_int("OCR_MAX_RETRIES", 3),
        ocr_public_base_url=_env("OCR_PUBLIC_BASE_URL") or None,
        accounting_entity=_env(
            "ACCOUNTING_ENTITY", "湃乐多（上海）航运科技有限公司"
        ),
        accounting_entity_aliases=_split_aliases(
            _env(
                "ACCOUNTING_ENTITY_ALIASES",
                "湃乐多;Pilot Aiship Tech co., Ltd.;PLTP",
            )
        ),
        ar_ap_policy=_env("AR_AP_POLICY", "hybrid").lower(),
        db_path=_resolve_path(_env("DB_PATH"), "data/agent.db"),
        asset_root=_resolve_path(_env("ASSET_ROOT"), "data/assets"),
        asset_backend=_env("ASSET_BACKEND", "local"),
        asset_public_base_url=_env("ASSET_PUBLIC_BASE_URL") or None,
        log_level=_env("LOG_LEVEL", str(app_section.get("log_level", "INFO"))),
        confidence_threshold=_env_float(
            "CONFIDENCE_THRESHOLD",
            float(app_section.get("confidence_threshold", 0.6)),
        ),
        max_retries=_env_int(
            "MAX_RETRIES", int(app_section.get("max_retries", 3))
        ),
        app_config=app_config,
    )
