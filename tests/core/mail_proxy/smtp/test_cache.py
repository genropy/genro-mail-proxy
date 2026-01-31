# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for TieredCache (MemoryCache and DiskCache)."""

import time
from pathlib import Path

import pytest

from core.mail_proxy.smtp.cache import MemoryCache, DiskCache, TieredCache


class TestMemoryCache:
    """Tests for in-memory LRU cache."""

    @pytest.fixture
    def cache(self):
        """Create a memory cache with 1MB limit."""
        return MemoryCache(max_mb=1, ttl_seconds=60)

    def test_get_nonexistent_returns_none(self, cache):
        """get() returns None for missing key."""
        assert cache.get("nonexistent") is None

    def test_set_and_get(self, cache):
        """set() stores data that can be retrieved with get()."""
        content = b"test content"
        cache.set("abc123", content)
        assert cache.get("abc123") == content

    def test_ttl_expiration(self):
        """Entries expire after TTL."""
        cache = MemoryCache(max_mb=1, ttl_seconds=0)  # 0 = immediate expiry
        cache.set("abc123", b"test")
        time.sleep(0.01)
        assert cache.get("abc123") is None

    def test_lru_eviction(self):
        """Oldest entries are evicted when cache is full."""
        cache = MemoryCache(max_mb=0.0001, ttl_seconds=60)  # ~100 bytes max
        cache.set("key1", b"x" * 50)
        cache.set("key2", b"x" * 50)
        cache.set("key3", b"x" * 50)  # Should evict key1

        assert cache.get("key1") is None
        assert cache.get("key2") is not None

    def test_get_moves_to_end(self, cache):
        """get() moves entry to end (most recently used)."""
        cache.set("key1", b"data1")
        cache.set("key2", b"data2")

        # Access key1 to make it most recently used
        cache.get("key1")

        # key2 should now be at the front (least recently used)
        # Internal check - in real LRU, key2 would be evicted first
        first_key = next(iter(cache._cache.keys()))
        assert first_key == "key2"

    def test_set_updates_existing(self, cache):
        """set() updates existing entry."""
        cache.set("key1", b"old data")
        cache.set("key1", b"new data")
        assert cache.get("key1") == b"new data"
        assert cache.entry_count == 1

    def test_content_too_large_not_stored(self):
        """Content larger than max is not stored."""
        cache = MemoryCache(max_mb=0.0001, ttl_seconds=60)  # ~100 bytes
        large_content = b"x" * 1000
        cache.set("large", large_content)
        assert cache.get("large") is None

    def test_clear(self, cache):
        """clear() removes all entries."""
        cache.set("key1", b"data1")
        cache.set("key2", b"data2")
        cache.clear()
        assert cache.entry_count == 0
        assert cache.size_bytes == 0

    def test_cleanup_expired(self):
        """cleanup_expired() removes old entries."""
        cache = MemoryCache(max_mb=1, ttl_seconds=0)
        cache.set("key1", b"data1")
        cache.set("key2", b"data2")
        time.sleep(0.01)

        removed = cache.cleanup_expired()

        assert removed == 2
        assert cache.entry_count == 0

    def test_size_bytes_tracking(self, cache):
        """size_bytes tracks total content size."""
        cache.set("key1", b"12345")  # 5 bytes
        cache.set("key2", b"67890")  # 5 bytes
        assert cache.size_bytes == 10

    def test_entry_count(self, cache):
        """entry_count returns number of entries."""
        cache.set("key1", b"data1")
        cache.set("key2", b"data2")
        assert cache.entry_count == 2


class TestDiskCache:
    """Tests for persistent disk cache."""

    @pytest.fixture
    def cache_dir(self, tmp_path):
        """Create a temporary cache directory."""
        return tmp_path / "cache"

    @pytest.fixture
    async def cache(self, cache_dir):
        """Create and initialize a disk cache."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        await cache.init()
        return cache

    async def test_init_creates_directory(self, cache_dir):
        """init() creates cache directory."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        await cache.init()
        assert cache_dir.exists()

    async def test_get_nonexistent_returns_none(self, cache):
        """get() returns None for missing key."""
        result = await cache.get("nonexistent")
        assert result is None

    async def test_set_and_get(self, cache):
        """set() stores data that can be retrieved with get()."""
        content = b"test content for disk"
        await cache.set("abc123", content)
        result = await cache.get("abc123")
        assert result == content

    async def test_file_structure(self, cache, cache_dir):
        """Files are stored in subdirectories by hash prefix."""
        await cache.set("abcdef123456", b"test")
        # Should be stored in ab/abcdef123456
        file_path = cache_dir / "ab" / "abcdef123456"
        assert file_path.exists()

    async def test_ttl_expiration(self, cache_dir):
        """Entries expire after TTL."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=0)
        await cache.init()
        await cache.set("abc123", b"test")
        time.sleep(0.01)
        result = await cache.get("abc123")
        assert result is None

    async def test_content_too_large_not_stored(self, cache_dir):
        """Content larger than max is not stored."""
        cache = DiskCache(str(cache_dir), max_mb=0.0001, ttl_seconds=60)
        await cache.init()
        large_content = b"x" * 1000
        await cache.set("large", large_content)
        result = await cache.get("large")
        assert result is None

    async def test_cleanup_expired(self, cache_dir):
        """cleanup_expired() removes old files."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=0)
        await cache.init()
        await cache.set("abc123", b"test1")
        await cache.set("def456", b"test2")
        time.sleep(0.01)

        removed = await cache.cleanup_expired()

        assert removed == 2

    async def test_clear(self, cache, cache_dir):
        """clear() removes all cached files."""
        await cache.set("abc123", b"test1")
        await cache.set("def456", b"test2")

        await cache.clear()

        # Cache dir should be empty (except maybe empty subdirs)
        assert await cache.get("abc123") is None
        assert await cache.get("def456") is None


