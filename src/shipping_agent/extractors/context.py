"""把 RawDocument 压缩成适合 LLM 的 ExtractionContext。

原则：
- 不放完整内部 JSON；
- 不放 OCR raw；
- 不放 base64；
- 表格转 Markdown，限制行数；
- EML 附件只放摘要，不放全文。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from ..models import RawDocument
from ..rules.registry import RuleRegistry

MAX_TEXT_CHARS = 12_000
MAX_TABLES = 5
MAX_TABLE_ROWS = 50
MAX_ATTACHMENTS = 10
ATTACHMENT_EXCERPT_CHARS = 300


def truncate(text: str | None, limit: int) -> str:
    if not text:
        return ""
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def table_to_markdown(rows: list[list[str]], max_rows: int = MAX_TABLE_ROWS) -> str:
    if not rows:
        return ""
    width = max(len(row) for row in rows)
    normalized = [
        [str(cell or "").replace("|", "\\|").strip() for cell in row]
        + [""] * (width - len(row))
        for row in rows[:max_rows]
    ]
    header = normalized[0]
    lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    for row in normalized[1:]:
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


@dataclass
class ExtractionContext:
    source_type: str
    file_name: str
    document_type_hint: str | None
    text: str
    tables: list[str] = field(default_factory=list)
    ocr: dict | None = None
    attachments: list[dict] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def render(self) -> str:
        lines = [
            "# Document",
            f"source_type: {self.source_type}",
            f"file_name: {self.file_name}",
            f"document_type_hint: {self.document_type_hint or 'unknown'}",
        ]
        for key in ("subject", "from", "to", "page_count", "is_scanned", "sheet_count"):
            value = self.metadata.get(key)
            if value not in (None, "", []):
                lines.append(f"{key}: {value}")

        lines.append("\n# Text")
        lines.append(self.text or "(empty)")

        if self.tables:
            lines.append("\n# Tables")
            for index, table in enumerate(self.tables, 1):
                lines.append(f"\n## Table {index}\n{table}")

        if self.ocr:
            lines.append("\n# OCR facts")
            lines.append(json.dumps(self.ocr, ensure_ascii=False, indent=2))

        if self.attachments:
            lines.append("\n# Attachments")
            lines.append(json.dumps(self.attachments, ensure_ascii=False, indent=2))

        return "\n".join(lines)


def _attachment_summary(doc: RawDocument) -> dict:
    return {
        "file_name": doc.metadata.get("file_name"),
        "source_type": doc.source_type.value,
        "text_excerpt": truncate(doc.text, ATTACHMENT_EXCERPT_CHARS),
        "ocr": doc.metadata.get("ocr"),
    }


def build_extraction_context(
    doc: RawDocument,
    rules: RuleRegistry,
) -> ExtractionContext:
    file_name = doc.metadata.get("file_name") or ""
    subject = doc.metadata.get("subject") or ""
    hint_text = f"{subject}\n{doc.text or ''}"
    document_type_hint = rules.extraction.infer_document_type(hint_text, file_name)

    tables = [
        table_to_markdown(table)
        for table in doc.tables[:MAX_TABLES]
        if table
    ]

    metadata = {
        "subject": doc.metadata.get("subject"),
        "from": doc.metadata.get("from"),
        "to": doc.metadata.get("to"),
        "page_count": doc.metadata.get("page_count"),
        "is_scanned": doc.metadata.get("is_scanned"),
        "sheet_count": doc.metadata.get("sheet_count"),
    }

    attachments = [
        _attachment_summary(child)
        for child in doc.attachments[:MAX_ATTACHMENTS]
    ]

    return ExtractionContext(
        source_type=doc.source_type.value,
        file_name=file_name,
        document_type_hint=document_type_hint,
        text=truncate(doc.text, MAX_TEXT_CHARS),
        tables=tables,
        ocr=doc.metadata.get("ocr"),
        attachments=attachments,
        metadata=metadata,
    )
