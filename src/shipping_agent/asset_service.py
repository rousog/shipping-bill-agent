"""AssetService：AssetStore + SQLite 的同步写入。

AssetStore 负责文件字节和 meta.json；
AssetRepository 负责 SQLite assets 表；
AssetService 把两者串起来，避免元数据两边漂移。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .assets import AssetStore, build_asset_store
from .config import Settings
from .db.repositories import AssetRepository
from .models import AssetRef, SourceType


class AssetService:
    def __init__(
        self,
        store: AssetStore,
        repository: AssetRepository | None = None,
    ):
        self.store = store
        self.repository = repository

    def _persist(self, asset: AssetRef) -> AssetRef:
        if self.repository is not None:
            self.repository.upsert(asset)
        return asset

    def put(
        self,
        local_path: str | Path,
        *,
        parent_asset_id: str | None = None,
        is_inline: bool = False,
        source_type: SourceType | None = None,
    ) -> AssetRef:
        return self._persist(
            self.store.put(
                local_path,
                parent_asset_id=parent_asset_id,
                is_inline=is_inline,
                source_type=source_type,
            )
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
        return self._persist(
            self.store.put_bytes(
                data,
                file_name,
                parent_asset_id=parent_asset_id,
                is_inline=is_inline,
                source_type=source_type,
                mime_type=mime_type,
            )
        )

    def get(self, asset_id: str) -> bytes:
        return self.store.get(asset_id)

    def path(self, asset_id: str) -> Path:
        return self.store.path(asset_id)

    def url(self, asset_id: str, expires_in: int = 3600) -> str:
        return self.store.url(asset_id, expires_in=expires_in)

    def meta(self, asset_id: str) -> dict:
        return self.store.meta(asset_id)


def build_asset_service(
    settings: Settings,
    conn: sqlite3.Connection | None = None,
) -> AssetService:
    store = build_asset_store(settings)
    repository = AssetRepository(conn) if conn is not None else None
    return AssetService(store, repository)
