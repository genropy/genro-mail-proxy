"""Tests for attachment fetcher modules."""

import base64
import tempfile
from pathlib import Path

import pytest

from mail_proxy.attachments.base64_fetcher import Base64Fetcher
from mail_proxy.attachments.filesystem_fetcher import FilesystemFetcher


class TestBase64Fetcher:
    """Tests for the Base64Fetcher class."""

    @pytest.mark.asyncio
    async def test_decode_valid_base64(self):
        """Test decoding valid base64 content."""
        fetcher = Base64Fetcher()
        original = b"Hello World!"
        encoded = base64.b64encode(original).decode()

        result = await fetcher.fetch(encoded)

        assert result == original

    @pytest.mark.asyncio
    async def test_decode_empty_content(self):
        """Test decoding empty content returns None."""
        fetcher = Base64Fetcher()
        result = await fetcher.fetch("")
        assert result is None

    @pytest.mark.asyncio
    async def test_decode_with_whitespace(self):
        """Test decoding handles whitespace."""
        fetcher = Base64Fetcher()
        original = b"Hello World!"
        encoded = "  " + base64.b64encode(original).decode() + "  "

        result = await fetcher.fetch(encoded)

        assert result == original

    @pytest.mark.asyncio
    async def test_decode_missing_padding(self):
        """Test decoding handles missing padding."""
        fetcher = Base64Fetcher()
        original = b"Hello World!"
        encoded = base64.b64encode(original).decode().rstrip("=")

        result = await fetcher.fetch(encoded)

        assert result == original

    @pytest.mark.asyncio
    async def test_decode_invalid_base64(self):
        """Test decoding invalid base64 raises ValueError."""
        fetcher = Base64Fetcher()

        with pytest.raises(ValueError, match="Invalid base64"):
            await fetcher.fetch("not valid base64!!!")

    @pytest.mark.asyncio
    async def test_decode_binary_content(self):
        """Test decoding binary content."""
        fetcher = Base64Fetcher()
        original = bytes(range(256))  # All byte values
        encoded = base64.b64encode(original).decode()

        result = await fetcher.fetch(encoded)

        assert result == original


class TestFilesystemFetcher:
    """Tests for the FilesystemFetcher class."""

    @pytest.mark.asyncio
    async def test_fetch_absolute_path(self):
        """Test fetching file with absolute path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test file
            test_file = Path(tmpdir) / "test.txt"
            test_content = b"test content"
            test_file.write_bytes(test_content)

            fetcher = FilesystemFetcher(base_dir=tmpdir)
            result = await fetcher.fetch(str(test_file))

            assert result == test_content

    @pytest.mark.asyncio
    async def test_fetch_relative_path(self):
        """Test fetching file with relative path."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create test file
            test_file = Path(tmpdir) / "subdir" / "test.txt"
            test_file.parent.mkdir(parents=True)
            test_content = b"test content"
            test_file.write_bytes(test_content)

            fetcher = FilesystemFetcher(base_dir=tmpdir)
            result = await fetcher.fetch("subdir/test.txt")

            assert result == test_content

    @pytest.mark.asyncio
    async def test_path_traversal_blocked(self):
        """Test that path traversal attempts are blocked."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a file outside base_dir
            outside_file = Path(tmpdir).parent / "outside.txt"
            try:
                outside_file.write_bytes(b"outside content")

                fetcher = FilesystemFetcher(base_dir=tmpdir)

                with pytest.raises(ValueError, match="Path traversal"):
                    await fetcher.fetch("../outside.txt")
            finally:
                if outside_file.exists():
                    outside_file.unlink()

    @pytest.mark.asyncio
    async def test_file_not_found(self):
        """Test that missing files raise FileNotFoundError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = FilesystemFetcher(base_dir=tmpdir)

            with pytest.raises(FileNotFoundError):
                await fetcher.fetch("nonexistent.txt")

    @pytest.mark.asyncio
    async def test_empty_path_rejected(self):
        """Test that empty path raises ValueError."""
        fetcher = FilesystemFetcher(base_dir="/tmp")

        with pytest.raises(ValueError, match="Empty path"):
            await fetcher.fetch("")

    @pytest.mark.asyncio
    async def test_relative_path_without_base_dir(self):
        """Test that relative paths without base_dir raise ValueError."""
        fetcher = FilesystemFetcher(base_dir=None)

        with pytest.raises(ValueError, match="Relative path.*not allowed"):
            await fetcher.fetch("relative/path.txt")

    @pytest.mark.asyncio
    async def test_directory_rejected(self):
        """Test that directories are rejected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir()

            fetcher = FilesystemFetcher(base_dir=tmpdir)

            with pytest.raises(ValueError, match="Not a regular file"):
                await fetcher.fetch("subdir")

    @pytest.mark.asyncio
    async def test_binary_file(self):
        """Test fetching binary file content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            test_file = Path(tmpdir) / "binary.bin"
            test_content = bytes(range(256))
            test_file.write_bytes(test_content)

            fetcher = FilesystemFetcher(base_dir=tmpdir)
            result = await fetcher.fetch("binary.bin")

            assert result == test_content

    def test_base_dir_property(self):
        """Test base_dir property."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fetcher = FilesystemFetcher(base_dir=tmpdir)
            # base_dir is resolved, so compare resolved paths
            assert fetcher.base_dir == Path(tmpdir).resolve()

        fetcher_none = FilesystemFetcher(base_dir=None)
        assert fetcher_none.base_dir is None
