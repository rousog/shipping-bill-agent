"""文档路由：按扩展名 / MIME / magic bytes 分发给具体 loader。"""

from __future__ import annotations

from pathlib import Path

from ..assets import AssetStore, sniff_source_type
from ..models import AssetRef, RawDocument, SourceType
from ..ocr.client import OCRClient
from .eml import parse_eml_bytes
from .excel import parse_excel
from .pdf_image import parse_pdf_image
from .word import parse_word


def route_path(
    path: str | Path,
    asset_store: AssetStore,
    *,
    depth: int = 0,
    max_depth: int = 3,
    parent_asset_id: str | None = None,
    visited: set[str] | None = None,
    ocr_client: OCRClient | None = None,
    use_ocr: bool = True,
) -> RawDocument:
    asset = asset_store.put(path, parent_asset_id=parent_asset_id)
    return route_asset(
        asset,
        asset_store,
        depth=depth,
        max_depth=max_depth,
        visited=visited,
        ocr_client=ocr_client,
        use_ocr=use_ocr,
    )


def route_asset(
    asset: AssetRef,
    asset_store: AssetStore,
    *,
    depth: int = 0,
    max_depth: int = 3,
    visited: set[str] | None = None,
    ocr_client: OCRClient | None = None,
    use_ocr: bool = True,
) -> RawDocument:
    source_type = asset.source_type
    if source_type in (None, SourceType.OTHER):
        data = asset_store.get(asset.asset_id)
        source_type = (
            sniff_source_type(asset.file_name, asset.mime_type, data)
            or SourceType.OTHER
        )

    if source_type == SourceType.EML:
        if depth > max_depth:
            return RawDocument(
                doc_id=f"EML-{asset.sha256[:12]}",
                asset_id=asset.asset_id,
                source_type=SourceType.EML,
                metadata={
                    "needs_loader": True,
                    "file_name": asset.file_name,
                    "depth": depth,
                    "max_depth_reached": True,
                },
                parse_notes=[f"达到最大递归深度 {max_depth}，未继续解析"],
            )
        data = asset_store.get(asset.asset_id)
        return parse_eml_bytes(
            data,
            asset.file_name,
            asset_store,
            existing_asset=asset,
            depth=depth,
            max_depth=max_depth,
            visited=visited,
            ocr_client=ocr_client,
            use_ocr=use_ocr,
        )

    if source_type in (SourceType.PDF, SourceType.IMAGE):
        return parse_pdf_image(
            asset,
            asset_store,
            ocr_client=ocr_client,
            use_ocr=use_ocr,
        )

    if source_type == SourceType.EXCEL:
        return parse_excel(asset, asset_store)

    if source_type == SourceType.WORD:
        return parse_word(asset, asset_store)

    if source_type == SourceType.JSON:
        return RawDocument(
            doc_id=f"JSON-{asset.sha256[:12]}",
            asset_id=asset.asset_id,
            source_type=SourceType.JSON,
            text=None,
            metadata={
                "file_name": asset.file_name,
                "mime_type": asset.mime_type,
                "kind": "existing_bill_json",
            },
            parse_notes=["JSON 作为已有账单输入，交给匹配器处理"],
        )

    return RawDocument(
        doc_id=f"OTHER-{asset.sha256[:12]}",
        asset_id=asset.asset_id,
        source_type=SourceType.OTHER,
        metadata={
            "needs_loader": True,
            "file_name": asset.file_name,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "depth": depth,
        },
        parse_notes=["未知文件类型，未解析"],
    )
