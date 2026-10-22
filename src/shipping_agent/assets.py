"""资产存储：原始文件、OCR 结果、抽取结果的统一入口。

Phase 0 实现 LocalAssetStore；OSSAssetStore 先留接口，生产可替换。
"""

from __future__ import annotations

import io
import json
import mimetypes
import re
import zipfile
from pathlib import Path
from typing import Protocol

from .config import Settings
from .models import AssetRef, SourceType
from .utils.hashing import sha256_bytes

_MIME_OVERRIDES = {
    ".eml": "message/rfc822",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}

_SOURCE_BY_SUFFIX = {
    ".eml": SourceType.EML,
    ".pdf": SourceType.PDF,
    ".png": SourceType.IMAGE,
    ".jpg": SourceType.IMAGE,
    ".jpeg": SourceType.IMAGE,
    ".tif": SourceType.IMAGE,
    ".tiff": SourceType.IMAGE,
    ".xls": SourceType.EXCEL,
    ".xlsx": SourceType.EXCEL,
    ".xlsm": SourceType.EXCEL,
    ".doc": SourceType.WORD,
    ".docx": SourceType.WORD,
    ".json": SourceType.JSON,
}


def infer_source_type(path: str | Path) -> SourceType | None:
    return _SOURCE_BY_SUFFIX.get(Path(path).suffix.lower())


def infer_mime_type(path: str | Path) -> str | None:
    suffix = Path(path).suffix.lower()
    if suffix in _MIME_OVERRIDES:
        return _MIME_OVERRIDES[suffix]
    mime, _ = mimetypes.guess_type(str(path))
    return mime


_MIME_SOURCE = {
    "application/pdf": SourceType.PDF,
    "image/jpeg": SourceType.IMAGE,
    "image/png": SourceType.IMAGE,
    "image/tiff": SourceType.IMAGE,
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": SourceType.EXCEL,
    "application/vnd.ms-excel": SourceType.EXCEL,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": SourceType.WORD,
    "application/msword": SourceType.WORD,
    "message/rfc822": SourceType.EML,
}


def infer_source_type_from_mime(mime_type: str | None) -> SourceType | None:
    return _MIME_SOURCE.get(mime_type or "")


def sniff_source_type(
    file_name: str | Path,
    mime_type: str | None = None,
    payload: bytes | None = None,
) -> SourceType | None:
    """按「后缀 → magic bytes → MIME」判断真实类型。

    MIME 只是发件人贴的标签，可能是 application/octet-stream 或完全错误。
    """
    by_suffix = infer_source_type(file_name)
    if by_suffix:
        return by_suffix

    if payload:
        if payload.startswith(b"%PDF-"):
            return SourceType.PDF
        if payload.startswith(b"\x89PNG\r\n\x1a\n"):
            return SourceType.IMAGE
        if payload.startswith(b"\xff\xd8\xff"):
            return SourceType.IMAGE
        if payload[:4] in (b"II*\x00", b"MM\x00*"):
            return SourceType.IMAGE
        if payload.startswith(b"PK\x03\x04"):
            # OOXML: xlsx / docx 都是 zip
            try:
                with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                    names = set(archive.namelist())
                if "xl/workbook.xml" in names:
                    return SourceType.EXCEL
                if "word/document.xml" in names:
                    return SourceType.WORD
            except zipfile.BadZipFile:
                pass

    return infer_source_type_from_mime(mime_type)


_MIME_SUFFIX = {
    "application/pdf": ".pdf",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/tiff": ".tif",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "application/msword": ".doc",
    "message/rfc822": ".eml",
}


def _safe_filename(name: str) -> str:
    """清理附件名。

    EML 里常见非断行空格（\\xa0），以及路径分隔符；都要处理。
    """
    name = (name or "").replace("\xa0", " ").strip()
    name = re.sub(r'[\\/:*?"<>|]+', "_", name)
    return name or "attachment"


def _suffix_for_mime(mime_type: str | None) -> str:
    return _MIME_SUFFIX.get(mime_type or "", ".bin")


class AssetStore(Protocol):
    def put(
        self,
        local_path: str | Path,
        *,
        parent_asset_id: str | None = None,
        is_inline: bool = False,
        source_type: SourceType | None = None,
    ) -> AssetRef: ...

    def put_bytes(
        self,
        data: bytes,
        file_name: str,
        *,
        parent_asset_id: str | None = None,
        is_inline: bool = False,
        source_type: SourceType | None = None,
        mime_type: str | None = None,
    ) -> AssetRef: ...

    def get(self, asset_id: str) -> bytes: ...

    def path(self, asset_id: str) -> Path: ...

    def url(self, asset_id: str, expires_in: int = 3600) -> str: ...

    def meta(self, asset_id: str) -> dict: ...


