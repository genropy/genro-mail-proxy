# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Attachment fetching from multiple storage backends.

Provides AttachmentManager for retrieving email attachments with:
- Base64 inline content decoding
- Filesystem fetching with path traversal protection
- HTTP fetching with authentication support
- Optional MD5-based caching

Supported fetch_mode values:
- endpoint: HTTP POST to tenant's attachment URL
- http_url: Direct HTTP fetch from URL in storage_path
- base64: Inline base64-encoded content in storage_path
- filesystem: Local filesystem path (absolute or relative to base_dir)

If fetch_mode is not specified, it is inferred from storage_path format:
- ``base64:...`` prefix -> base64 (prefix is stripped)
- ``http://`` or ``https://`` prefix -> http_url
- ``/`` (absolute path) -> filesystem
- otherwise -> endpoint (default)
"""

from __future__ import annotations

import asyncio
import base64
import mimetypes
import re
from pathlib import Path
from typing import Any

import aiohttp

from .cache import TieredCache

MD5_MARKER_PATTERN = re.compile(r'\{MD5:([a-fA-F0-9]+)\}')


class Base64Fetcher:
    """Decoder for base64-encoded inline attachment content."""

    async def fetch(self, base64_content: str) -> bytes | None:
        if not base64_content:
            return None

        try:
            content = base64_content.strip()
            padding_needed = 4 - (len(content) % 4)
            if padding_needed != 4:
                content += "=" * padding_needed

            return base64.b64decode(content, validate=True)
        except Exception as e:
            raise ValueError(f"Invalid base64 content: {e}") from e


class FilesystemFetcher:
    """Fetcher for local filesystem attachments with path traversal protection."""

    def __init__(self, base_dir: str | None = None):
        self._base_dir: Path | None = None
        if base_dir:
            self._base_dir = Path(base_dir).resolve()

    async def fetch(self, path: str) -> bytes | None:
        if not path:
            raise ValueError("Empty path provided")

        resolved_path = self._resolve_and_validate(path)
        return await asyncio.to_thread(resolved_path.read_bytes)

    def _resolve_and_validate(self, path: str) -> Path:
        path_obj = Path(path)

        if path_obj.is_absolute():
            resolved = path_obj.resolve()
        elif self._base_dir:
            resolved = (self._base_dir / path_obj).resolve()
        else:
            raise ValueError(
                f"Relative path '{path}' not allowed without base_dir configuration"
            )

        if self._base_dir:
            try:
                resolved.relative_to(self._base_dir)
            except ValueError:
                raise ValueError(
                    f"Path traversal detected: '{path}' resolves outside base directory"
                ) from None

        if not resolved.exists():
            raise FileNotFoundError(f"File not found: {resolved}")

        if not resolved.is_file():
            raise ValueError(f"Not a regular file: {resolved}")

        return resolved

    @property
    def base_dir(self) -> Path | None:
        return self._base_dir


class HttpFetcher:
    """Fetcher for HTTP-served attachments with authentication support."""

    def __init__(
        self,
        default_endpoint: str | None = None,
        auth_config: dict[str, str] | None = None,
    ):
        self._default_endpoint = default_endpoint
        self._auth_config = auth_config or {}

    def _parse_path(self, path: str) -> tuple[str, str]:
        if path.startswith("["):
            match = re.match(r'\[([^\]]+)\](.*)', path)
            if not match:
                raise ValueError(f"Invalid HTTP path format: {path}")
            return match.group(1), match.group(2)

        if path.startswith(("http://", "https://")):
            return path, ""

        if not self._default_endpoint:
            raise ValueError(
                "No default endpoint configured and path doesn't specify one"
            )
        return self._default_endpoint, path

    def _get_auth_headers(
        self, auth_override: dict[str, str] | None = None
    ) -> dict[str, str]:
        auth_config = auth_override if auth_override is not None else self._auth_config
        method = auth_config.get("method", "none")

        if method == "bearer":
            token = auth_config.get("token", "")
            return {"Authorization": f"Bearer {token}"}

        if method == "basic":
            user = auth_config.get("user", "")
            password = auth_config.get("password", "")
            credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
            return {"Authorization": f"Basic {credentials}"}

        return {}

    async def fetch(
        self, path: str, auth_override: dict[str, str] | None = None
    ) -> bytes:
        server_url, params = self._parse_path(path)
        headers = self._get_auth_headers(auth_override)

        async with aiohttp.ClientSession() as session:
            if not params:
                async with session.get(server_url, headers=headers) as response:
                    response.raise_for_status()
                    return await response.read()
            else:
                async with session.post(
                    server_url,
                    json={"storage_path": params},
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    return await response.read()

    @property
    def default_endpoint(self) -> str | None:
        return self._default_endpoint


class AttachmentManager:
    """High-level interface for fetching email attachments from multiple sources."""

    def __init__(
        self,
        base_dir: str | None = None,
        http_endpoint: str | None = None,
        http_auth_config: dict[str, str] | None = None,
        cache: TieredCache | None = None,
    ):
        self._base64_fetcher = Base64Fetcher()
        self._filesystem_fetcher = FilesystemFetcher(base_dir=base_dir)
        self._http_fetcher = HttpFetcher(
            default_endpoint=http_endpoint,
            auth_config=http_auth_config,
        )
        self._cache = cache

    @staticmethod
    def parse_filename(filename: str) -> tuple[str, str | None]:
        """Extract MD5 marker from filename if present."""
        match = MD5_MARKER_PATTERN.search(filename)
        if not match:
            return filename, None

        md5_hash = match.group(1).lower()
        clean_filename = MD5_MARKER_PATTERN.sub('', filename)
        clean_filename = re.sub(r'_+', '_', clean_filename)
        clean_filename = clean_filename.strip('_')
        clean_filename = re.sub(r'_\.', '.', clean_filename)

        return clean_filename, md5_hash

    def _parse_storage_path(
        self, path: str, fetch_mode: str | None = None
    ) -> tuple[str, str]:
        if not path:
            raise ValueError("Empty storage_path")

        if not fetch_mode:
            if path.startswith("base64:"):
                fetch_mode = "base64"
                path = path[7:]
            elif path.startswith(("http://", "https://")):
                fetch_mode = "http_url"
            elif path.startswith("/"):
                fetch_mode = "filesystem"
            else:
                fetch_mode = "endpoint"

        if fetch_mode == "endpoint":
            return ("http", path)
        if fetch_mode == "http_url":
            return ("http", f"[{path}]")
        if fetch_mode == "base64":
            if path.startswith("base64:"):
                path = path[7:]
            return ("base64", path)
        if fetch_mode == "filesystem":
            return ("filesystem", path)

        raise ValueError(f"Unknown fetch_mode: {fetch_mode}")

    async def fetch(self, att: dict[str, Any]) -> tuple[bytes, str] | None:
        """Retrieve attachment content with caching and filename cleanup."""
        storage_path = att.get("storage_path")
        if not storage_path:
            return None

        raw_filename = att.get("filename", "file.bin")
        clean_filename, md5_from_marker = self.parse_filename(raw_filename)

        content_md5 = att.get("content_md5")
        fetch_mode = att.get("fetch_mode")
        auth = att.get("auth")

        cache_key = content_md5 or md5_from_marker

        if cache_key and self._cache:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached, clean_filename

        content = await self._fetch_from_backend(
            storage_path, fetch_mode=fetch_mode, auth_override=auth
        )
        if content is None:
            return None

        if self._cache:
            actual_md5 = TieredCache.compute_md5(content)
            await self._cache.set(actual_md5, content)

        return content, clean_filename

    async def _fetch_from_backend(
        self,
        storage_path: str,
        fetch_mode: str | None = None,
        auth_override: dict[str, Any] | None = None,
    ) -> bytes | None:
        path_type, parsed_path = self._parse_storage_path(storage_path, fetch_mode)

        if path_type == "base64":
            return await self._base64_fetcher.fetch(parsed_path)

        if path_type == "filesystem":
            return await self._filesystem_fetcher.fetch(parsed_path)

        if path_type == "http":
            return await self._http_fetcher.fetch(parsed_path, auth_override)

        raise ValueError(f"Unknown path type: {path_type}")

    @staticmethod
    def guess_mime(filename: str) -> tuple[str, str]:
        """Determine the MIME type for a filename based on its extension."""
        mt, _ = mimetypes.guess_type(filename)
        if not mt:
            return ("application", "octet-stream")
        return tuple(mt.split("/", 1))  # type: ignore[return-value]
