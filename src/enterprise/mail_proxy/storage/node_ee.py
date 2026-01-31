# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition: Cloud storage backend support via fsspec.

This mixin adds S3, Azure, and GCS support to StorageNode.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fsspec import AbstractFileSystem

logger = logging.getLogger(__name__)


class StorageNode_EE:
    """Enterprise Edition mixin: Cloud storage backends via fsspec.

    Supports:
    - S3 (protocol: 's3')
    - Google Cloud Storage (protocol: 'gcs')
    - Azure Blob (protocol: 'azure')

    Usage (mixed into StorageNode):
        class StorageNode(StorageNode_EE, StorageNodeBase):
            pass
    """

    _fs_cache: dict[str, AbstractFileSystem] = {}

    def _get_fs(self) -> AbstractFileSystem:
        """Get or create fsspec filesystem for this mount."""
        mount_name = self._mount_name  # type: ignore[attr-defined]
        config = self._config  # type: ignore[attr-defined]

        if mount_name in self._fs_cache:
            return self._fs_cache[mount_name]

        try:
            import fsspec
        except ImportError as e:
            raise ImportError(
                "Cloud storage requires fsspec. "
                "Install with: pip install genro-mail-proxy[cloud-storage]"
            ) from e

        protocol = config.get("protocol", "local")

        if protocol == "s3":
            fs = fsspec.filesystem(
                "s3",
                key=config.get("aws_access_key_id"),
                secret=config.get("aws_secret_access_key"),
                endpoint_url=config.get("endpoint_url"),
                client_kwargs=config.get("client_kwargs", {}),
            )
        elif protocol == "gcs":
            fs = fsspec.filesystem(
                "gcs",
                project=config.get("project"),
                token=config.get("token"),
            )
        elif protocol == "azure":
            fs = fsspec.filesystem(
                "az",
                account_name=config.get("account_name"),
                account_key=config.get("account_key"),
                connection_string=config.get("connection_string"),
            )
        else:
            raise ValueError(f"Unsupported cloud protocol: {protocol}")

        self._fs_cache[mount_name] = fs
        return fs

    def _get_cloud_path(self) -> str:
        """Get the full path for cloud storage."""
        config = self._config  # type: ignore[attr-defined]
        path = self._path  # type: ignore[attr-defined]
        protocol = config.get("protocol")

        if protocol == "s3" or protocol == "gcs":
            bucket = config.get("bucket", "")
            prefix = config.get("prefix", "").strip("/")
            if prefix:
                return f"{bucket}/{prefix}/{path}"
            return f"{bucket}/{path}"

        elif protocol == "azure":
            container = config.get("container", "")
            prefix = config.get("prefix", "").strip("/")
            if prefix:
                return f"{container}/{prefix}/{path}"
            return f"{container}/{path}"

        return path

    # ----------------------------------------------------------------- Cloud I/O

    async def _cloud_exists(self) -> bool:
        fs = self._get_fs()
        return fs.exists(self._get_cloud_path())

    async def _cloud_is_file(self) -> bool:
        fs = self._get_fs()
        return fs.isfile(self._get_cloud_path())

    async def _cloud_is_dir(self) -> bool:
        fs = self._get_fs()
        return fs.isdir(self._get_cloud_path())

    async def _cloud_size(self) -> int:
        fs = self._get_fs()
        size = fs.size(self._get_cloud_path())
        return int(size) if size is not None else 0

    async def _cloud_mtime(self) -> float:
        fs = self._get_fs()
        info = fs.info(self._get_cloud_path())
        mtime = info.get("mtime") or info.get("LastModified")
        if mtime is None:
            return 0.0
        if hasattr(mtime, "timestamp"):
            return mtime.timestamp()  # type: ignore[union-attr]
        return float(mtime)

    async def _cloud_read_bytes(self) -> bytes:
        fs = self._get_fs()
        with fs.open(self._get_cloud_path(), "rb") as f:
            data = f.read()
            return data if isinstance(data, bytes) else data.encode()

    async def _cloud_write_bytes(self, data: bytes) -> None:
        fs = self._get_fs()
        cloud_path = self._get_cloud_path()

        # Ensure parent directory exists (for some backends)
        parent = "/".join(cloud_path.split("/")[:-1])
        if parent:
            fs.makedirs(parent, exist_ok=True)

        with fs.open(cloud_path, "wb") as f:
            f.write(data)  # type: ignore[arg-type]

    async def _cloud_delete(self) -> bool:
        fs = self._get_fs()
        cloud_path = self._get_cloud_path()
        if not fs.exists(cloud_path):
            return False
        if fs.isdir(cloud_path):
            fs.rm(cloud_path, recursive=True)
        else:
            fs.rm(cloud_path)
        return True

    async def _cloud_mkdir(self, parents: bool, exist_ok: bool) -> None:
        fs = self._get_fs()
        fs.makedirs(self._get_cloud_path(), exist_ok=exist_ok)

    async def _cloud_children(self) -> list:
        fs = self._get_fs()
        cloud_path = self._get_cloud_path()

        if not fs.isdir(cloud_path):
            return []

        children = []
        for item in fs.ls(cloud_path, detail=False):
            # item is full path, extract just the name
            name = item.rstrip("/").split("/")[-1]
            children.append(self.child(name))  # type: ignore[attr-defined]

        return children

    def _cloud_url(self, expires_in: int) -> str:
        """Generate presigned URL for cloud storage."""
        fs = self._get_fs()
        cloud_path = self._get_cloud_path()

        if hasattr(fs, "sign"):
            return fs.sign(cloud_path, expiration=expires_in)

        # Fallback for backends without native signing
        raise NotImplementedError(
            f"Protocol '{self._config.get('protocol')}' does not support presigned URLs"  # type: ignore[attr-defined]
        )


__all__ = ["StorageNode_EE"]
