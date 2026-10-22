"""Word 解析：提取段落和表格。

- .docx：python-docx
- .doc：尝试 LibreOffice 转换；没有转换工具时显式返回待转换提示
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from ..assets import AssetStore
from ..models import AssetRef, RawDocument, SourceType


def _table_to_rows(table) -> list[list[str]]:
    return [
        [cell.text.strip() for cell in row.cells]
        for row in table.rows
    ]


def _convert_doc_to_docx(path: Path, out_dir: Path) -> Path | None:
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if not binary:
        return None
    try:
        subprocess.run(
            [
                binary,
                "--headless",
                "--convert-to",
                "docx",
                "--outdir",
                str(out_dir),
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    candidate = out_dir / f"{path.stem}.docx"
    return candidate if candidate.exists() else None


def _parse_docx(
    asset: AssetRef,
    path: Path,
    *,
    converted_from_doc: bool = False,
) -> RawDocument:
    from docx import Document

    document = Document(str(path))
    paragraphs = [
        paragraph.text.strip()
        for paragraph in document.paragraphs
        if paragraph.text.strip()
    ]
    tables: list[list[list[str]]] = []
    table_texts: list[str] = []

    for table in document.tables:
        rows = _table_to_rows(table)
        if not rows:
            continue
        tables.append(rows)
        table_texts.append("\n".join(" | ".join(row) for row in rows))

    text = "\n".join(paragraphs)
    if table_texts:
        text = (text + "\n\n" + "\n\n".join(table_texts)).strip()

    metadata = {
        "file_name": asset.file_name,
        "mime_type": asset.mime_type,
        "size_bytes": asset.size_bytes,
        "paragraph_count": len(paragraphs),
        "table_count": len(tables),
        "converted_from_doc": converted_from_doc,
    }

    return RawDocument(
        doc_id=f"DOCX-{asset.sha256[:12]}",
        asset_id=asset.asset_id,
        source_type=SourceType.WORD,
        text=text or None,
        tables=tables,
        metadata=metadata,
        parse_notes=[],
    )


def parse_word(
    source: str | Path | AssetRef,
    asset_store: AssetStore,
) -> RawDocument:
    asset = source if isinstance(source, AssetRef) else asset_store.put(source)
    path = asset_store.path(asset.asset_id)

    if path.suffix.lower() == ".doc":
        with tempfile.TemporaryDirectory(prefix="shipping-doc-") as tmp:
            converted = _convert_doc_to_docx(path, Path(tmp))
            if converted is None:
                return RawDocument(
                    doc_id=f"DOC-{asset.sha256[:12]}",
                    asset_id=asset.asset_id,
                    source_type=SourceType.WORD,
                    text=None,
                    tables=[],
                    metadata={
                        "file_name": asset.file_name,
                        "mime_type": asset.mime_type,
                        "size_bytes": asset.size_bytes,
                        "needs_conversion": True,
                        "converted_from_doc": False,
                    },
                    parse_notes=[
                        ".doc 老格式需要 LibreOffice/soffice 转换；"
                        "当前环境未找到转换工具"
                    ],
                )
            return _parse_docx(asset, converted, converted_from_doc=True)

    return _parse_docx(asset, path)
