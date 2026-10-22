"""OCR API 客户端。

接口：
    POST http://36.103.199.11:5002/process
    {"file": "<公网 URL>", "task": "orderChange", "callBack": "", "taskType": ""}

特性：
- 超时、重试、指数退避；
- 按 asset sha256 缓存原始响应；
- 把返回的领域 JSON 映射成 OCRResult；
- 找不到公网 URL 时明确报错。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from ..assets import AssetStore
from ..config import Settings
from ..models import OCRCharge, OCRResult
from ..utils.money import parse_decimal


class OCRError(RuntimeError):
    """OCR 调用或响应解析失败。"""


def _first(mapping: dict, keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _bool_flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().upper() in {"Y", "YES", "TRUE", "1"}


class OCRClient:
    def __init__(
        self,
        base_url: str,
        *,
        task: str = "orderChange",
        timeout: tuple[int, int] = (10, 300),
        max_retries: int = 3,
        cache_dir: str | Path | None = None,
        session: requests.Session | None = None,
        backoff: float = 1.0,
        sleep=time.sleep,
    ):
        self.base_url = base_url
        self.task = task
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.session = session or requests.Session()
        self.backoff = backoff
        self.sleep = sleep

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        session: requests.Session | None = None,
    ) -> "OCRClient":
        return cls(
            settings.ocr_url,
            task=settings.ocr_task,
            timeout=(settings.ocr_timeout_connect, settings.ocr_timeout_read),
            max_retries=settings.ocr_max_retries,
            cache_dir=settings.asset_root.parent / "ocr_cache",
            session=session,
        )

    def process_file_url(self, file_url: str) -> dict:
        payload = {
            "file": file_url,
            "task": self.task,
            "callBack": "",
            "taskType": "",
        }
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.post(
                    self.base_url, json=payload, timeout=self.timeout
                )
                if response.status_code >= 500:
                    raise OCRError(f"OCR 服务端错误: HTTP {response.status_code}")
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    raise OCRError("OCR 返回不是 JSON object")
                if data.get("success") is False:
                    raise OCRError(
                        f"OCR 处理失败: {data.get('message') or data.get('error_code')}"
                    )
                return data
            except (requests.RequestException, ValueError, OCRError) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                self.sleep(self.backoff * (2**attempt))

        raise OCRError(f"OCR 调用失败: {last_error}") from last_error

    def process_asset(self, asset_id: str, asset_store: AssetStore) -> OCRResult:
        raw = self._load_cache(asset_id)
        if raw is None:
            file_url = asset_store.url(asset_id)
            raw = self.process_file_url(file_url)
            self._save_cache(asset_id, raw)
        return self.parse_response(raw)

    def parse_response(self, raw: dict) -> OCRResult:
        data = raw.get("data") or {}
        items = data.get("list") or []
        item = items[0] if items else {}

        charges = [
            self._parse_charge(charge)
            for charge in (item.get("chargeList") or [])
            if charge
        ]
        containers = [
            container
            for container in (
                self._parse_container(entry)
                for entry in (item.get("ctrList") or [])
            )
            if container
        ]

        text = _first(item, ("text", "content", "rawText")) or _first(
            data, ("text", "content")
        )
        page_texts: list[str] = []
        pages = item.get("pageTexts") or data.get("pageTexts")
        if isinstance(pages, list):
            page_texts = [str(page) for page in pages if page]
        else:
            page_list = item.get("pages")
            if isinstance(page_list, list):
                page_texts = [str(page) for page in page_list if isinstance(page, str)]

        return OCRResult(
            file_type=item.get("fileType"),
            order_type=item.get("orderType"),
            shipper=item.get("shipper"),
            consignee=item.get("consignee"),
            mbl_number=item.get("mblNumber"),
            invoice_number=item.get("invoiceNumber"),
            text=str(text) if text else None,
            page_texts=page_texts,
            charges=charges,
            containers=containers,
            suspicious=_bool_flag(item.get("suspicious", "N")),
            requires_review=_bool_flag(item.get("requiresReview", "N")),
            raw=raw,
        )

    @staticmethod
    def _parse_charge(charge: Any) -> OCRCharge:
        if not isinstance(charge, dict):
            return OCRCharge(raw_name=str(charge))

        raw_name = _first(
            charge,
            (
                "rawName",
                "chargeItem",
                "charge_item",
                "chargeName",
                "charge_name",
                "feeName",
                "fee_name",
                "name",
                "item",
                "description",
            ),
        )
        amount = _first(
            charge,
            ("amount", "fee", "total", "charge", "debit", "credit"),
        )
        currency = _first(charge, ("currency", "curr", "ccy"))
        payment_term = _first(
            charge, ("paymentTerm", "payment_term", "p/c", "pc", "prepaidCollect")
        )

        return OCRCharge(
            raw_name=str(raw_name) if raw_name is not None else None,
            amount=parse_decimal(amount),
            currency=str(currency).upper() if currency else None,
            payment_term=str(payment_term) if payment_term else None,
            raw=charge,
        )

    @staticmethod
    def _parse_container(entry: Any) -> str | None:
        if isinstance(entry, str):
            return entry.strip() or None
        if isinstance(entry, dict):
            value = _first(
                entry,
                (
                    "containerNo",
                    "container_no",
                    "ctrNumber",
                    "ctr_number",
                    "ctrNo",
                    "ctr_no",
                    "container",
                    "number",
                ),
            )
            return str(value).strip() if value else None
        return None

    def _load_cache(self, asset_id: str) -> dict | None:
        if not self.cache_dir:
            return None
        path = self.cache_dir / f"{asset_id}.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None

    def _save_cache(self, asset_id: str, raw: dict) -> None:
        if not self.cache_dir:
            return
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"{asset_id}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(path)