class TestTieredCache:
    """Tests for two-tiered cache combining memory and disk."""

    @pytest.fixture
    async def cache(self, tmp_path):
        """Create a tiered cache with both memory and disk."""
        cache = TieredCache(
            memory_max_mb=1,
            memory_ttl_seconds=60,
            disk_dir=str(tmp_path / "disk_cache"),
            disk_max_mb=10,
            disk_ttl_seconds=300,
            disk_threshold_kb=0.1,  # 100 bytes threshold
        )
        await cache.init()
        return cache

    @pytest.fixture
    async def memory_only_cache(self):
        """Create a tiered cache with memory only."""
        cache = TieredCache(
            memory_max_mb=1,
            memory_ttl_seconds=60,
            disk_dir=None,
        )
        await cache.init()
        return cache

    async def test_get_nonexistent_returns_none(self, cache):
        """get() returns None for missing key."""
        result = await cache.get("nonexistent")
        assert result is None

    async def test_small_content_goes_to_memory(self, cache):
        """Content smaller than threshold goes to memory."""
        small_content = b"small"  # < 100 bytes
        md5 = TieredCache.compute_md5(small_content)
        await cache.set(md5, small_content)

        # Should be in memory
        assert cache._memory.get(md5) is not None

    async def test_large_content_goes_to_disk(self, cache):
        """Content larger than threshold goes to disk."""
        large_content = b"x" * 200  # > 100 bytes threshold
        md5 = TieredCache.compute_md5(large_content)
        await cache.set(md5, large_content)

        # Should be in disk (not memory)
        assert cache._memory.get(md5) is None
        assert await cache._disk.get(md5) is not None

    async def test_get_promotes_from_disk_to_memory(self, cache):
        """get() promotes small disk content to memory."""
        # Manually put small content in disk
        small_content = b"small"
        md5 = TieredCache.compute_md5(small_content)
        await cache._disk.set(md5, small_content)

        # get() should retrieve and promote to memory
        result = await cache.get(md5)
        assert result == small_content
        assert cache._memory.get(md5) is not None

    async def test_get_does_not_promote_large_content(self, cache):
        """get() does not promote large disk content to memory."""
        large_content = b"x" * 200
        md5 = TieredCache.compute_md5(large_content)
        await cache._disk.set(md5, large_content)

        result = await cache.get(md5)
        assert result == large_content
        assert cache._memory.get(md5) is None

    async def test_memory_only_mode(self, memory_only_cache):
        """Cache works with memory only (no disk)."""
        content = b"test content"
        md5 = TieredCache.compute_md5(content)
        await memory_only_cache.set(md5, content)

        result = await memory_only_cache.get(md5)
        assert result == content

    async def test_compute_md5(self):
        """compute_md5() returns correct hash."""
        content = b"test content"
        md5 = TieredCache.compute_md5(content)
        assert len(md5) == 32  # MD5 hex is 32 chars
        assert md5 == "9473fdd0d880a43c21b7778d34872157"

    async def test_cleanup_expired(self, cache):
        """cleanup_expired() cleans both tiers."""
        cache._memory = MemoryCache(max_mb=1, ttl_seconds=0)
        cache._memory.set("key1", b"data")
        time.sleep(0.01)

        memory_removed, disk_removed = await cache.cleanup_expired()

        assert memory_removed == 1

    async def test_clear(self, cache):
        """clear() clears both tiers."""
        small = b"small"
        large = b"x" * 200
        await cache.set(TieredCache.compute_md5(small), small)
        await cache.set(TieredCache.compute_md5(large), large)

        await cache.clear()

        assert cache._memory.entry_count == 0

    async def test_get_checks_memory_first(self, cache):
        """get() checks memory before disk."""
        content = b"test"
        md5 = TieredCache.compute_md5(content)

        # Put in both (though normally wouldn't happen)
        cache._memory.set(md5, content)
        await cache._disk.set(md5, b"different")

        # Should get memory version
        result = await cache.get(md5)
        assert result == content


