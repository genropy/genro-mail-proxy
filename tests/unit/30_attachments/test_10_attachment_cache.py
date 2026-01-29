"""Tests for the attachment cache module."""

import asyncio
import hashlib
import tempfile
from pathlib import Path

import pytest

from core.mail_proxy.attachments.cache import DiskCache, MemoryCache, TieredCache


class TestMemoryCache:
    """Tests for the MemoryCache class."""

    def test_set_and_get(self):
        """Test basic set and get operations."""
        cache = MemoryCache(max_mb=1, ttl_seconds=300)
        content = b"test content"
        md5 = hashlib.md5(content).hexdigest()

        cache.set(md5, content)
        result = cache.get(md5)

        assert result == content

    def test_get_missing_key(self):
        """Test get returns None for missing keys."""
        cache = MemoryCache()
        assert cache.get("nonexistent") is None

    def test_lru_eviction(self):
        """Test that LRU eviction works when cache is full."""
        # Create a small cache (100 bytes)
        cache = MemoryCache(max_mb=100 / (1024 * 1024), ttl_seconds=300)

        # Add items that will exceed the limit
        content1 = b"a" * 40
        content2 = b"b" * 40
        content3 = b"c" * 40

        md5_1 = hashlib.md5(content1).hexdigest()
        md5_2 = hashlib.md5(content2).hexdigest()
        md5_3 = hashlib.md5(content3).hexdigest()

        cache.set(md5_1, content1)
        cache.set(md5_2, content2)
        cache.set(md5_3, content3)  # Should evict md5_1

        assert cache.get(md5_1) is None  # Evicted
        assert cache.get(md5_2) == content2
        assert cache.get(md5_3) == content3

    def test_ttl_expiration(self):
        """Test that entries expire after TTL."""
        cache = MemoryCache(max_mb=1, ttl_seconds=0)  # Immediate expiration

        content = b"test content"
        md5 = hashlib.md5(content).hexdigest()
        cache.set(md5, content)

        # Entry should be expired immediately
        import time
        time.sleep(0.01)
        assert cache.get(md5) is None

    def test_update_existing_key(self):
        """Test updating an existing key."""
        cache = MemoryCache()
        md5 = "test_key"

        cache.set(md5, b"original")
        cache.set(md5, b"updated")

        assert cache.get(md5) == b"updated"

    def test_clear(self):
        """Test clearing the cache."""
        cache = MemoryCache()
        cache.set("key1", b"content1")
        cache.set("key2", b"content2")

        cache.clear()

        assert cache.get("key1") is None
        assert cache.get("key2") is None
        assert cache.entry_count == 0

    def test_cleanup_expired(self):
        """Test cleanup of expired entries."""
        cache = MemoryCache(ttl_seconds=0)
        cache.set("key1", b"content1")
        cache.set("key2", b"content2")

        import time
        time.sleep(0.01)

        removed = cache.cleanup_expired()
        assert removed == 2
        assert cache.entry_count == 0


class TestDiskCache:
    """Tests for the DiskCache class."""

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        """Test basic set and get operations."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=tmpdir, max_mb=10, ttl_seconds=300)
            await cache.init()

            content = b"test content"
            md5 = hashlib.md5(content).hexdigest()

            await cache.set(md5, content)
            result = await cache.get(md5)

            assert result == content

    @pytest.mark.asyncio
    async def test_get_missing_key(self):
        """Test get returns None for missing keys."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=tmpdir)
            await cache.init()

            assert await cache.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_persistence(self):
        """Test that data persists after cache object is recreated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            content = b"persistent content"
            md5 = hashlib.md5(content).hexdigest()

            # Write with first cache instance
            cache1 = DiskCache(cache_dir=tmpdir)
            await cache1.init()
            await cache1.set(md5, content)

            # Read with new cache instance
            cache2 = DiskCache(cache_dir=tmpdir)
            await cache2.init()
            result = await cache2.get(md5)

            assert result == content

    @pytest.mark.asyncio
    async def test_ttl_expiration(self):
        """Test that entries expire after TTL."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=tmpdir, ttl_seconds=0)
            await cache.init()

            content = b"test content"
            md5 = hashlib.md5(content).hexdigest()
            await cache.set(md5, content)

            # Wait for expiration
            await asyncio.sleep(0.01)
            result = await cache.get(md5)

            assert result is None

    @pytest.mark.asyncio
    async def test_cleanup_expired(self):
        """Test cleanup of expired entries."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=tmpdir, ttl_seconds=0)
            await cache.init()

            await cache.set("key1", b"content1")
            await cache.set("key2", b"content2")

            await asyncio.sleep(0.01)
            removed = await cache.cleanup_expired()

            assert removed == 2

    @pytest.mark.asyncio
    async def test_subdirectory_structure(self):
        """Test that files are stored in subdirectories based on hash prefix."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = DiskCache(cache_dir=tmpdir)
            await cache.init()

            content = b"test content"
            md5 = hashlib.md5(content).hexdigest()

            await cache.set(md5, content)

            # Check subdirectory exists (first 2 chars of hash)
            subdir = Path(tmpdir) / md5[:2]
            assert subdir.exists()
            assert (subdir / md5).exists()


