# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Large file storage backend using fsspec for multi-cloud support.

This module provides storage capabilities for large email attachments,
supporting multiple backends (S3, GCS, Azure, local filesystem) via fsspec.

When attachments exceed the configured size threshold, they are uploaded
to external storage and replaced with download links in the email body.

Example:
    Using S3 storage::

        storage = LargeFileStorage(
            storage_url="s3://my-bucket/mail-attachments",
        )
        await storage.upload("file-123", content, "report.pdf")
        url = storage.get_download_url("file-123", "report.pdf")

    Using local filesystem with public URL::

        storage = LargeFileStorage(
            storage_url="file:///var/www/downloads",
            public_base_url="https://files.example.com",
        )
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fsspec import AbstractFileSystem

logger = logging.getLogger(__name__)

# Secret key for signing download tokens (should be configured in production)
_DEFAULT_SECRET_KEY = "genro-mail-proxy-default-secret-change-me"


class LargeFileStorageError(Exception):
    """Base exception for large file storage errors."""

    pass


class StorageNotConfiguredError(LargeFileStorageError):
    """Raised when storage is not properly configured."""

    pass


class UploadError(LargeFileStorageError):
    """Raised when file upload fails."""

    pass


class LargeFileStorage:
    """Storage backend for large email attachments using fsspec.

    Supports multiple storage backends through fsspec's unified interface:
    - S3/MinIO: s3://bucket/path
    - Google Cloud Storage: gs://bucket/path
    - Azure Blob: az://container/path
    - Local filesystem: file:///path/to/dir

    For cloud storage (S3, GCS, Azure), download URLs are generated using
    the backend's native signed URL mechanism. For local filesystem, a
    public_base_url must be provided and signed tokens are generated.

    Attributes:
        storage_url: fsspec-compatible URL for the storage backend.
        public_base_url: Base URL for download links (required for local storage).
        secret_key: Secret key for signing download tokens.
    """

    def __init__(
        self,
        storage_url: str,
        public_base_url: str | None = None,
        secret_key: str | None = None,
        storage_options: dict[str, Any] | None = None,
    ):
        """Initialize the large file storage backend.

        Args:
            storage_url: fsspec URL (s3://bucket/path, file:///data, etc.).
            public_base_url: Public URL for download links (required for local storage).
            secret_key: Secret key for signing download tokens.
            storage_options: Additional options passed to fsspec filesystem.

        Raises:
            ImportError: If fsspec is not installed.
        """
        self.storage_url = storage_url
        self.public_base_url = public_base_url
        self.secret_key = secret_key or _DEFAULT_SECRET_KEY
        self._storage_options = storage_options or {}

        # Lazy initialization
        self._fs: AbstractFileSystem | None = None
        self._base_path: str | None = None

    def _init_fs(self) -> None:
        """Initialize the fsspec filesystem lazily."""
        if self._fs is not None:
            return

        try:
            import fsspec
        except ImportError as e:
            raise ImportError(
                "Large file storage requires fsspec. "
                "Install with: pip install genro-mail-proxy[large-files]"
            ) from e

        self._fs, self._base_path = fsspec.core.url_to_fs(self.storage_url, **self._storage_options)

    @property
    def fs(self) -> AbstractFileSystem:
        """The fsspec filesystem instance."""
        self._init_fs()
        return self._fs  # type: ignore[return-value]

    @property
    def base_path(self) -> str:
        """The base path within the storage backend."""
        self._init_fs()
        return self._base_path or ""

    def _get_file_path(self, file_id: str, filename: str) -> str:
        """Build the full storage path for a file."""
        return f"{self.base_path}/{file_id}/{filename}"

    async def upload(self, file_id: str, content: bytes, filename: str) -> str:
        """Upload a file to storage.

        Args:
            file_id: Unique identifier for this file (e.g., UUID).
            content: File content as bytes.
            filename: Original filename.

        Returns:
            The storage path where the file was uploaded.

        Raises:
            UploadError: If upload fails.
        """
        path = self._get_file_path(file_id, filename)

        try:
            # Ensure parent directory exists
            parent = f"{self.base_path}/{file_id}"
            self.fs.makedirs(parent, exist_ok=True)

            # Upload file
            with self.fs.open(path, "wb") as f:
                f.write(content)

            logger.info(f"Uploaded {len(content)} bytes to {path}")
            return path

        except Exception as e:
            raise UploadError(f"Failed to upload {filename} to {path}: {e}") from e

    def get_download_url(self, file_id: str, filename: str, expires_in: int = 86400) -> str:
        """Generate a download URL for a file.

        For cloud storage backends (S3, GCS, Azure), uses the backend's
        native signed URL mechanism. For local filesystem, generates a
        signed token URL using public_base_url.

        Args:
            file_id: The file's unique identifier.
            filename: The original filename.
            expires_in: URL expiration time in seconds (default: 24 hours).

        Returns:
            A download URL for the file.

        Raises:
            StorageNotConfiguredError: If public_base_url is required but not set.
        """
        path = self._get_file_path(file_id, filename)

        # Check if filesystem supports native signing (S3, GCS, Azure)
        if hasattr(self.fs, "sign"):
            try:
                return self.fs.sign(path, expiration=expires_in)
            except Exception as e:
                logger.warning(f"Native URL signing failed: {e}, falling back to token")

        # Fall back to token-based URL for local filesystem
        if not self.public_base_url:
            raise StorageNotConfiguredError(
                "public_base_url is required for local filesystem storage. "
                "Set it to the base URL where files are served (e.g., https://files.example.com)"
            )

        token = self._generate_signed_token(file_id, filename, expires_in)
        return f"{self.public_base_url.rstrip('/')}/download/{token}/{filename}"

    def _generate_signed_token(self, file_id: str, filename: str, expires_in: int) -> str:
        """Generate a signed token for secure download URLs.

        The token contains the file_id, expiration timestamp, and a signature
        that can be verified to ensure the URL hasn't been tampered with.

        Args:
            file_id: The file's unique identifier.
            filename: The original filename.
            expires_in: Token validity in seconds.

        Returns:
            A signed token string.
        """
        expires_at = int(time.time()) + expires_in
        message = f"{file_id}:{filename}:{expires_at}"
        signature = hmac.new(
            self.secret_key.encode(), message.encode(), hashlib.sha256
        ).hexdigest()[:16]

        return f"{file_id}-{expires_at}-{signature}"

    def verify_download_token(self, token: str, filename: str) -> str | None:
        """Verify a download token and return the file_id if valid.

        Args:
            token: The token from the download URL.
            filename: The filename from the URL (for verification).

        Returns:
            The file_id if token is valid and not expired, None otherwise.
        """
        try:
            parts = token.rsplit("-", 2)
            if len(parts) != 3:
                return None

            file_id, expires_at_str, signature = parts
            expires_at = int(expires_at_str)

            # Check expiration
            if time.time() > expires_at:
                logger.debug(f"Token expired: {token}")
                return None

            # Verify signature
            message = f"{file_id}:{filename}:{expires_at}"
            expected_sig = hmac.new(
                self.secret_key.encode(), message.encode(), hashlib.sha256
            ).hexdigest()[:16]

            if not hmac.compare_digest(signature, expected_sig):
                logger.warning(f"Invalid token signature: {token}")
                return None

            return file_id

        except (ValueError, IndexError) as e:
            logger.warning(f"Token parsing failed: {token}, error: {e}")
            return None

    def get_file_content(self, file_id: str, filename: str) -> bytes | None:
        """Retrieve file content from storage.

        Args:
            file_id: The file's unique identifier.
            filename: The original filename.

        Returns:
            File content as bytes, or None if not found.
        """
        path = self._get_file_path(file_id, filename)
        try:
            with self.fs.open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    async def cleanup_expired(self, ttl_days: int) -> int:
        """Remove files older than the specified TTL.

        Args:
            ttl_days: Delete files older than this many days.

        Returns:
            Number of files deleted.
        """
        cutoff = datetime.now(timezone.utc).timestamp() - (ttl_days * 86400)
        deleted = 0

        try:
            # List all file_id directories
            if not self.fs.exists(self.base_path):
                return 0

            for item in self.fs.ls(self.base_path, detail=True):
                if item.get("type") == "directory":
                    # Check directory modification time
                    mtime = item.get("mtime") or item.get("LastModified")
                    if mtime:
                        if isinstance(mtime, datetime):
                            mtime = mtime.timestamp()
                        if mtime < cutoff:
                            path = item.get("name") or item.get("Key")
                            if path:
                                self.fs.rm(path, recursive=True)
                                deleted += 1
                                logger.info(f"Deleted expired files at {path}")

        except Exception as e:
            logger.error(f"Cleanup failed: {e}")

        return deleted

    def exists(self, file_id: str, filename: str) -> bool:
        """Check if a file exists in storage.

        Args:
            file_id: The file's unique identifier.
            filename: The original filename.

        Returns:
            True if file exists, False otherwise.
        """
        path = self._get_file_path(file_id, filename)
        return self.fs.exists(path)