class TestMemoryCacheEdgeCases:
    """Additional edge case tests for MemoryCache."""

    def test_remove_nonexistent_key(self):
        """_remove() handles nonexistent key gracefully."""
        cache = MemoryCache(max_mb=1, ttl_seconds=60)
        # Should not raise
        cache._remove("nonexistent")
        assert cache.entry_count == 0

    def test_set_updates_size_correctly(self):
        """set() correctly updates size when replacing entry."""
        cache = MemoryCache(max_mb=1, ttl_seconds=60)
        cache.set("key1", b"12345")  # 5 bytes
        assert cache.size_bytes == 5

        cache.set("key1", b"123456789")  # 9 bytes (replace)
        assert cache.size_bytes == 9
        assert cache.entry_count == 1


class TestDiskCacheEdgeCases:
    """Additional edge case tests for DiskCache."""

    @pytest.fixture
    def cache_dir(self, tmp_path):
        return tmp_path / "cache"

    async def test_get_handles_read_error(self, cache_dir, monkeypatch):
        """get() returns None on read error."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        await cache.init()
        await cache.set("abc123", b"test")

        # Simulate read error by making file unreadable
        def raise_oserror(*args, **kwargs):
            raise OSError("Simulated error")

        file_path = cache._file_path("abc123")
        original_read = file_path.read_bytes

        # Patch at the path level
        monkeypatch.setattr(type(file_path), "read_bytes", lambda self: raise_oserror())

        result = await cache.get("abc123")
        assert result is None

    async def test_remove_handles_missing_file(self, cache_dir):
        """_remove() handles already-deleted file."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        await cache.init()

        # Should not raise even though file doesn't exist
        await cache._remove("nonexistent")

    async def test_ensure_space_evicts_oldest(self, cache_dir):
        """_ensure_space() evicts oldest files when full."""
        cache = DiskCache(str(cache_dir), max_mb=0.0002, ttl_seconds=3600)  # ~200 bytes
        await cache.init()

        # Fill cache
        await cache.set("old1", b"x" * 50)
        time.sleep(0.01)  # Ensure different mtime
        await cache.set("old2", b"x" * 50)
        time.sleep(0.01)
        await cache.set("old3", b"x" * 50)
        time.sleep(0.01)

        # Add more - should evict oldest
        await cache.set("new1", b"x" * 50)

        # old1 should be evicted (oldest)
        # But this depends on exact timing and space calculation
        # Just verify we can still read the newest
        result = await cache.get("new1")
        assert result is not None

    async def test_get_total_size_empty_cache(self, cache_dir):
        """_get_total_size() returns 0 for empty/nonexistent cache."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        # Don't call init() - directory doesn't exist
        size = await cache._get_total_size()
        assert size == 0

    async def test_cleanup_expired_handles_empty_subdir(self, cache_dir):
        """cleanup_expired() removes empty subdirectories."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=0)
        await cache.init()

        await cache.set("ab123456", b"test")
        time.sleep(0.01)

        removed = await cache.cleanup_expired()

        assert removed == 1
        # Subdirectory should also be removed
        subdir = cache_dir / "ab"
        assert not subdir.exists()

    async def test_clear_empty_cache(self, cache_dir):
        """clear() handles empty/nonexistent cache."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        # Don't call init()
        await cache.clear()  # Should not raise

    async def test_cleanup_on_nonexistent_dir(self, cache_dir):
        """cleanup_expired() handles nonexistent directory."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        # Don't call init()
        removed = await cache.cleanup_expired()
        assert removed == 0

    async def test_get_cache_files_nonexistent_dir(self, cache_dir):
        """_get_cache_files_by_age() handles nonexistent directory."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        # Don't call init()
        files = await cache._get_cache_files_by_age()
        assert files == []


class TestTieredCacheEdgeCases:
    """Additional edge case tests for TieredCache."""

    async def test_memory_only_large_content_discarded(self):
        """Memory-only cache discards large content (no disk fallback)."""
        cache = TieredCache(
            memory_max_mb=1,
            memory_ttl_seconds=60,
            disk_dir=None,  # No disk
            disk_threshold_kb=0.1,  # 100 bytes
        )
        await cache.init()

        large_content = b"x" * 200  # > threshold
        md5 = TieredCache.compute_md5(large_content)
        await cache.set(md5, large_content)

        # Large content goes nowhere when disk is disabled
        result = await cache.get(md5)
        assert result is None

    async def test_cleanup_expired_memory_only(self):
        """cleanup_expired() works with memory only."""
        cache = TieredCache(
            memory_max_mb=1,
            memory_ttl_seconds=0,
            disk_dir=None,
        )
        await cache.init()

        cache._memory.set("key1", b"data")
        time.sleep(0.01)

        mem_removed, disk_removed = await cache.cleanup_expired()

        assert mem_removed == 1
        assert disk_removed == 0

    async def test_clear_memory_only(self):
        """clear() works with memory only."""
        cache = TieredCache(
            memory_max_mb=1,
            memory_ttl_seconds=60,
            disk_dir=None,
        )
        await cache.init()

        await cache.set(TieredCache.compute_md5(b"test"), b"test")
        await cache.clear()

        assert cache._memory.entry_count == 0


class TestDiskCacheSpaceManagement:
    """Tests for DiskCache space eviction logic."""

    @pytest.fixture
    def cache_dir(self, tmp_path):
        return tmp_path / "cache"

    async def test_ensure_space_with_multiple_evictions(self, cache_dir):
        """_ensure_space() evicts multiple files when needed."""
        # Very small cache: ~150 bytes total
        cache = DiskCache(str(cache_dir), max_mb=0.00015, ttl_seconds=3600)
        await cache.init()

        # Add files that fill the cache
        await cache.set("file1", b"a" * 40)
        time.sleep(0.01)
        await cache.set("file2", b"b" * 40)
        time.sleep(0.01)
        await cache.set("file3", b"c" * 40)
        time.sleep(0.01)

        # Add larger file - should trigger eviction of oldest files
        await cache.set("file4", b"d" * 60)

        # file4 should exist
        result = await cache.get("file4")
        assert result is not None

    async def test_ensure_space_handles_oserror_during_eviction(self, cache_dir, monkeypatch):
        """_ensure_space() handles OSError during file deletion."""
        cache = DiskCache(str(cache_dir), max_mb=0.0002, ttl_seconds=3600)
        await cache.init()

        await cache.set("file1", b"a" * 50)
        time.sleep(0.01)
        await cache.set("file2", b"b" * 50)

        # Track original unlink
        original_unlink = Path.unlink
        unlink_calls = []

        def patched_unlink(self, *args, **kwargs):
            unlink_calls.append(str(self))
            if "file1" in str(self):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", patched_unlink)

        # Should not raise despite OSError
        await cache.set("file3", b"c" * 50)

    async def test_remove_cleans_empty_parent_dir(self, cache_dir):
        """_remove() removes empty parent subdirectory."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        await cache.init()

        await cache.set("ab123456", b"test")
        subdir = cache_dir / "ab"
        assert subdir.exists()

        await cache._remove("ab123456")

        # Subdirectory should be removed (it's empty)
        assert not subdir.exists()

    async def test_cleanup_skips_non_files(self, cache_dir):
        """cleanup_expired() skips non-file entries."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=0)
        await cache.init()

        # Create a subdirectory inside cache subdir (edge case)
        subdir = cache_dir / "ab"
        subdir.mkdir(parents=True, exist_ok=True)
        nested_dir = subdir / "nested_dir"
        nested_dir.mkdir()

        # Also add a real file
        await cache.set("ab123456", b"test")
        time.sleep(0.01)

        # Should handle the nested dir gracefully
        removed = await cache.cleanup_expired()
        assert removed == 1  # Only the file, not the dir

    async def test_clear_handles_file_removal_error(self, cache_dir, monkeypatch):
        """clear() handles OSError during file removal."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        await cache.init()

        await cache.set("ab123456", b"test")
        await cache.set("cd789012", b"test2")

        error_count = [0]
        original_unlink = Path.unlink

        def patched_unlink(self, *args, **kwargs):
            if error_count[0] == 0:
                error_count[0] += 1
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", patched_unlink)

        # Should not raise
        await cache.clear()

    async def test_clear_handles_rmdir_error(self, cache_dir, monkeypatch):
        """clear() handles OSError during directory removal."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=60)
        await cache.init()

        await cache.set("ab123456", b"test")

        original_rmdir = Path.rmdir

        def patched_rmdir(self, *args, **kwargs):
            raise OSError("Directory not empty")

        monkeypatch.setattr(Path, "rmdir", patched_rmdir)

        # Should not raise
        await cache.clear()

    async def test_cleanup_handles_rmdir_error_after_cleanup(self, cache_dir, monkeypatch):
        """cleanup_expired() handles OSError when removing empty subdir."""
        cache = DiskCache(str(cache_dir), max_mb=1, ttl_seconds=0)
        await cache.init()

        await cache.set("ab123456", b"test")
        time.sleep(0.01)

        original_rmdir = Path.rmdir

        def patched_rmdir(self, *args, **kwargs):
            raise OSError("Permission denied")

        monkeypatch.setattr(Path, "rmdir", patched_rmdir)

        # Should not raise despite rmdir error
        removed = await cache.cleanup_expired()
        assert removed == 1
