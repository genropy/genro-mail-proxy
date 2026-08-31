# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Filesystem attachment fetcher.

This module provides a fetcher for attachments stored on the local
filesystem, supporting both absolute and relative paths.

Security: Path traversal attacks are prevented by validating that
resolved paths stay within the configured base directory.

Example:
    Fetching a file from the filesystem::

        fetcher = FilesystemFetcher(base_dir="/var/mail-service/files")

        # Absolute path (must still be under base_dir if configured)
        content = await fetcher.fetch("/var/mail-service/files/doc.pdf")

        # Relative path (resolved against base_dir)
        content = await fetcher.fetch("uploads/report.pdf")
"""

from __future__ import annotations

import asyncio
from pathlib import Path


class FilesystemFetcher:
    """Fetcher for local filesystem attachments.

    Supports absolute paths and relative paths (resolved against base_dir).
    Implements path traversal protection to prevent unauthorized file access.

    Attributes:
        _base_dir: Base directory for relative paths and security boundary.
    """

    def __init__(self, base_dir: str | None = None):
        """Initialize the filesystem fetcher.

        Args:
            base_dir: Base directory for relative paths. If provided,
                all paths (including absolute) must resolve to within
                this directory. If None, only absolute paths are allowed
                and no security boundary is enforced.
        """
        self._base_dir: Path | None = None
        if base_dir:
            self._base_dir = Path(base_dir).resolve()

    async def fetch(self, path: str) -> bytes | None:
        """Read file content from the filesystem.

        Args:
            path: File path (absolute or relative to base_dir).

        Returns:
            Binary content of the file.

        Raises:
            ValueError: If path traversal is detected or path is invalid.
            FileNotFoundError: If the file does not exist.
            PermissionError: If the file cannot be read.
        """
        if not path:
            raise ValueError("Empty path provided")

        resolved_path = self._resolve_and_validate(path)
        return await asyncio.to_thread(resolved_path.read_bytes)

    def _resolve_and_validate(self, path: str) -> Path:
        """Resolve path and validate it's safe to access.

        Args:
            path: The requested path.

        Returns:
            Resolved absolute Path object.

        Raises:
            ValueError: If path is invalid or escapes base_dir.
        """
        path_obj = Path(path)

        if path_obj.is_absolute():
            resolved = path_obj.resolve()
        elif self._base_dir:
            # Relative path - resolve against base_dir
            resolved = (self._base_dir / path_obj).resolve()
        else:
            raise ValueError(
                f"Relative path '{path}' not allowed without base_dir configuration"
            )

        # Security check: ensure path is within base_dir
        if self._base_dir:
            try:
                resolved.relative_to(self._base_dir)
            except ValueError:
                raise ValueError(
                    f"Path traversal detected: '{path}' resolves outside base directory"
                ) from None

        # Additional safety checks
        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {resolved}")

        if not resolved.is_file():
            raise ValueError(f"Not a regular file: {resolved}")

        return resolved

    @property
    def base_dir(self) -> Path | None:
        """The configured base directory."""
        return self._base_dir
