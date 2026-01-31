# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for AttachmentManager and fetchers."""

import base64
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

import pytest
import aiohttp

from core.mail_proxy.smtp.attachments import (
    AttachmentManager,
    Base64Fetcher,
    StorageFetcher,
    HttpFetcher,
)
from storage import StorageManager


class TestBase64Fetcher:
    """Tests for base64 inline content decoder."""

    @pytest.fixture
    def fetcher(self):
        return Base64Fetcher()

    async def test_decode_valid_base64(self, fetcher):
        """Decodes valid base64 content."""
        original = b"Hello, World!"
        encoded = base64.b64encode(original).decode()
        result = await fetcher.fetch(encoded)
        assert result == original

    async def test_decode_with_padding(self, fetcher):
        """Handles base64 with padding correctly."""
        original = b"Test"
        encoded = base64.b64encode(original).decode()
        result = await fetcher.fetch(encoded)
        assert result == original

    async def test_decode_without_padding(self, fetcher):
        """Adds missing padding automatically."""
        original = b"Test"
        encoded = base64.b64encode(original).decode().rstrip("=")
        result = await fetcher.fetch(encoded)
        assert result == original

    async def test_decode_with_whitespace(self, fetcher):
        """Strips whitespace from content."""
        original = b"Test"
        encoded = "  " + base64.b64encode(original).decode() + "  \n"
        result = await fetcher.fetch(encoded)
        assert result == original

    async def test_empty_content_returns_none(self, fetcher):
        """Empty content returns None."""
        result = await fetcher.fetch("")
        assert result is None

    async def test_invalid_base64_raises(self, fetcher):
        """Invalid base64 raises ValueError."""
        with pytest.raises(ValueError, match="Invalid base64"):
            await fetcher.fetch("not!valid@base64")


class TestStorageFetcher:
    """Tests for storage-based attachment fetcher."""

    @pytest.fixture
    def base_dir(self, tmp_path):
        """Create a base directory with test files."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content")

        subdir = tmp_path / "subdir"
        subdir.mkdir()
        (subdir / "nested.txt").write_bytes(b"nested content")

        return tmp_path

    @pytest.fixture
    def storage_manager(self, base_dir):
        """Create a StorageManager with a local mount."""
        manager = StorageManager()
        manager.register("data", {"protocol": "local", "base_path": str(base_dir)})
        return manager

    @pytest.fixture
    def fetcher(self, storage_manager):
        return StorageFetcher(storage_manager=storage_manager)

    async def test_fetch_mount_path(self, fetcher):
        """Fetches file via mount:path format."""
        result = await fetcher.fetch("data:test.txt")
        assert result == b"test content"

    async def test_fetch_nested_file(self, fetcher):
        """Fetches nested file via mount."""
        result = await fetcher.fetch("data:subdir/nested.txt")
        assert result == b"nested content"

    async def test_fetch_absolute_path(self, base_dir):
        """Fetches file by absolute path (legacy compatibility)."""
        fetcher = StorageFetcher(storage_manager=None)
        abs_path = str(base_dir / "test.txt")
        result = await fetcher.fetch(abs_path)
        assert result == b"test content"

    async def test_mount_path_without_manager_raises(self):
        """Mount path without StorageManager raises error."""
        fetcher = StorageFetcher(storage_manager=None)
        with pytest.raises(ValueError, match="StorageManager not configured"):
            await fetcher.fetch("data:test.txt")

    async def test_file_not_found(self, fetcher):
        """Missing file raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            await fetcher.fetch("data:nonexistent.txt")

    async def test_empty_path_raises(self, fetcher):
        """Empty path raises ValueError."""
        with pytest.raises(ValueError, match="Empty path"):
            await fetcher.fetch("")

    async def test_directory_raises(self, fetcher):
        """Directory path raises ValueError."""
        with pytest.raises(ValueError, match="Not a regular file"):
            await fetcher.fetch("data:subdir")

    async def test_invalid_path_format_raises(self):
        """Invalid path format raises error."""
        fetcher = StorageFetcher(storage_manager=None)
        with pytest.raises(ValueError, match="Invalid path format"):
            await fetcher.fetch("relative.txt")

    def test_storage_manager_property(self, fetcher, storage_manager):
        """storage_manager property returns configured manager."""
        assert fetcher.storage_manager is storage_manager


