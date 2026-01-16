"""Tests for the AttachmentManager class."""

import base64
import hashlib
import tempfile
from pathlib import Path

import pytest

from async_mail_service.attachments import AttachmentManager, is_storage_available
from async_mail_service.attachments.cache import TieredCache


class TestParseFilename:
    """Tests for filename parsing with MD5 marker."""

    def test_no_marker(self):
        """Test filename without MD5 marker."""
        filename, md5 = AttachmentManager.parse_filename("report.pdf")

        assert filename == "report.pdf"
        assert md5 is None

    def test_with_marker_in_middle(self):
        """Test filename with MD5 marker in the middle."""
        filename, md5 = AttachmentManager.parse_filename(
            "report_{MD5:a1b2c3d4e5f6}.pdf"
        )

        assert filename == "report.pdf"
        assert md5 == "a1b2c3d4e5f6"

    def test_with_marker_at_end(self):
        """Test filename with MD5 marker at the end."""
        filename, md5 = AttachmentManager.parse_filename(
            "report{MD5:abcdef123456}.pdf"
        )

        assert filename == "report.pdf"
        assert md5 == "abcdef123456"

    def test_uppercase_md5(self):
        """Test MD5 marker with uppercase hex digits."""
        filename, md5 = AttachmentManager.parse_filename(
            "file_{MD5:ABCDEF123456}.txt"
        )

        assert filename == "file.txt"
        assert md5 == "abcdef123456"  # Converted to lowercase

    def test_multiple_underscores_cleaned(self):
        """Test that multiple underscores are cleaned up."""
        filename, md5 = AttachmentManager.parse_filename(
            "report__{MD5:abc123}__final.pdf"
        )

        assert filename == "report_final.pdf"
        assert md5 == "abc123"

    def test_marker_between_name_and_extension(self):
        """Test marker placed directly before extension."""
        filename, md5 = AttachmentManager.parse_filename(
            "report_{MD5:abc123}.pdf"
        )

        assert filename == "report.pdf"
        assert md5 == "abc123"


