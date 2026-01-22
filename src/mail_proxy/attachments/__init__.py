# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Attachment management with flexible routing and caching.

This module provides the AttachmentManager class for retrieving email
attachments from various storage backends with intelligent routing and
optional MD5-based caching.

Supported fetch_mode values:
- endpoint - HTTP POST to tenant's attachment URL (base_url + attachment_path)
- http_url - Direct HTTP fetch from URL in storage_path
- base64 - Inline base64-encoded content in storage_path

Additional attachment parameters:
- content_md5: MD5 hash for cache lookup (alternative to filename marker)
- auth: Authentication override for HTTP requests (uses TenantAuth format)

MD5 marker in filename (legacy):
The filename can include an MD5 marker in the format {MD5:hash} which
enables cache lookup before fetching. The marker is stripped from the
final filename.

Example:
    filename="report_{MD5:a1b2c3d4}.pdf" -> clean filename="report.pdf"

Example:
    Using AttachmentManager with explicit fetch_mode::

        content, filename = await manager.fetch({
            "filename": "report.pdf",
            "storage_path": "https://cdn.example.com/files/123",
            "fetch_mode": "http_url",
            "content_md5": "a1b2c3d4e5f6789012345678901234ab",
            "auth": {"method": "bearer", "token": "cdn-token"}
        })