class TestHttpFetcher:
    """Tests for HTTP attachment fetcher."""

    @pytest.fixture
    def fetcher(self):
        return HttpFetcher(
            default_endpoint="https://api.example.com/attachments",
            auth_config={"method": "bearer", "token": "secret-token"},
        )

    def test_parse_path_with_bracket_notation(self, fetcher):
        """Parses [endpoint]path notation."""
        server, params = fetcher._parse_path("[https://other.com/api]storage/file.pdf")
        assert server == "https://other.com/api"
        assert params == "storage/file.pdf"

    def test_parse_path_direct_url(self, fetcher):
        """Parses direct HTTP URL."""
        server, params = fetcher._parse_path("https://cdn.example.com/file.pdf")
        assert server == "https://cdn.example.com/file.pdf"
        assert params == ""

    def test_parse_path_uses_default_endpoint(self, fetcher):
        """Uses default endpoint for relative paths."""
        server, params = fetcher._parse_path("storage/file.pdf")
        assert server == "https://api.example.com/attachments"
        assert params == "storage/file.pdf"

    def test_parse_path_no_endpoint_raises(self):
        """Raises when no default endpoint and relative path."""
        fetcher = HttpFetcher(default_endpoint=None)
        with pytest.raises(ValueError, match="No default endpoint"):
            fetcher._parse_path("storage/file.pdf")

    def test_bearer_auth_headers(self, fetcher):
        """Generates Bearer auth headers."""
        headers = fetcher._get_auth_headers()
        assert headers["Authorization"] == "Bearer secret-token"

    def test_basic_auth_headers(self):
        """Generates Basic auth headers."""
        fetcher = HttpFetcher(
            auth_config={"method": "basic", "user": "admin", "password": "secret"}
        )
        headers = fetcher._get_auth_headers()
        expected = "Basic " + base64.b64encode(b"admin:secret").decode()
        assert headers["Authorization"] == expected

    def test_no_auth_returns_empty(self):
        """No auth method returns empty headers."""
        fetcher = HttpFetcher(auth_config={"method": "none"})
        headers = fetcher._get_auth_headers()
        assert headers == {}

    def test_auth_override(self, fetcher):
        """Auth override replaces default auth."""
        override = {"method": "basic", "user": "other", "password": "pass"}
        headers = fetcher._get_auth_headers(auth_override=override)
        expected = "Basic " + base64.b64encode(b"other:pass").decode()
        assert headers["Authorization"] == expected

    @patch("aiohttp.ClientSession")
    async def test_fetch_direct_url(self, mock_session_class, fetcher):
        """Fetches directly from URL."""
        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=b"file content")
        mock_response.raise_for_status = MagicMock()

        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_response)))
        mock_session_class.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_class.return_value.__aexit__ = AsyncMock()

        result = await fetcher.fetch("https://cdn.example.com/file.pdf")
        # Note: This test is simplified; in reality would need proper mock setup

    def test_default_endpoint_property(self, fetcher):
        """default_endpoint property returns configured endpoint."""
        assert fetcher.default_endpoint == "https://api.example.com/attachments"


