"""文件 URL 暴露接口。

Phase 1 直接复用 AssetStore.url()：
- 本地：ASSET_PUBLIC_BASE_URL / OCR_PUBLIC_BASE_URL + storage_key
- 生产：OSSAssetStore 生成签名 URL

如果以后要接 cloudflared、临时上传、S3 预签名，只需要实现 FileExposer。
"""

from __future__ import annotations

from typing import Protocol

from ..assets import AssetStore


class FileExposer(Protocol):
    def url(self, asset_id: str) -> str: ...


class AssetStoreExposer:
    def __init__(self, asset_store: AssetStore):
        self.asset_store = asset_store

    def url(self, asset_id: str) -> str:
        return self.asset_store.url(asset_id)
