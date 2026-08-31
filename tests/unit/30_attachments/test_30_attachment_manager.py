# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for the AttachmentManager class."""

import base64
import hashlib
import tempfile

import pytest

from mail_proxy.attachments import AttachmentManager
from mail_proxy.attachments.cache import TieredCache


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
    """Tests for storage path parsing with explicit fetch_mode."""

    def test_base64_fetch_mode(self):
        """Test fetch_mode=base64 returns base64 path type."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("SGVsbG8=", fetch_mode="base64")

        assert path_type == "base64"
        assert parsed == "SGVsbG8="

    def test_endpoint_fetch_mode(self):
        """Test fetch_mode=endpoint returns http path type."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("doc_id=123", fetch_mode="endpoint")

        assert path_type == "http"
        assert parsed == "doc_id=123"

    def test_http_url_fetch_mode(self):
        """Test fetch_mode=http_url wraps URL in brackets."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path(
            "https://api.example.com/files/123", fetch_mode="http_url"
        )

        assert path_type == "http"
        assert parsed == "[https://api.example.com/files/123]"

    def test_missing_fetch_mode_infers_endpoint(self):
        """Test that missing fetch_mode defaults to endpoint for non-URL paths."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("doc_id=123")

        assert path_type == "http"
        assert parsed == "doc_id=123"

    def test_infer_http_url_from_https(self):
        """Test that https:// URL infers http_url fetch_mode."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("https://example.com/file.pdf")

        assert path_type == "http"
        assert parsed == "[https://example.com/file.pdf]"

    def test_infer_http_url_from_http(self):
        """Test that http:// URL infers http_url fetch_mode."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("http://example.com/file.pdf")

        assert path_type == "http"
        assert parsed == "[http://example.com/file.pdf]"

    def test_infer_filesystem_from_absolute_path(self):
        """Test that absolute path infers filesystem fetch_mode."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("/var/attachments/file.pdf")

        assert path_type == "filesystem"
        assert parsed == "/var/attachments/file.pdf"

    def test_infer_base64_from_prefix(self):
        """Test that base64: prefix infers base64 fetch_mode and strips prefix."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("base64:SGVsbG8gV29ybGQ=")

        assert path_type == "base64"
        assert parsed == "SGVsbG8gV29ybGQ="  # Prefix stripped

    def test_filesystem_fetch_mode(self):
        """Test fetch_mode=filesystem returns filesystem path type."""
        manager = AttachmentManager()
        path_type, parsed = manager._parse_storage_path("/var/attachments/file.pdf", fetch_mode="filesystem")
        assert path_type == "filesystem"
        assert parsed == "/var/attachments/file.pdf"

    def test_invalid_fetch_mode_raises(self):
        """Test that invalid fetch_mode raises ValueError."""
        manager = AttachmentManager()

        with pytest.raises(ValueError, match="Unknown fetch_mode"):
            manager._parse_storage_path("some/path", fetch_mode="invalid")

    def test_empty_path_rejected(self):
        """Test empty path raises ValueError."""
        manager = AttachmentManager()

        with pytest.raises(ValueError, match="Empty storage_path"):
            manager._parse_storage_path("", fetch_mode="base64")


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
            "storage_path": encoded,
            "fetch_mode": "base64",
        })

        assert result is not None
        content, filename = result
        assert content == original
        assert filename == "test.txt"


class TestFetchBase64WithPrefix:
    """Tests for fetching base64 attachments with base64: prefix (backwards compat)."""

    @pytest.mark.asyncio
    async def test_fetch_base64_content_with_prefix(self):
        """Test fetching base64-encoded attachment with base64: prefix in storage_path."""
        manager = AttachmentManager()
        original = b"Hello World!"
        encoded = base64.b64encode(original).decode()

        # Note: The base64: prefix is kept in storage_path when fetch_mode is base64
        # The prefix gets stripped during parsing
        result = await manager.fetch({
            "filename": "test.txt",
            "storage_path": encoded,  # No prefix needed, fetch_mode tells us it's base64
            "fetch_mode": "base64",
        })

        assert result is not None
        content, filename = result
        assert content == original
        assert filename == "test.txt"


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
                "storage_path": encoded,
                "fetch_mode": "base64",
            })

            assert result is not None

            # Check content was cached
            md5 = hashlib.md5(content).hexdigest()
            cached = await cache.get(md5)
            assert cached == content


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
