"""EML 解析：正文、邮件头、附件递归、内嵌图过滤。

核心点：
1. 附件递归：EML 里可以再套 EML（message/rfc822 或 .eml 附件），最多 3 层；
2. 内嵌图过滤：邮件签名/宣传 banner 不送 OCR；
3. 附件按实际扩展名走 router，不假设都是 PDF。
"""

from __future__ import annotations

import email
import html as html_lib
import re
from email import policy
from email.message import Message
from io import BytesIO
from pathlib import Path
from typing import Iterable

from PIL import Image

from ..assets import AssetStore, sniff_source_type
from ..models import AssetRef, RawDocument, SourceType
from ..ocr.client import OCRClient
from ..utils.hashing import sha256_bytes

# 文件名里出现这些词，优先当作单据，而不是 banner
_DOC_NAME_HINTS = (
    "invoice",
    "bill",
    "debit",
    "credit",
    "statement",
    "receipt",
    "bl",
    "提单",
    "账单",
    "发票",
    "扫描",
    "scan",
)

# 文件名里出现这些词，优先当作宣传图/签名图
_BANNER_NAME_HINTS = (
    "banner",
    "logo",
    "signature",
    "qr",
    "二维码",
    "宣传",
    "footer",
)

_UNSAFE_FILENAME = re.compile(r'[\\/:*?"<>|]+')


def _safe_filename(name: str) -> str:
    name = (name or "").replace("\xa0", " ").strip()
    name = _UNSAFE_FILENAME.sub("_", name)
    return name or "attachment"


def _strip_html(html_text: str) -> str:
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html_text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_lib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _iter_parts(msg: Message) -> Iterable[Message]:
    if hasattr(msg, "iter_parts"):
        yield from msg.iter_parts()
    elif msg.is_multipart():
        yield from msg.get_payload()


def _iter_leaf_parts(msg: Message) -> Iterable[Message]:
    """递归展开 multipart，但把 message/rfc822 当成叶子节点。"""
    if not msg.is_multipart():
        yield msg
        return
    for part in _iter_parts(msg):
        if part.get_content_type() == "message/rfc822":
            yield part
        elif part.is_multipart():
            yield from _iter_leaf_parts(part)
        else:
            yield part


def _image_size(payload: bytes) -> tuple[int, int] | None:
    try:
        with Image.open(BytesIO(payload)) as image:
            return image.size
    except Exception:
        return None


def _is_banner_image(
    *,
    payload: bytes,
    filename: str,
    cid: str | None,
    html_text: str,
    content_type: str,
    disposition: str | None,
) -> tuple[bool, str]:
    """判断内嵌图片是不是宣传 banner / 签名图。

    规则（按优先级）：
    1. 文件名含 invoice/bill/scan 等 → 当单据，不跳过；
    2. 文件名含 banner/logo/qr 等 → 当 banner；
    3. Content-Disposition: attachment → 当附件，不跳过；
    4. Content-ID 被 HTML 引用，且图片偏宽 → 当内嵌 banner；
    5. 宽高比 >= 1.5 或高 < 300 → 当 banner；
    6. 尺寸过小 → 当图标。
    """
    if not content_type.startswith("image/"):
        return False, ""

    name = (filename or "").lower()
    if any(hint in name for hint in _DOC_NAME_HINTS):
        return False, ""

    size = _image_size(payload)
    if size is None:
        return False, ""
    width, height = size
    ratio = width / height if height else 0.0

    if any(hint in name for hint in _BANNER_NAME_HINTS):
        return True, f"文件名像 banner/logo（{width}x{height}）"

    if disposition == "attachment":
        return False, ""

    cid_ref = cid.strip("<>") if cid else ""
    if cid_ref and cid_ref in html_text and (ratio >= 1.2 or width < 1000):
        return True, f"被 HTML 引用的内嵌图（{width}x{height}, ratio={ratio:.2f}）"

    if ratio >= 1.5 or (height < 300 and width > 300):
        return True, f"横幅比例内嵌图（{width}x{height}, ratio={ratio:.2f}）"

    if width < 200 or height < 200:
        return True, f"尺寸过小的内嵌图（{width}x{height}）"

    return False, ""


