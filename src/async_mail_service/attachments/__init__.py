"""Attachment management with flexible routing and caching.

This module provides the AttachmentManager class for retrieving email
attachments from various storage backends with intelligent routing and
optional MD5-based caching.

Supported storage path formats:
- volume:path - genro-storage volume (requires genro-storage)
- base64:content - Inline base64-encoded content
- /absolute/path - Local filesystem absolute path
- relative/path - Local filesystem path relative to base_dir
- @params - HTTP POST to default endpoint
- @[url]params - HTTP POST to specific URL

MD5 marker in filename:
The filename can include an MD5 marker in the format {MD5:hash} which
enables cache lookup before fetching. The marker is stripped from the
final filename.

Example:
    filename="report_{MD5:a1b2c3d4}.pdf" -> clean filename="report.pdf"

Example:
    Using AttachmentManager with various backends::

        from async_mail_service.attachments import AttachmentManager

        manager = AttachmentManager(
            storage_manager=storage,  # Optional genro-storage
            base_dir="/var/files",    # For filesystem paths
            http_endpoint="https://api.example.com/files",
            cache=tiered_cache,       # Optional TieredCache
        )

        # Fetch with MD5 cache lookup
        content, filename = await manager.fetch({
            "filename": "report_{MD5:a1b2c3}.pdf",
            "storage_path": "docs:report.pdf"
        })
"""

from __future__ import annotations

import mimetypes
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from .base64_fetcher import Base64Fetcher
from .cache import TieredCache
from .filesystem_fetcher import FilesystemFetcher
from .http_fetcher import HttpFetcher

# Optional genro-storage import
try:
    from genro_storage import AsyncStorageManager
    GENRO_STORAGE_AVAILABLE = True
except ImportError:
    AsyncStorageManager = None  # type: ignore[misc, assignment]
    GENRO_STORAGE_AVAILABLE = False

if TYPE_CHECKING:
    from genro_storage import AsyncStorageManager as AsyncStorageManagerType

# Regex pattern for MD5 marker in filename: {MD5:hexstring}
MD5_MARKER_PATTERN = re.compile(r'\{MD5:([a-fA-F0-9]+)\}')