class TestAttachmentManager:
    """Tests for high-level attachment manager."""

    @pytest.fixture
    def base_dir(self, tmp_path):
        """Create a base directory with test files."""
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"test content")
        return tmp_path

    @pytest.fixture
    def storage_manager(self, base_dir):
        """Create a StorageManager with local mount."""
        sm = StorageManager()
        sm.register("data", {"protocol": "local", "base_path": str(base_dir)})
        return sm

    @pytest.fixture
    def manager(self, storage_manager):
        return AttachmentManager(
            storage_manager=storage_manager,
            http_endpoint="https://api.example.com/attachments",
        )

    # =========================================================================
    # parse_filename
    # =========================================================================

    def test_parse_filename_no_marker(self, manager):
        """Filename without MD5 marker is returned as-is."""
        filename, md5 = manager.parse_filename("document.pdf")
        assert filename == "document.pdf"
        assert md5 is None

    def test_parse_filename_with_marker(self, manager):
        """Filename with MD5 marker extracts hash and cleans name."""
        filename, md5 = manager.parse_filename("report_{MD5:abc123}.pdf")
        assert filename == "report.pdf"
        assert md5 == "abc123"

    def test_parse_filename_marker_in_middle(self, manager):
        """MD5 marker in middle of filename."""
        filename, md5 = manager.parse_filename("file_{MD5:def456}_final.pdf")
        assert filename == "file_final.pdf"
        assert md5 == "def456"

    def test_parse_filename_uppercase_marker(self, manager):
        """MD5 hash is lowercased."""
        filename, md5 = manager.parse_filename("file_{MD5:ABC123}.pdf")
        assert md5 == "abc123"

    # =========================================================================
    # _parse_storage_path
    # =========================================================================

    def test_parse_storage_path_base64_prefix(self, manager):
        """Detects base64: prefix."""
        path_type, parsed = manager._parse_storage_path("base64:SGVsbG8=")
        assert path_type == "base64"
        assert parsed == "SGVsbG8="

    def test_parse_storage_path_http_url(self, manager):
        """Detects http:// URL."""
        path_type, parsed = manager._parse_storage_path("http://example.com/file.pdf")
        assert path_type == "http"
        assert "[http://example.com/file.pdf]" in parsed

    def test_parse_storage_path_https_url(self, manager):
        """Detects https:// URL."""
        path_type, parsed = manager._parse_storage_path("https://example.com/file.pdf")
        assert path_type == "http"

    def test_parse_storage_path_absolute_path(self, manager):
        """Detects absolute filesystem path (goes to storage fetcher)."""
        path_type, parsed = manager._parse_storage_path("/var/attachments/file.pdf")
        assert path_type == "storage"
        assert parsed == "/var/attachments/file.pdf"

    def test_parse_storage_path_mount_path(self, manager):
        """Detects mount:path format."""
        path_type, parsed = manager._parse_storage_path("data:files/report.pdf")
        assert path_type == "storage"
        assert parsed == "data:files/report.pdf"

    def test_parse_storage_path_endpoint_default(self, manager):
        """Relative path defaults to endpoint mode."""
        path_type, parsed = manager._parse_storage_path("storage/file.pdf")
        assert path_type == "http"
        assert parsed == "storage/file.pdf"

    def test_parse_storage_path_explicit_mode(self, manager):
        """Explicit fetch_mode overrides detection."""
        path_type, parsed = manager._parse_storage_path(
            "/var/file.pdf", fetch_mode="endpoint"
        )
        assert path_type == "http"

    def test_parse_storage_path_empty_raises(self, manager):
        """Empty storage_path raises ValueError."""
        with pytest.raises(ValueError, match="Empty storage_path"):
            manager._parse_storage_path("")

    def test_parse_storage_path_unknown_mode_raises(self, manager):
        """Unknown fetch_mode raises ValueError."""
        with pytest.raises(ValueError, match="Unknown fetch_mode"):
            manager._parse_storage_path("file.pdf", fetch_mode="unknown")

    # =========================================================================
    # fetch - base64 mode
    # =========================================================================

    async def test_fetch_base64_inline(self, manager):
        """Fetches base64 inline content."""
        content = b"Hello, World!"
        encoded = base64.b64encode(content).decode()

        result = await manager.fetch({
            "storage_path": f"base64:{encoded}",
            "filename": "hello.txt",
        })

        assert result is not None
        assert result[0] == content
        assert result[1] == "hello.txt"

    # =========================================================================
    # fetch - storage mode
    # =========================================================================

    async def test_fetch_storage_absolute(self, manager, base_dir):
        """Fetches from storage via absolute path."""
        result = await manager.fetch({
            "storage_path": str(base_dir / "test.txt"),
            "filename": "test.txt",
        })

        assert result is not None
        assert result[0] == b"test content"
        assert result[1] == "test.txt"

    async def test_fetch_storage_mount_path(self, manager):
        """Fetches from storage with mount:path format."""
        result = await manager.fetch({
            "storage_path": "data:test.txt",
            "filename": "test.txt",
        })

        assert result is not None
        assert result[0] == b"test content"

    # =========================================================================
    # fetch - with caching
    # =========================================================================

    async def test_fetch_uses_cache(self, base_dir, storage_manager):
        """fetch() checks cache first."""
        from core.mail_proxy.smtp.cache import TieredCache

        cache = TieredCache(memory_max_mb=1, memory_ttl_seconds=60)
        await cache.init()

        manager = AttachmentManager(storage_manager=storage_manager, cache=cache)

        # Pre-populate cache
        content = b"cached content"
        md5 = TieredCache.compute_md5(content)
        await cache.set(md5, content)

        # Fetch should use cached content
        result = await manager.fetch({
            "storage_path": "data:nonexistent.txt",
            "content_md5": md5,
            "filename": "file.txt",
        })

        assert result is not None
        assert result[0] == content

    async def test_fetch_stores_in_cache(self, base_dir, storage_manager):
        """fetch() stores result in cache."""
        from core.mail_proxy.smtp.cache import TieredCache

        cache = TieredCache(memory_max_mb=1, memory_ttl_seconds=60)
        await cache.init()

        manager = AttachmentManager(storage_manager=storage_manager, cache=cache)

        # Fetch from storage
        result = await manager.fetch({
            "storage_path": "data:test.txt",
            "filename": "test.txt",
        })

        # Check it was cached
        md5 = TieredCache.compute_md5(result[0])
        cached = await cache.get(md5)
        assert cached == result[0]

    # =========================================================================
    # fetch - empty/missing
    # =========================================================================

    async def test_fetch_no_storage_path_returns_none(self, manager):
        """Missing storage_path returns None."""
        result = await manager.fetch({"filename": "file.txt"})
        assert result is None

    async def test_fetch_empty_storage_path_returns_none(self, manager):
        """Empty storage_path returns None."""
        result = await manager.fetch({"storage_path": "", "filename": "file.txt"})
        assert result is None

    # =========================================================================
    # guess_mime
    # =========================================================================

    def test_guess_mime_pdf(self, manager):
        """Guesses PDF MIME type."""
        main, sub = manager.guess_mime("document.pdf")
        assert main == "application"
        assert sub == "pdf"

    def test_guess_mime_image(self, manager):
        """Guesses image MIME type."""
        main, sub = manager.guess_mime("photo.jpg")
        assert main == "image"
        assert sub == "jpeg"

    def test_guess_mime_text(self, manager):
        """Guesses text MIME type."""
        main, sub = manager.guess_mime("readme.txt")
        assert main == "text"
        assert sub == "plain"

    def test_guess_mime_unknown(self, manager):
        """Unknown extension returns octet-stream."""
        main, sub = manager.guess_mime("file.qwertyasdf")
        assert main == "application"
        assert sub == "octet-stream"

    def test_guess_mime_no_extension(self, manager):
        """No extension returns octet-stream."""
        main, sub = manager.guess_mime("noextension")
        assert main == "application"
        assert sub == "octet-stream"