"""

from __future__ import annotations

import mimetypes
import re
from typing import Any

from .base64_fetcher import Base64Fetcher
from .cache import TieredCache
from .filesystem_fetcher import FilesystemFetcher
from .http_fetcher import HttpFetcher

# Regex pattern for MD5 marker in filename: {MD5:hexstring}
MD5_MARKER_PATTERN = re.compile(r'\{MD5:([a-fA-F0-9]+)\}')


class AttachmentManager:
    """High-level interface for fetching email attachments from multiple sources.

    Routes attachment requests to the appropriate fetcher based on fetch_mode.
    Supports optional MD5-based caching for deduplication and performance.

    Supported fetch_mode values:
    - endpoint - HTTP POST to tenant's attachment URL
    - http_url - Direct HTTP fetch from URL
    - base64 - Inline base64-encoded content

    Attributes:
        _base64_fetcher: Fetcher for base64-encoded inline content.
        _filesystem_fetcher: Fetcher for local filesystem paths.
        _http_fetcher: Fetcher for HTTP endpoints.
        _cache: Optional TieredCache for MD5-based caching.
    """

    def __init__(
        self,
        base_dir: str | None = None,
        http_endpoint: str | None = None,
        http_auth_config: dict[str, str] | None = None,
        cache: TieredCache | None = None,
    ):
        """Initialize the attachment manager with configured fetchers.

        Args:
            base_dir: Base directory for relative filesystem paths.
            http_endpoint: Default HTTP endpoint for endpoint fetch_mode.
            http_auth_config: HTTP authentication config with keys:
                method ("none", "bearer", "basic"), token, user, password.
            cache: Optional TieredCache for MD5-based content caching.
        """
        self._base64_fetcher = Base64Fetcher()
        self._filesystem_fetcher = FilesystemFetcher(base_dir=base_dir)
        self._http_fetcher = HttpFetcher(
            default_endpoint=http_endpoint,
            auth_config=http_auth_config,
        )
        self._cache = cache

    @staticmethod
    def parse_filename(filename: str) -> tuple[str, str | None]:
        """Extract MD5 marker from filename if present.

        Parses filenames like "report_{MD5:a1b2c3d4}.pdf" to extract
        the hash and return a clean filename.

        Args:
            filename: Original filename, possibly containing MD5 marker.

        Returns:
            Tuple of (clean_filename, md5_hash or None).
        """
        match = MD5_MARKER_PATTERN.search(filename)
        if not match:
            return filename, None

        md5_hash = match.group(1).lower()
        # Remove the marker from filename
        clean_filename = MD5_MARKER_PATTERN.sub('', filename)
        # Clean up any double underscores or trailing/leading underscores
        clean_filename = re.sub(r'_+', '_', clean_filename)
        clean_filename = clean_filename.strip('_')
        # Handle case where marker was between name and extension
        clean_filename = re.sub(r'_\.', '.', clean_filename)

        return clean_filename, md5_hash

    def _parse_storage_path(
        self, path: str, fetch_mode: str | None = None
    ) -> tuple[str, str]:
        """Determine the type and parsed content of a storage path.

        Args:
            path: The storage_path value from attachment dict.
            fetch_mode: Explicit fetch mode (required). Valid values:
                "endpoint", "http_url", "base64".

        Returns:
            Tuple of (path_type, parsed_path) where path_type is one of:
            "base64", "http".

        Raises:
            ValueError: If fetch_mode is missing or invalid.
        """
        if not path:
            raise ValueError("Empty storage_path")

        if not fetch_mode:
            raise ValueError("fetch_mode is required")

        if fetch_mode == "endpoint":
            return ("http", path)
        if fetch_mode == "http_url":
            # Wrap URL in brackets for HttpFetcher
            return ("http", f"[{path}]")
        if fetch_mode == "base64":
            return ("base64", path)

        raise ValueError(f"Unknown fetch_mode: {fetch_mode}")

    async def fetch(self, att: dict[str, Any]) -> tuple[bytes, str] | None:
        """Retrieve attachment content with caching and filename cleanup.

        Parses the filename for MD5 marker, checks cache if available,
        fetches from appropriate backend, and caches the result.

        Args:
            att: Attachment specification dictionary containing:
                - filename: Original filename (may contain MD5 marker)
                - storage_path: Location identifier for content
                - fetch_mode: Optional explicit fetch mode
                - content_md5: Optional MD5 hash for cache lookup
                - auth: Optional auth override for HTTP requests

        Returns:
            Tuple of (content_bytes, clean_filename), or None if
            storage_path is not specified.

        Raises:
            ValueError: If storage_path format is invalid.
            FileNotFoundError: If file doesn't exist (filesystem).
            aiohttp.ClientError: If HTTP request fails.
        """
        storage_path = att.get("storage_path")
        if not storage_path:
            return None

        raw_filename = att.get("filename", "file.bin")
        clean_filename, md5_from_marker = self.parse_filename(raw_filename)

        # Get explicit parameters
        content_md5 = att.get("content_md5")
        fetch_mode = att.get("fetch_mode")
        auth = att.get("auth")

        # Use content_md5 if provided, fallback to marker in filename
        cache_key = content_md5 or md5_from_marker

        # Try cache lookup
        if cache_key and self._cache:
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return cached, clean_filename

        # Fetch from backend
        content = await self._fetch_from_backend(
            storage_path, fetch_mode=fetch_mode, auth_override=auth
        )
        if content is None:
            return None

        # Cache the result
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
        """Fetch content from the appropriate backend.

        Args:
            storage_path: The storage path to fetch.
            fetch_mode: Optional explicit fetch mode.
            auth_override: Optional auth config for HTTP requests.

        Returns:
            Binary content, or None if not found.
        """
        path_type, parsed_path = self._parse_storage_path(storage_path, fetch_mode)

        if path_type == "base64":
            return await self._base64_fetcher.fetch(parsed_path)

        if path_type == "filesystem":
            return await self._filesystem_fetcher.fetch(parsed_path)

        if path_type == "http":
            return await self._http_fetcher.fetch(parsed_path, auth_override)

        raise ValueError(f"Unknown path type: {path_type}")

    async def fetch_batch(
        self,
        attachments: list[dict[str, Any]],
    ) -> dict[str, tuple[bytes, str]]:
        """Fetch multiple attachments with batching optimization.

        Groups HTTP requests by server for batching. Other types are
        fetched individually but in parallel.

        Args:
            attachments: List of attachment dicts with storage_path, filename,
                and optionally fetch_mode and content_md5.

        Returns:
            Dictionary mapping storage_path to (content, clean_filename).
        """
        results: dict[str, tuple[bytes, str]] = {}
        to_fetch: dict[str, list[dict[str, Any]]] = {
            "base64": [],
            "filesystem": [],
            "http": [],
        }

        # First pass: check cache and categorize
        for att in attachments:
            storage_path = att.get("storage_path")
            if not storage_path:
                continue

            raw_filename = att.get("filename", "file.bin")
            clean_filename, md5_from_marker = self.parse_filename(raw_filename)

            # Use content_md5 if provided, fallback to marker in filename
            content_md5 = att.get("content_md5")
            cache_key = content_md5 or md5_from_marker

            # Try cache
            if cache_key and self._cache:
                cached = await self._cache.get(cache_key)
                if cached is not None:
                    results[storage_path] = (cached, clean_filename)
                    continue

            # Categorize for fetching
            fetch_mode = att.get("fetch_mode")
            try:
                path_type, _ = self._parse_storage_path(storage_path, fetch_mode)
                to_fetch[path_type].append(att)
            except (ValueError, RuntimeError):
                continue

        # Batch HTTP fetches
        if to_fetch["http"]:
            http_results = await self._http_fetcher.fetch_batch(to_fetch["http"])
            for att in to_fetch["http"]:
                storage_path = att.get("storage_path", "")
                if storage_path in http_results:
                    content = http_results[storage_path]
                    raw_filename = att.get("filename", "file.bin")
                    clean_filename, _ = self.parse_filename(raw_filename)
                    results[storage_path] = (content, clean_filename)
                    # Cache
                    if self._cache:
                        md5 = TieredCache.compute_md5(content)
                        await self._cache.set(md5, content)

        # Fetch other types individually
        for path_type in ["base64", "filesystem"]:
            for att in to_fetch[path_type]:
                try:
                    result = await self.fetch(att)
                    if result:
                        storage_path = att.get("storage_path", "")
                        results[storage_path] = result
                except Exception:
                    continue

        return results

    @staticmethod
    def guess_mime(filename: str) -> tuple[str, str]:
        """Determine the MIME type for a filename based on its extension.

        Uses Python's mimetypes module to detect the appropriate MIME type
        for email attachment encoding. Falls back to application/octet-stream
        for unrecognized extensions.

        Args:
            filename: Name of the file including extension.

        Returns:
            Tuple of (maintype, subtype) for the MIME type. For example,
            "document.pdf" returns ("application", "pdf").
        """
        mt, _ = mimetypes.guess_type(filename)
        if not mt:
            return ("application", "octet-stream")
        return tuple(mt.split("/", 1))  # type: ignore[return-value]
