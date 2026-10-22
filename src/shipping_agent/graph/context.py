"""图节点依赖注入。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..asset_service import AssetService
from ..config import Settings
from ..decide import DecisionEngine
from ..extractors.llm import LLMExtractor
from ..ocr.client import OCRClient
from ..rules.registry import RuleRegistry


@dataclass
class GraphContext:
    settings: Settings
    rules: RuleRegistry
    asset_service: AssetService
    ocr_client: OCRClient | None
    llm_extractor: LLMExtractor
    engine: DecisionEngine
    conn: sqlite3.Connection
    output_path: Path
    summary_path: Path | None = None
    use_ocr: bool = True
    max_depth: int = 3
