# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Two-tiered cache for attachment content.

This module provides a TieredCache class that stores attachment content
using MD5 hash as the key. It uses two levels:

- Level 1 (Memory): Fast LRU cache with short TTL for small files
- Level 2 (Disk): Persistent cache with longer TTL for larger files

The cache is content-addressable: the same content always maps to
the same key (MD5 hash), enabling deduplication across different
storage paths.

Example:
    Using the cache for attachment fetching::

        cache = TieredCache(
            memory_max_mb=50,
            memory_ttl_seconds=300,
            disk_dir="/var/cache/attachments",
            disk_max_mb=500,
            disk_ttl_seconds=3600,
            disk_threshold_kb=100,
        )

        # Try cache first
        content = await cache.get("a1b2c3d4e5f6...")
        if content is None:
            content = await fetch_from_storage()
            md5 = hashlib.md5(content).hexdigest()
            await cache.set(md5, content)
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from pathlib import Path


class MemoryCache:
    """LRU in-memory cache with TTL and size limits.

    Stores small files in memory for fast access. Uses an OrderedDict
    for LRU eviction and tracks total size to enforce memory limits.

    Attributes:
        _max_bytes: Maximum total bytes to store.
        _ttl_seconds: Time-to-live for entries in seconds.
        _cache: OrderedDict mapping MD5 hash to (content, timestamp).
        _current_bytes: Current total bytes stored.
    """

    def __init__(self, max_mb: float = 50, ttl_seconds: int = 300):
        """Initialize the memory cache.

        Args:
            max_mb: Maximum memory usage in megabytes.
            ttl_seconds: Time-to-live for entries in seconds.
        """
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self._current_bytes = 0

    def get(self, md5_hash: str) -> bytes | None:
        """Retrieve content by MD5 hash.

        Args:
            md5_hash: The MD5 hash key.

        Returns:
            The cached content, or None if not found or expired.
        """
        entry = self._cache.get(md5_hash)
        if entry is None:
            return None

        content, timestamp = entry
        if time.time() - timestamp > self._ttl_seconds:
            # Entry expired, remove it
            self._remove(md5_hash)
            return None

        # Move to end (most recently used)
        self._cache.move_to_end(md5_hash)
        return content

    def set(self, md5_hash: str, content: bytes) -> None:
        """Store content with MD5 hash as key.

        Args:
            md5_hash: The MD5 hash key.
            content: The binary content to cache.
        """
        content_size = len(content)

        # Don't cache if single item exceeds max size
        if content_size > self._max_bytes:
            return

        # Remove existing entry if present
        if md5_hash in self._cache:
            self._remove(md5_hash)

        # Evict oldest entries until we have space
        while self._current_bytes + content_size > self._max_bytes and self._cache:
            oldest_key = next(iter(self._cache))
            self._remove(oldest_key)

        # Add new entry
        self._cache[md5_hash] = (content, time.time())
        self._current_bytes += content_size

    def _remove(self, md5_hash: str) -> None:
        """Remove an entry from the cache."""
        if md5_hash in self._cache:
            content, _ = self._cache.pop(md5_hash)
            self._current_bytes -= len(content)

    def clear(self) -> None:
        """Clear all entries from the cache."""
        self._cache.clear()
        self._current_bytes = 0

    def cleanup_expired(self) -> int:
        """Remove all expired entries.

        Returns:
            Number of entries removed.
        """
        now = time.time()
        expired = [
            key for key, (_, timestamp) in self._cache.items()
            if now - timestamp > self._ttl_seconds
        ]
        for key in expired:
            self._remove(key)
        return len(expired)

    @property
    def size_bytes(self) -> int:
        """Current total size in bytes."""
        return self._current_bytes

    @property
    def entry_count(self) -> int:
        """Number of entries in cache."""
        return len(self._cache)