class LocalAssetStore:
    """本地资产库。

    目录结构：
        {root}/{sha[:2]}/{sha}/original.{ext}
        {root}/{sha[:2]}/{sha}/meta.json
    """

    def __init__(self, root: str | Path, public_base_url: str | None = None):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None

    def put(
        self,
        local_path: str | Path,
        *,
        parent_asset_id: str | None = None,
        is_inline: bool = False,
        source_type: SourceType | None = None,
    ) -> AssetRef:
        source = Path(local_path)
        if not source.exists():
            raise FileNotFoundError(f"asset not found: {source}")
        return self.put_bytes(
            source.read_bytes(),
            source.name,
            parent_asset_id=parent_asset_id,
            is_inline=is_inline,
            source_type=source_type or infer_source_type(source),
            mime_type=infer_mime_type(source),
        )

    def put_bytes(
        self,
        data: bytes,
        file_name: str,
        *,
        parent_asset_id: str | None = None,
        is_inline: bool = False,
        source_type: SourceType | None = None,
        mime_type: str | None = None,
    ) -> AssetRef:
        digest = sha256_bytes(data)
        safe_name = _safe_filename(file_name)
        suffix = Path(safe_name).suffix.lower() or _suffix_for_mime(mime_type)

        asset_dir = self.root / digest[:2] / digest
        asset_dir.mkdir(parents=True, exist_ok=True)

        target = asset_dir / f"original{suffix}"
        if not target.exists():
            target.write_bytes(data)

        rel_key = target.relative_to(self.root).as_posix()
        asset = AssetRef(
            asset_id=digest,
            sha256=digest,
            file_name=safe_name,
            mime_type=mime_type or infer_mime_type(safe_name),
            size_bytes=len(data),
            storage_backend="local",
            storage_key=rel_key,
            parent_asset_id=parent_asset_id,
            source_type=source_type or infer_source_type(safe_name),
            is_inline=is_inline,
        )

        meta_path = asset_dir / "meta.json"
        meta_path.write_text(
            json.dumps(asset.model_dump(mode="json"), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return asset

    def path(self, asset_id: str) -> Path:
        asset_dir = self.root / asset_id[:2] / asset_id
        meta_path = asset_dir / "meta.json"
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            return self.root / meta["storage_key"]
        matches = sorted(asset_dir.glob("original.*"))
        if not matches:
            raise FileNotFoundError(f"asset not found: {asset_id}")
        return matches[0]

    def get(self, asset_id: str) -> bytes:
        return self.path(asset_id).read_bytes()

    def url(self, asset_id: str, expires_in: int = 3600) -> str:
        """返回 OCR 服务可访问的 URL。

        Phase 0 只支持配置一个公网前缀；没有配置时明确报错，避免静默失败。
        后续可接 LocalTunnel / OSS 签名 URL。
        """
        if not self.public_base_url:
            raise RuntimeError(
                "ASSET_PUBLIC_BASE_URL / OCR_PUBLIC_BASE_URL 未配置。"
                "OCR 需要一个公网可访问的文件 URL。"
            )
        rel = str(self.path(asset_id).relative_to(self.root)).replace("\\", "/")
        return f"{self.public_base_url}/{rel}"

    def meta(self, asset_id: str) -> dict:
        meta_path = self.root / asset_id[:2] / asset_id / "meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"asset meta not found: {asset_id}")
        return json.loads(meta_path.read_text(encoding="utf-8"))


class OSSAssetStore:
    """OSS/S3 适配器占位。

    生产可接阿里云 OSS、MinIO 或 S3；本题 Phase 0 不实现。
    """

    def __init__(self, *args, **kwargs):
        raise NotImplementedError(
            "OSSAssetStore 尚未实现；Phase 0 使用 LocalAssetStore。"
        )


def build_asset_store(settings: Settings) -> AssetStore:
    backend = settings.asset_backend.lower()
    if backend == "local":
        return LocalAssetStore(
            settings.asset_root,
            public_base_url=settings.asset_public_base_url
            or settings.ocr_public_base_url,
        )
    if backend in {"oss", "s3", "minio"}:
        return OSSAssetStore()  # type: ignore[return-value]
    raise ValueError(f"unsupported ASSET_BACKEND: {settings.asset_backend}")