def _new_doc_id(asset: AssetRef, depth: int) -> str:
    return f"EML-{asset.sha256[:12]}-d{depth}"


def _attachment_placeholder(
    asset: AssetRef,
    *,
    depth: int,
    reason: str,
) -> RawDocument:
    return RawDocument(
        doc_id=f"{asset.source_type.value.upper() if asset.source_type else 'DOC'}-{asset.sha256[:12]}",
        asset_id=asset.asset_id,
        source_type=asset.source_type or SourceType.OTHER,
        metadata={
            "needs_loader": True,
            "file_name": asset.file_name,
            "mime_type": asset.mime_type,
            "size_bytes": asset.size_bytes,
            "depth": depth,
        },
        parse_notes=[reason],
    )


def parse_eml(
    path: str | Path,
    asset_store: AssetStore,
    *,
    max_depth: int = 3,
    depth: int = 0,
    parent_asset_id: str | None = None,
    visited: set[str] | None = None,
    ocr_client: OCRClient | None = None,
    use_ocr: bool = True,
) -> RawDocument:
    source = Path(path)
    return parse_eml_bytes(
        source.read_bytes(),
        source.name,
        asset_store,
        max_depth=max_depth,
        depth=depth,
        parent_asset_id=parent_asset_id,
        visited=visited,
        ocr_client=ocr_client,
        use_ocr=use_ocr,
    )


def parse_eml_bytes(
    data: bytes,
    file_name: str,
    asset_store: AssetStore,
    *,
    existing_asset: AssetRef | None = None,
    max_depth: int = 3,
    depth: int = 0,
    parent_asset_id: str | None = None,
    visited: set[str] | None = None,
    ocr_client: OCRClient | None = None,
    use_ocr: bool = True,
) -> RawDocument:
    asset = existing_asset or asset_store.put_bytes(
        data,
        file_name,
        parent_asset_id=parent_asset_id,
        source_type=SourceType.EML,
        mime_type="message/rfc822",
    )

    seen = visited if visited is not None else set()
    if asset.sha256 in seen:
        return RawDocument(
            doc_id=_new_doc_id(asset, depth),
            asset_id=asset.asset_id,
            source_type=SourceType.EML,
            metadata={
                "file_name": asset.file_name,
                "depth": depth,
                "cycle_detected": True,
            },
            parse_notes=["检测到重复/循环附件，跳过递归"],
        )
    seen.add(asset.sha256)

    msg = email.message_from_bytes(data, policy=policy.default)
    return _parse_message(
        msg,
        asset,
        depth=depth,
        max_depth=max_depth,
        asset_store=asset_store,
        visited=seen,
        ocr_client=ocr_client,
        use_ocr=use_ocr,
    )