class DiskCache:
    """Persistent disk cache with TTL and size limits.

    Stores larger files on disk for persistence across restarts.
    Files are named by their MD5 hash with metadata stored in
    a companion .meta file.

    Attributes:
        _cache_dir: Directory for cached files.
        _max_bytes: Maximum total bytes to store.
        _ttl_seconds: Time-to-live for entries in seconds.
    """

    def __init__(
        self,
        cache_dir: str,
        max_mb: float = 500,
        ttl_seconds: int = 3600,
    ):
        """Initialize the disk cache.

        Args:
            cache_dir: Directory path for cached files.
            max_mb: Maximum disk usage in megabytes.
            ttl_seconds: Time-to-live for entries in seconds.
        """
        self._cache_dir = Path(cache_dir)
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        """Initialize the cache directory."""
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, md5_hash: str) -> Path:
        """Get the file path for an MD5 hash."""
        # Use first 2 chars as subdirectory to avoid too many files in one dir
        subdir = md5_hash[:2]
        return self._cache_dir / subdir / md5_hash

    async def get(self, md5_hash: str) -> bytes | None:
        """Retrieve content by MD5 hash.

        Args:
            md5_hash: The MD5 hash key.

        Returns:
            The cached content, or None if not found or expired.
        """
        file_path = self._file_path(md5_hash)
        if not file_path.exists():
            return None

        # Check TTL based on file mtime
        try:
            mtime = file_path.stat().st_mtime
            if time.time() - mtime > self._ttl_seconds:
                # Expired, remove it
                await self._remove(md5_hash)
                return None

            # Read content
            return await asyncio.to_thread(file_path.read_bytes)
        except OSError:
            return None

    async def set(self, md5_hash: str, content: bytes) -> None:
        """Store content with MD5 hash as key.

        Args:
            md5_hash: The MD5 hash key.
            content: The binary content to cache.
        """
        content_size = len(content)

        # Don't cache if single item exceeds max size
        if content_size > self._max_bytes:
            return

        async with self._lock:
            # Ensure we have space
            await self._ensure_space(content_size)

            # Write file
            file_path = self._file_path(md5_hash)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(file_path.write_bytes, content)

    async def _remove(self, md5_hash: str) -> None:
        """Remove a cached file."""
        file_path = self._file_path(md5_hash)
        try:
            if file_path.exists():
                await asyncio.to_thread(file_path.unlink)
            # Try to remove empty parent directory
            if file_path.parent.exists() and not any(file_path.parent.iterdir()):
                await asyncio.to_thread(file_path.parent.rmdir)
        except OSError:
            pass

    async def _ensure_space(self, needed_bytes: int) -> None:
        """Ensure enough space by removing oldest files if necessary."""
        current_size = await self._get_total_size()

        if current_size + needed_bytes <= self._max_bytes:
            return

        # Get all cache files sorted by mtime (oldest first)
        files = await self._get_cache_files_by_age()

        for file_path, file_size in files:
            if current_size + needed_bytes <= self._max_bytes:
                break
            try:
                await asyncio.to_thread(file_path.unlink)
                current_size -= file_size
            except OSError:
                pass

    async def _get_total_size(self) -> int:
        """Get total size of all cached files."""
        total = 0
        if not self._cache_dir.exists():
            return 0
        for subdir in self._cache_dir.iterdir():
            if subdir.is_dir():
                for file_path in subdir.iterdir():
                    if file_path.is_file():
                        total += file_path.stat().st_size
        return total

    async def _get_cache_files_by_age(self) -> list[tuple[Path, int]]:
        """Get all cache files sorted by modification time (oldest first)."""
        files = []
        if not self._cache_dir.exists():
            return files
        for subdir in self._cache_dir.iterdir():
            if subdir.is_dir():
                for file_path in subdir.iterdir():
                    if file_path.is_file():
                        stat = file_path.stat()
                        files.append((file_path, stat.st_size, stat.st_mtime))
        # Sort by mtime (oldest first)
        files.sort(key=lambda x: x[2])
        return [(f[0], f[1]) for f in files]

    async def cleanup_expired(self) -> int:
        """Remove all expired entries.

        Returns:
            Number of entries removed.
        """
        now = time.time()
        removed = 0
        if not self._cache_dir.exists():
            return 0

        for subdir in self._cache_dir.iterdir():
            if not subdir.is_dir():
                continue
            for file_path in subdir.iterdir():
                if not file_path.is_file():
                    continue
                try:
                    mtime = file_path.stat().st_mtime
                    if now - mtime > self._ttl_seconds:
                        await asyncio.to_thread(file_path.unlink)
                        removed += 1
                except OSError:
                    pass
            # Remove empty subdirectory
            try:
                if not any(subdir.iterdir()):
                    await asyncio.to_thread(subdir.rmdir)
            except OSError:
                pass

        return removed

    async def clear(self) -> None:
        """Clear all entries from the cache."""
        if not self._cache_dir.exists():
            return
        for subdir in self._cache_dir.iterdir():
            if subdir.is_dir():
                for file_path in subdir.iterdir():
                    try:
                        await asyncio.to_thread(file_path.unlink)
                    except OSError:
                        pass
                try:
                    await asyncio.to_thread(subdir.rmdir)
                except OSError:
                    pass


