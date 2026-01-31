# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Two-tiered cache for attachment content.

Provides content-addressable caching using MD5 hash as key with
automatic tier selection based on content size.

Components:
    MemoryCache: Fast LRU cache with short TTL for small files.
    DiskCache: Persistent cache with longer TTL for larger files.
    TieredCache: Combined memory + disk cache with automatic routing.

Example:
    Create and use a tiered cache::

        cache = TieredCache(
            memory_max_mb=50,
            disk_dir="/var/cache/attachments",
            disk_threshold_kb=100,
        )
        await cache.init()

        content = await cache.get("a1b2c3d4...")
        if content is None:
            content = await fetch_from_storage()
            await cache.set(TieredCache.compute_md5(content), content)

Note:
    Files smaller than disk_threshold_kb are cached in memory only.
    Larger files go to disk cache. Memory cache is checked first
    on reads for optimal performance.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import OrderedDict
from pathlib import Path


class MemoryCache:
    """LRU in-memory cache with TTL and size limits.

    Attributes:
        _max_bytes: Maximum cache size in bytes.
        _ttl_seconds: Time-to-live for entries.
    """

    def __init__(self, max_mb: float = 50, ttl_seconds: int = 300):
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self._current_bytes = 0

    def get(self, md5_hash: str) -> bytes | None:
        entry = self._cache.get(md5_hash)
        if entry is None:
            return None

        content, timestamp = entry
        if time.time() - timestamp > self._ttl_seconds:
            self._remove(md5_hash)
            return None

        self._cache.move_to_end(md5_hash)
        return content

    def set(self, md5_hash: str, content: bytes) -> None:
        content_size = len(content)

        if content_size > self._max_bytes:
            return

        if md5_hash in self._cache:
            self._remove(md5_hash)

        while self._current_bytes + content_size > self._max_bytes and self._cache:
            oldest_key = next(iter(self._cache))
            self._remove(oldest_key)

        self._cache[md5_hash] = (content, time.time())
        self._current_bytes += content_size

    def _remove(self, md5_hash: str) -> None:
        if md5_hash in self._cache:
            content, _ = self._cache.pop(md5_hash)
            self._current_bytes -= len(content)

    def clear(self) -> None:
        self._cache.clear()
        self._current_bytes = 0

    def cleanup_expired(self) -> int:
        now = time.time()
        expired = [
            key
            for key, (_, timestamp) in self._cache.items()
            if now - timestamp > self._ttl_seconds
        ]
        for key in expired:
            self._remove(key)
        return len(expired)

    @property
    def size_bytes(self) -> int:
        return self._current_bytes

    @property
    def entry_count(self) -> int:
        return len(self._cache)


class DiskCache:
    """Persistent disk cache with TTL and size limits.

    Uses subdirectory structure based on MD5 prefix for efficient
    filesystem operations with large numbers of files.

    Attributes:
        _cache_dir: Root directory for cached files.
        _max_bytes: Maximum total cache size in bytes.
        _ttl_seconds: Time-to-live for entries.
    """

    def __init__(
        self,
        cache_dir: str,
        max_mb: float = 500,
        ttl_seconds: int = 3600,
    ):
        self._cache_dir = Path(cache_dir)
        self._max_bytes = int(max_mb * 1024 * 1024)
        self._ttl_seconds = ttl_seconds
        self._lock = asyncio.Lock()

    async def init(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _file_path(self, md5_hash: str) -> Path:
        subdir = md5_hash[:2]
        return self._cache_dir / subdir / md5_hash

    async def get(self, md5_hash: str) -> bytes | None:
        file_path = self._file_path(md5_hash)
        if not file_path.exists():
            return None

        try:
            mtime = file_path.stat().st_mtime
            if time.time() - mtime > self._ttl_seconds:
                await self._remove(md5_hash)
                return None

            return await asyncio.to_thread(file_path.read_bytes)
        except OSError:
            return None

    async def set(self, md5_hash: str, content: bytes) -> None:
        content_size = len(content)

        if content_size > self._max_bytes:
            return

        async with self._lock:
            await self._ensure_space(content_size)

            file_path = self._file_path(md5_hash)
            file_path.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(file_path.write_bytes, content)

    async def _remove(self, md5_hash: str) -> None:
        file_path = self._file_path(md5_hash)
        try:
            if file_path.exists():
                await asyncio.to_thread(file_path.unlink)
            if file_path.parent.exists() and not any(file_path.parent.iterdir()):
                await asyncio.to_thread(file_path.parent.rmdir)
        except OSError:
            pass

    async def _ensure_space(self, needed_bytes: int) -> None:
        current_size = await self._get_total_size()

        if current_size + needed_bytes <= self._max_bytes:
            return

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
        files = []
        if not self._cache_dir.exists():
            return files
        for subdir in self._cache_dir.iterdir():
            if subdir.is_dir():
                for file_path in subdir.iterdir():
                    if file_path.is_file():
                        stat = file_path.stat()
                        files.append((file_path, stat.st_size, stat.st_mtime))
        files.sort(key=lambda x: x[2])
        return [(f[0], f[1]) for f in files]

    async def cleanup_expired(self) -> int:
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
            try:
                if not any(subdir.iterdir()):
                    await asyncio.to_thread(subdir.rmdir)
            except OSError:
                pass

        return removed

    async def clear(self) -> None:
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

    Routes content to appropriate tier based on size threshold.
    Small files go to memory for fast access, large files to disk
    for persistence. Reads check memory first, then disk.

    Attributes:
        _memory: MemoryCache instance for small files.
        _disk: Optional DiskCache for large files.
        _threshold_bytes: Size threshold for tier selection.
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
        if self._disk:
            await self._disk.init()

    async def get(self, md5_hash: str) -> bytes | None:
        content = self._memory.get(md5_hash)
        if content is not None:
            return content

        if self._disk:
            content = await self._disk.get(md5_hash)
            if content is not None:
                if len(content) < self._threshold_bytes:
                    self._memory.set(md5_hash, content)
                return content

        return None

    async def set(self, md5_hash: str, content: bytes) -> None:
        if len(content) < self._threshold_bytes:
            self._memory.set(md5_hash, content)
        elif self._disk:
            await self._disk.set(md5_hash, content)

    async def cleanup_expired(self) -> tuple[int, int]:
        memory_removed = self._memory.cleanup_expired()
        disk_removed = 0
        if self._disk:
            disk_removed = await self._disk.cleanup_expired()
        return memory_removed, disk_removed

    async def clear(self) -> None:
        self._memory.clear()
        if self._disk:
            await self._disk.clear()

    @staticmethod
    def compute_md5(content: bytes) -> str:
        return hashlib.md5(content).hexdigest()