def _parse_message(
    msg: Message,
    asset: AssetRef,
    *,
    depth: int,
    max_depth: int,
    asset_store: AssetStore,
    visited: set[str],
    ocr_client: OCRClient | None = None,
    use_ocr: bool = True,
) -> RawDocument:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[RawDocument] = []
    skipped_inline: list[dict] = []
    notes: list[str] = []

    for part in _iter_leaf_parts(msg):
        content_type = part.get_content_type()

        # 1. 嵌套邮件：message/rfc822
        if content_type == "message/rfc822":
            nested_payload = part.get_payload()
            if isinstance(nested_payload, list) and nested_payload:
                nested_msg = nested_payload[0]
            else:
                nested_bytes = part.get_payload(decode=True) or b""
                nested_msg = email.message_from_bytes(
                    nested_bytes, policy=policy.default
                )
            nested_bytes = nested_msg.as_bytes(policy=policy.default)
            nested_name = _safe_filename(
                part.get_filename()
                or f"nested-{sha256_bytes(nested_bytes)[:8]}.eml"
            )
            nested_asset = asset_store.put_bytes(
                nested_bytes,
                nested_name,
                parent_asset_id=asset.asset_id,
                source_type=SourceType.EML,
                mime_type="message/rfc822",
            )
            from .router import route_asset  # 延迟导入，避免循环依赖

            attachments.append(
                route_asset(
                    nested_asset,
                    asset_store,
                    depth=depth + 1,
                    max_depth=max_depth,
                    visited=visited,
                    ocr_client=ocr_client,
                    use_ocr=use_ocr,
                )
            )
            continue

        # 2. 正文
        raw_filename = part.get_filename()
        filename = _safe_filename(raw_filename) if raw_filename else ""
        if content_type == "text/plain" and not raw_filename:
            try:
                plain_parts.append(part.get_content())
            except Exception as exc:
                notes.append(f"text/plain 解码失败: {exc}")
            continue

        if content_type == "text/html" and not raw_filename:
            try:
                html_parts.append(part.get_content())
            except Exception as exc:
                notes.append(f"text/html 解码失败: {exc}")
            continue

        # 3. 附件 / 内嵌图
        payload = part.get_payload(decode=True)
        if not payload:
            continue

        if not filename:
            suffix = {
                "application/pdf": ".pdf",
                "image/jpeg": ".jpg",
                "image/png": ".png",
                "image/tiff": ".tif",
                "message/rfc822": ".eml",
            }.get(content_type, ".bin")
            filename = f"attachment-{sha256_bytes(payload)[:8]}{suffix}"

        disposition = part.get_content_disposition()
        cid = part.get("Content-ID")
        html_text = "\n".join(html_parts)

        is_banner, banner_reason = _is_banner_image(
            payload=payload,
            filename=filename,
            cid=cid,
            html_text=html_text,
            content_type=content_type,
            disposition=disposition,
        )
        if is_banner:
            banner_asset = asset_store.put_bytes(
                payload,
                filename,
                parent_asset_id=asset.asset_id,
                is_inline=True,
                source_type=SourceType.IMAGE,
                mime_type=content_type,
            )
            skipped_inline.append(
                {
                    "file_name": filename,
                    "reason": banner_reason,
                    "asset_id": banner_asset.asset_id,
                    "size_bytes": len(payload),
                }
            )
            notes.append(f"跳过内嵌 banner: {filename}（{banner_reason}）")
            continue

        # application/octet-stream 不可信：后缀 → magic bytes → MIME
        effective_mime = None if content_type == "application/octet-stream" else content_type
        source_type = sniff_source_type(filename, content_type, payload) or SourceType.OTHER

        child_asset = asset_store.put_bytes(
            payload,
            filename,
            parent_asset_id=asset.asset_id,
            is_inline=disposition == "inline" or bool(cid),
            source_type=source_type,
            mime_type=effective_mime,
        )

        from .router import route_asset  # 延迟导入，避免循环依赖

        attachments.append(
            route_asset(
                child_asset,
                asset_store,
                depth=depth + 1,
                max_depth=max_depth,
                visited=visited,
                ocr_client=ocr_client,
                use_ocr=use_ocr,
            )
        )

    plain_text = "\n\n".join(p.strip() for p in plain_parts if p and p.strip())
    html_raw = "\n".join(html_parts)
    html_text = _strip_html(html_raw)

    if plain_text and html_text and html_text not in plain_text:
        body = plain_text + "\n\n" + html_text
    else:
        body = plain_text or html_text

    metadata = {
        "subject": msg.get("subject"),
        "from": msg.get("from"),
        "to": msg.get("to"),
        "cc": msg.get("cc"),
        "date": msg.get("date"),
        "message_id": msg.get("message_id"),
        "file_name": asset.file_name,
        "mime_type": "message/rfc822",
        "depth": depth,
        "attachment_count": len(attachments),
        "skipped_inline_images": skipped_inline,
        "has_html": bool(html_parts),
    }

    return RawDocument(
        doc_id=_new_doc_id(asset, depth),
        asset_id=asset.asset_id,
        source_type=SourceType.EML,
        text=body,
        metadata=metadata,
        attachments=attachments,
        parse_notes=notes,
    )