class TieredCache:
    """Two-tiered cache combining memory and disk storage.

    Uses MD5 hash as the key for content-addressable storage.
    Small files go to memory (L1), larger files go to disk (L2).

    Attributes:
        _memory: Level 1 memory cache.
        _disk: Level 2 disk cache.
        _threshold_bytes: Size threshold for disk storage.
    """

    def __init__(
        self,
        memory_max_mb: float = 50,
        memory_ttl_seconds: int = 300,
        disk_dir: str | None = None,
        disk_max_mb: float = 500,
        disk_ttl_seconds: int = 3600,
        disk_threshold_kb: float = 100,
    ):
        """Initialize the tiered cache.

        Args:
            memory_max_mb: Maximum memory usage in megabytes.
            memory_ttl_seconds: TTL for memory entries in seconds.
            disk_dir: Directory for disk cache. If None, disk cache disabled.
            disk_max_mb: Maximum disk usage in megabytes.
            disk_ttl_seconds: TTL for disk entries in seconds.
            disk_threshold_kb: Files larger than this go to disk.
        """
        self._memory = MemoryCache(max_mb=memory_max_mb, ttl_seconds=memory_ttl_seconds)
        self._disk: DiskCache | None = None
        if disk_dir:
            self._disk = DiskCache(
                cache_dir=disk_dir,
                max_mb=disk_max_mb,
                ttl_seconds=disk_ttl_seconds,
            )
        self._threshold_bytes = int(disk_threshold_kb * 1024)

    async def init(self) -> None:
        """Initialize the cache (creates disk directory if needed)."""
        if self._disk:
            await self._disk.init()

    async def get(self, md5_hash: str) -> bytes | None:
        """Retrieve content by MD5 hash.

        Checks memory first, then disk. If found on disk and small enough,
        promotes to memory for faster subsequent access.

        Args:
            md5_hash: The MD5 hash key.

        Returns:
            The cached content, or None if not found.
        """
        # Try memory first
        content = self._memory.get(md5_hash)
        if content is not None:
            return content

        # Try disk
        if self._disk:
            content = await self._disk.get(md5_hash)
            if content is not None:
                # Promote to memory if small enough
                if len(content) < self._threshold_bytes:
                    self._memory.set(md5_hash, content)
                return content

        return None

    async def set(self, md5_hash: str, content: bytes) -> None:
        """Store content with MD5 hash as key.

        Small files go to memory, larger files go to disk.

        Args:
            md5_hash: The MD5 hash key.
            content: The binary content to cache.
        """
        if len(content) < self._threshold_bytes:
            self._memory.set(md5_hash, content)
        elif self._disk:
            await self._disk.set(md5_hash, content)

    async def cleanup_expired(self) -> tuple[int, int]:
        """Remove expired entries from both tiers.

        Returns:
            Tuple of (memory_removed, disk_removed).
        """
        memory_removed = self._memory.cleanup_expired()
        disk_removed = 0
        if self._disk:
            disk_removed = await self._disk.cleanup_expired()
        return memory_removed, disk_removed

    async def clear(self) -> None:
        """Clear all entries from both tiers."""
        self._memory.clear()
        if self._disk:
            await self._disk.clear()

    @staticmethod
    def compute_md5(content: bytes) -> str:
        """Compute MD5 hash of content.

        Args:
            content: Binary content to hash.

        Returns:
            Lowercase hexadecimal MD5 hash string.
        """
        return hashlib.md5(content).hexdigest()
