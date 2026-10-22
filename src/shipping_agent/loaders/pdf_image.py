"""PDF / 图片解析。

策略：
- 文本型 PDF：pdfplumber 抽文字和表格，OCR 可作为交叉校验；
- 扫描型 PDF：走 OCR；
- 图片：必须走 OCR；
- OCR 失败不丢文档：保留本地文本/错误信息，交给后续 PENDING_REVIEW。
"""

from __future__ import annotations

from pathlib import Path

import pdfplumber
from PIL import Image

from ..assets import AssetStore
from ..models import AssetRef, OCRResult, RawDocument, SourceType
from ..ocr.client import OCRClient

_TEXT_THRESHOLD = 100


def _cell(value: object) -> str:
    return "" if value is None else str(value).strip()


def _ocr_text(result: OCRResult | None) -> str:
    if result is None:
        return ""
    if result.text:
        return result.text.strip()
    return "\n".join(page.strip() for page in result.page_texts if page.strip())


def _ocr_summary(result: OCRResult | None) -> dict | None:
    if result is None:
        return None
    # raw 可能很大，放缓存文件；state 里只放摘要
    return result.model_dump(mode="json", exclude={"raw"})


def _ocr_metadata(
    result: OCRResult | None,
    error: str | None,
    asset: AssetRef,
) -> dict:
    return {
        "ocr_used": result is not None,
        "ocr_error": error,
        "ocr_cache_key": asset.sha256 if result is not None else None,
        "ocr_file_type": result.file_type if result else None,
        "ocr_charge_count": len(result.charges) if result else 0,
        "ocr_container_count": len(result.containers) if result else 0,
        "ocr_suspicious": result.suspicious if result else False,
        "ocr": _ocr_summary(result),
    }


def parse_pdf_image(
    source: str | Path | AssetRef,
    asset_store: AssetStore,
    *,
    ocr_client: OCRClient | None = None,
    use_ocr: bool = True,
) -> RawDocument:
    if isinstance(source, AssetRef):
        asset = source
    else:
        asset = asset_store.put(source)

    path = asset_store.path(asset.asset_id)
    suffix = path.suffix.lower()
    source_type = asset.source_type
    if source_type not in (SourceType.PDF, SourceType.IMAGE):
        if suffix == ".pdf":
            source_type = SourceType.PDF
        elif suffix in {".png", ".jpg", ".jpeg", ".tif", ".tiff"}:
            source_type = SourceType.IMAGE
        else:
            source_type = SourceType.OTHER

    if source_type == SourceType.PDF:
        return _parse_pdf(
            asset,
            path,
            asset_store,
            ocr_client=ocr_client,
            use_ocr=use_ocr,
        )
    return _parse_image(
        asset,
        path,
        asset_store,
        ocr_client=ocr_client,
        use_ocr=use_ocr,
    )


def _parse_pdf(
    asset: AssetRef,
    path: Path,
    asset_store: AssetStore,
    *,
    ocr_client: OCRClient | None,
    use_ocr: bool,
) -> RawDocument:
    page_texts: list[str] = []
    tables: list[list[list[str]]] = []
    notes: list[str] = []
    page_count = 0

    try:
        with pdfplumber.open(path) as pdf:
            page_count = len(pdf.pages)
            for page in pdf.pages:
                text = page.extract_text() or ""
                page_texts.append(text)
                for table in page.extract_tables() or []:
                    if table:
                        tables.append(
                            [[_cell(cell) for cell in row] for row in table]
                        )
    except Exception as exc:
        notes.append(f"PDF 本地解析失败: {exc}")

    local_text = "\n\n".join(text.strip() for text in page_texts if text.strip())
    has_text_layer = len(local_text.strip()) >= max(
        _TEXT_THRESHOLD * max(page_count, 1), 50
    )
    is_scanned = not has_text_layer

    if is_scanned:
        notes.append("未检测到有效文本层，按扫描件走 OCR")

    ocr_result: OCRResult | None = None
    ocr_error: str | None = None
    if use_ocr:
        if ocr_client is None:
            notes.append("OCR client 未配置，保留本地文本结果")
        else:
            try:
                ocr_result = ocr_client.process_asset(asset.asset_id, asset_store)
                notes.append("OCR 调用成功")
            except Exception as exc:
                ocr_error = str(exc)
                notes.append(f"OCR 调用失败: {exc}")

    text = local_text or _ocr_text(ocr_result)

    metadata = {
        "file_name": asset.file_name,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "page_count": page_count,
        "has_text_layer": has_text_layer,
        "is_scanned": is_scanned,
        **_ocr_metadata(ocr_result, ocr_error, asset),
    }

    return RawDocument(
        doc_id=f"PDF-{asset.sha256[:12]}",
        asset_id=asset.asset_id,
        source_type=SourceType.PDF,
        text=text or None,
        tables=tables,
        metadata=metadata,
        parse_notes=notes,
    )


def _parse_image(
    asset: AssetRef,
    path: Path,
    asset_store: AssetStore,
    *,
    ocr_client: OCRClient | None,
    use_ocr: bool,
) -> RawDocument:
    notes: list[str] = []
    width = height = None
    try:
        with Image.open(path) as image:
            width, height = image.size
    except Exception as exc:
        notes.append(f"图片尺寸读取失败: {exc}")

    ocr_result: OCRResult | None = None
    ocr_error: str | None = None
    if use_ocr:
        if ocr_client is None:
            notes.append("OCR client 未配置，图片无法抽取文本")
        else:
            try:
                ocr_result = ocr_client.process_asset(asset.asset_id, asset_store)
                notes.append("OCR 调用成功")
            except Exception as exc:
                ocr_error = str(exc)
                notes.append(f"OCR 调用失败: {exc}")
    else:
        notes.append("OCR 已禁用")

    text = _ocr_text(ocr_result)

    metadata = {
        "file_name": asset.file_name,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "width": width,
        "height": height,
        "is_scanned": True,
        "has_text_layer": False,
        **_ocr_metadata(ocr_result, ocr_error, asset),
    }

    return RawDocument(
        doc_id=f"IMG-{asset.sha256[:12]}",
        asset_id=asset.asset_id,
        source_type=SourceType.IMAGE,
        text=text or None,
        tables=[],
        metadata=metadata,
        parse_notes=notes,
    )