class TestTieredCache:
    """Tests for the TieredCache class."""

    @pytest.mark.asyncio
    async def test_small_file_goes_to_memory(self):
        """Test that small files are stored in memory cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TieredCache(
                memory_max_mb=1,
                memory_ttl_seconds=300,
                disk_dir=tmpdir,
                disk_threshold_kb=1,  # 1KB threshold
            )
            await cache.init()

            # Small content (< 1KB)
            content = b"small content"
            md5 = TieredCache.compute_md5(content)

            await cache.set(md5, content)

            # Should be in memory
            assert cache._memory.get(md5) == content

    @pytest.mark.asyncio
    async def test_large_file_goes_to_disk(self):
        """Test that large files are stored on disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TieredCache(
                memory_max_mb=1,
                disk_dir=tmpdir,
                disk_threshold_kb=0.001,  # Very small threshold
            )
            await cache.init()

            # Large content (> threshold)
            content = b"a" * 100
            md5 = TieredCache.compute_md5(content)

            await cache.set(md5, content)

            # Should be on disk, not in memory
            assert cache._memory.get(md5) is None
            assert await cache._disk.get(md5) == content

    @pytest.mark.asyncio
    async def test_get_promotes_to_memory(self):
        """Test that disk cache hits promote small files to memory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TieredCache(
                memory_max_mb=1,
                disk_dir=tmpdir,
                disk_threshold_kb=10,  # 10KB threshold
            )
            await cache.init()

            # Content that's under the threshold
            content = b"small file"
            md5 = TieredCache.compute_md5(content)

            # Write directly to disk
            await cache._disk.set(md5, content)

            # Get should promote to memory
            result = await cache.get(md5)
            assert result == content
            assert cache._memory.get(md5) == content

    @pytest.mark.asyncio
    async def test_memory_first_then_disk(self):
        """Test that get checks memory before disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TieredCache(
                memory_max_mb=1,
                disk_dir=tmpdir,
            )
            await cache.init()

            content = b"test content"
            md5 = TieredCache.compute_md5(content)

            # Put in both caches with different content
            cache._memory.set(md5, b"memory version")
            await cache._disk.set(md5, b"disk version")

            # Should get memory version
            result = await cache.get(md5)
            assert result == b"memory version"

    @pytest.mark.asyncio
    async def test_no_disk_cache(self):
        """Test operation without disk cache."""
        cache = TieredCache(
            memory_max_mb=1,
            disk_dir=None,  # No disk cache
        )
        await cache.init()

        content = b"test content"
        md5 = TieredCache.compute_md5(content)

        await cache.set(md5, content)
        result = await cache.get(md5)

        assert result == content

    @pytest.mark.asyncio
    async def test_clear(self):
        """Test clearing both cache tiers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TieredCache(
                memory_max_mb=1,
                disk_dir=tmpdir,
            )
            await cache.init()

            # Add to both tiers
            small = b"small"
            large = b"large" * 1000
            md5_small = TieredCache.compute_md5(small)
            md5_large = TieredCache.compute_md5(large)

            cache._memory.set(md5_small, small)
            await cache._disk.set(md5_large, large)

            await cache.clear()

            assert await cache.get(md5_small) is None
            assert await cache.get(md5_large) is None

    def test_compute_md5(self):
        """Test MD5 computation helper."""
        content = b"Hello World!"
        expected = hashlib.md5(content).hexdigest()

        assert TieredCache.compute_md5(content) == expected

    @pytest.mark.asyncio
    async def test_cleanup_expired(self):
        """Test cleanup of expired entries in both tiers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TieredCache(
                memory_ttl_seconds=0,
                disk_dir=tmpdir,
                disk_ttl_seconds=0,
            )
            await cache.init()

            cache._memory.set("key1", b"memory")
            await cache._disk.set("key2", b"disk")

            await asyncio.sleep(0.01)
            memory_removed, disk_removed = await cache.cleanup_expired()

            assert memory_removed >= 1
            assert disk_removed >= 1