class AttachmentManager:
    """High-level interface for fetching email attachments from multiple sources.

    Routes attachment requests to the appropriate fetcher based on storage_path
    format. Supports optional MD5-based caching for deduplication and performance.

    Supported storage_path formats:
    - volume:path - genro-storage (requires genro-storage installed)
    - base64:content - Inline base64 decode
    - /absolute/path - Filesystem absolute path
    - relative/path - Filesystem relative to base_dir
    - @params - HTTP POST to default endpoint
    - @[url]params - HTTP POST to explicit URL

    Attributes:
        _storage_fetcher: Fetcher for genro-storage volumes (optional).
        _base64_fetcher: Fetcher for base64-encoded inline content.
        _filesystem_fetcher: Fetcher for local filesystem paths.
        _http_fetcher: Fetcher for HTTP endpoints.
        _cache: Optional TieredCache for MD5-based caching.
    """

    def __init__(
        self,
        storage_manager: Optional["AsyncStorageManagerType"] = None,
        base_dir: Optional[str] = None,
        http_endpoint: Optional[str] = None,
        http_auth_config: Optional[Dict[str, str]] = None,
        cache: Optional[TieredCache] = None,
    ):
        """Initialize the attachment manager with configured fetchers.

        Args:
            storage_manager: Optional AsyncStorageManager instance for
                volume:path storage paths. If None and genro-storage is
                not installed, volume paths will raise an error.
            base_dir: Base directory for relative filesystem paths.
                Required if using relative paths without leading slash.
            http_endpoint: Default HTTP endpoint for @params paths.
            http_auth_config: HTTP authentication config with keys:
                method ("none", "bearer", "basic"), token, user, password.
            cache: Optional TieredCache for MD5-based content caching.
        """
        # Initialize fetchers
        self._storage_fetcher = None
        if storage_manager is not None and GENRO_STORAGE_AVAILABLE:
            from .storage_fetcher import StorageFetcher
            self._storage_fetcher = StorageFetcher(storage_manager)

        self._base64_fetcher = Base64Fetcher()
        self._filesystem_fetcher = FilesystemFetcher(base_dir=base_dir)
        self._http_fetcher = HttpFetcher(
            default_endpoint=http_endpoint,
            auth_config=http_auth_config,
        )
        self._cache = cache

    @staticmethod
    def parse_filename(filename: str) -> Tuple[str, Optional[str]]:
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

    def _parse_storage_path(self, path: str) -> Tuple[str, str]:
        """Determine the type and parsed content of a storage path.

        Args:
            path: The storage_path value from attachment dict.

        Returns:
            Tuple of (path_type, parsed_path) where path_type is one of:
            "storage", "base64", "filesystem", "http".

        Raises:
            ValueError: If path format is invalid or unsupported.
        """
        if not path:
            raise ValueError("Empty storage_path")

        # HTTP: starts with @
        if path.startswith("@"):
            return ("http", path[1:])

        # Base64: starts with "base64:"
        if path.startswith("base64:"):
            return ("base64", path[7:])

        # Absolute filesystem path: starts with /
        if path.startswith("/"):
            return ("filesystem", path)

        # Check for volume:path format (genro-storage)
        if ":" in path:
            # Could be volume:path or windows path like C:\
            # Volume names don't contain slashes before the colon
            colon_pos = path.index(":")
            potential_volume = path[:colon_pos]
            if "/" not in potential_volume and "\\" not in potential_volume:
                # Looks like volume:path
                if not self._storage_fetcher:
                    raise RuntimeError(
                        f"genro-storage required for volume path: {path}"
                    )
                return ("storage", path)

        # Relative filesystem path
        return ("filesystem", path)

    async def fetch(self, att: Dict[str, Any]) -> Optional[Tuple[bytes, str]]:
        """Retrieve attachment content with caching and filename cleanup.

        Parses the filename for MD5 marker, checks cache if available,
        fetches from appropriate backend, and caches the result.

        Args:
            att: Attachment specification dictionary containing:
                - filename: Original filename (may contain MD5 marker)
                - storage_path: Location identifier for content

        Returns:
            Tuple of (content_bytes, clean_filename), or None if
            storage_path is not specified.

        Raises:
            ValueError: If storage_path format is invalid.
            RuntimeError: If required backend is not available.
            FileNotFoundError: If file doesn't exist (filesystem).
            aiohttp.ClientError: If HTTP request fails.
        """
        storage_path = att.get("storage_path")
        if not storage_path:
            return None

        raw_filename = att.get("filename", "file.bin")
        clean_filename, md5_from_marker = self.parse_filename(raw_filename)

        # Try cache lookup if MD5 marker was provided
        if md5_from_marker and self._cache:
            cached = await self._cache.get(md5_from_marker)
            if cached is not None:
                return cached, clean_filename

        # Fetch from backend
        content = await self._fetch_from_backend(storage_path)
        if content is None:
            return None

        # Cache the result
        if self._cache:
            actual_md5 = TieredCache.compute_md5(content)
            await self._cache.set(actual_md5, content)

        return content, clean_filename

    async def _fetch_from_backend(self, storage_path: str) -> Optional[bytes]:
        """Fetch content from the appropriate backend.

        Args:
            storage_path: The storage path to fetch.

        Returns:
            Binary content, or None if not found.
        """
        path_type, parsed_path = self._parse_storage_path(storage_path)

        if path_type == "storage":
            if not self._storage_fetcher:
                raise RuntimeError(
                    f"genro-storage not available for path: {storage_path}"
                )
            return await self._storage_fetcher.fetch({"storage_path": storage_path})

        if path_type == "base64":
            return await self._base64_fetcher.fetch(parsed_path)

        if path_type == "filesystem":
            return await self._filesystem_fetcher.fetch(parsed_path)

        if path_type == "http":
            return await self._http_fetcher.fetch(parsed_path)

        raise ValueError(f"Unknown path type: {path_type}")

    async def fetch_batch(
        self,
        attachments: List[Dict[str, Any]],
    ) -> Dict[str, Tuple[bytes, str]]:
        """Fetch multiple attachments with batching optimization.

        Groups HTTP requests by server for batching. Other types are
        fetched individually but in parallel.

        Args:
            attachments: List of attachment dicts with storage_path and filename.

        Returns:
            Dictionary mapping storage_path to (content, clean_filename).
        """
        results: Dict[str, Tuple[bytes, str]] = {}
        to_fetch: Dict[str, List[Dict[str, Any]]] = {
            "storage": [],
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

            # Try cache
            if md5_from_marker and self._cache:
                cached = await self._cache.get(md5_from_marker)
                if cached is not None:
                    results[storage_path] = (cached, clean_filename)
                    continue

            # Categorize for fetching
            try:
                path_type, _ = self._parse_storage_path(storage_path)
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
        for path_type in ["storage", "base64", "filesystem"]:
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
    def guess_mime(filename: str) -> Tuple[str, str]:
        """Determine the MIME type for a filename based on its extension.

        Uses Python's mimetypes module to detect the appropriate MIME type
        for email attachment encoding. Falls back to application/octet-stream
        for unrecognized extensions.

        This method works independently of any backend availability.

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


def is_storage_available() -> bool:
    """Check if genro-storage is installed and available.

    Returns:
        True if genro-storage can be imported, False otherwise.
    """
    return GENRO_STORAGE_AVAILABLE