class TestParseStoragePath:
    """Tests for storage path parsing and routing."""

    def test_base64_path(self):
        """Test base64: prefix is recognized."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("base64:SGVsbG8=")

        assert path_type == "base64"
        assert parsed == "SGVsbG8="

    def test_http_path(self):
        """Test @params format is recognized."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("@doc_id=123")

        assert path_type == "http"
        assert parsed == "doc_id=123"

    def test_http_path_with_url(self):
        """Test @[url]params format is recognized."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path(
            "@[https://api.example.com/files]doc_id=123"
        )

        assert path_type == "http"
        assert parsed == "[https://api.example.com/files]doc_id=123"

    def test_absolute_filesystem_path(self):
        """Test absolute path is recognized as filesystem."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("/var/files/doc.pdf")

        assert path_type == "filesystem"
        assert parsed == "/var/files/doc.pdf"

    def test_relative_filesystem_path(self):
        """Test relative path is recognized as filesystem."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("uploads/doc.pdf")

        assert path_type == "filesystem"
        assert parsed == "uploads/doc.pdf"

    def test_volume_path_without_storage(self):
        """Test volume:path raises error without genro-storage."""
        manager = AttachmentManager(storage_manager=None)

        with pytest.raises(RuntimeError, match="genro-storage required"):
            manager._parse_storage_path("documents:reports/q1.pdf")

    def test_empty_path_rejected(self):
        """Test empty path raises ValueError."""
        manager = AttachmentManager()

        with pytest.raises(ValueError, match="Empty storage_path"):
            manager._parse_storage_path("")


class TestFetchBase64:
    """Tests for fetching base64 attachments."""

    @pytest.mark.asyncio
    async def test_fetch_base64_content(self):
        """Test fetching base64-encoded attachment."""
        manager = AttachmentManager()
        original = b"Hello World!"
        encoded = base64.b64encode(original).decode()

        result = await manager.fetch({
            "filename": "test.txt",
            "storage_path": f"base64:{encoded}",
        })

        assert result is not None
        content, filename = result
        assert content == original
        assert filename == "test.txt"


class TestFetchFilesystem:
    """Tests for fetching filesystem attachments."""

    @pytest.mark.asyncio
    async def test_fetch_absolute_path(self):
        """Test fetching file with absolute path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"
            test_content = b"test content"
            test_file.write_bytes(test_content)

            manager = AttachmentManager(base_dir=tmpdir)
            result = await manager.fetch({
                "filename": "test.txt",
                "storage_path": str(test_file),
            })

            assert result is not None
            content, filename = result
            assert content == test_content
            assert filename == "test.txt"

    @pytest.mark.asyncio
    async def test_fetch_relative_path(self):
        """Test fetching file with relative path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "test.txt"
            test_content = b"test content"
            test_file.write_bytes(test_content)

            manager = AttachmentManager(base_dir=tmpdir)
            result = await manager.fetch({
                "filename": "output.txt",
                "storage_path": "test.txt",
            })

            assert result is not None
            content, filename = result
            assert content == test_content
            assert filename == "output.txt"


class TestFetchWithCache:
    """Tests for caching behavior."""

    @pytest.mark.asyncio
    async def test_cache_hit_with_md5_marker(self):
        """Test that cache is used when MD5 marker is provided."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TieredCache(
                memory_max_mb=10,
                disk_dir=tmpdir,
            )
            await cache.init()

            # Pre-populate cache
            content = b"cached content"
            md5 = hashlib.md5(content).hexdigest()
            await cache.set(md5, content)

            manager = AttachmentManager(cache=cache)
            result = await manager.fetch({
                "filename": f"test_{{MD5:{md5}}}.txt",
                "storage_path": "base64:dW51c2Vk",  # Different content
            })

            assert result is not None
            cached_content, filename = result
            assert cached_content == content  # Got cached version
            assert filename == "test.txt"  # MD5 marker stripped

    @pytest.mark.asyncio
    async def test_cache_populated_after_fetch(self):
        """Test that fetched content is cached."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TieredCache(
                memory_max_mb=10,
                disk_dir=tmpdir,
            )
            await cache.init()

            manager = AttachmentManager(cache=cache)
            content = b"Hello World!"
            encoded = base64.b64encode(content).decode()

            # Fetch without MD5 marker
            result = await manager.fetch({
                "filename": "test.txt",
                "storage_path": f"base64:{encoded}",
            })

            assert result is not None

            # Check content was cached
            md5 = hashlib.md5(content).hexdigest()
            cached = await cache.get(md5)
            assert cached == content


class TestFetchBatch:
    """Tests for batch fetching."""

    @pytest.mark.asyncio
    async def test_batch_fetch_base64(self):
        """Test batch fetching base64 attachments."""
        manager = AttachmentManager()

        content1 = b"content 1"
        content2 = b"content 2"
        enc1 = base64.b64encode(content1).decode()
        enc2 = base64.b64encode(content2).decode()

        results = await manager.fetch_batch([
            {"filename": "file1.txt", "storage_path": f"base64:{enc1}"},
            {"filename": "file2.txt", "storage_path": f"base64:{enc2}"},
        ])

        assert f"base64:{enc1}" in results
        assert f"base64:{enc2}" in results
        assert results[f"base64:{enc1}"][0] == content1
        assert results[f"base64:{enc2}"][0] == content2

    @pytest.mark.asyncio
    async def test_batch_uses_cache(self):
        """Test that batch fetch uses cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TieredCache(memory_max_mb=10, disk_dir=tmpdir)
            await cache.init()

            # Pre-populate cache
            content = b"cached content"
            md5 = hashlib.md5(content).hexdigest()
            await cache.set(md5, content)

            manager = AttachmentManager(cache=cache)
            results = await manager.fetch_batch([
                {"filename": f"file_{{MD5:{md5}}}.txt", "storage_path": "base64:unused"},
            ])

            # Should have cache hit
            assert len(results) == 1
            storage_path, (fetched_content, filename) = list(results.items())[0]
            assert fetched_content == content


class TestGuessMime:
    """Tests for MIME type detection."""

    def test_pdf(self):
        """Test PDF MIME type."""
        maintype, subtype = AttachmentManager.guess_mime("document.pdf")
        assert maintype == "application"
        assert subtype == "pdf"

    def test_html(self):
        """Test HTML MIME type."""
        maintype, subtype = AttachmentManager.guess_mime("page.html")
        assert maintype == "text"
        assert subtype == "html"

    def test_image(self):
        """Test image MIME type."""
        maintype, subtype = AttachmentManager.guess_mime("photo.jpg")
        assert maintype == "image"
        assert subtype == "jpeg"

    def test_unknown(self):
        """Test unknown extension defaults to octet-stream."""
        maintype, subtype = AttachmentManager.guess_mime("file.unknownext")
        assert maintype == "application"
        assert subtype == "octet-stream"


class TestIsStorageAvailable:
    """Tests for the is_storage_available function."""

    def test_returns_boolean(self):
        """Test the function returns a boolean."""
        result = is_storage_available()
        assert isinstance(result, bool)
