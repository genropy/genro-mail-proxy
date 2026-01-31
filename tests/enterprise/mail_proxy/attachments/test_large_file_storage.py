# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for LargeFileStorage with mocked fsspec."""

import time
from unittest.mock import MagicMock, patch

import pytest

from enterprise.mail_proxy.attachments.large_file_storage import (
    LargeFileStorage,
    LargeFileStorageError,
    StorageNotConfiguredError,
    UploadError,
)


class TestLargeFileStorageExceptions:
    """Tests for exception classes."""

    def test_exception_hierarchy(self):
        """All exceptions inherit from LargeFileStorageError."""
        assert issubclass(StorageNotConfiguredError, LargeFileStorageError)
        assert issubclass(UploadError, LargeFileStorageError)

    def test_exceptions_can_be_raised(self):
        """Exceptions can be raised with messages."""
        with pytest.raises(LargeFileStorageError, match="test error"):
            raise LargeFileStorageError("test error")

        with pytest.raises(StorageNotConfiguredError, match="not configured"):
            raise StorageNotConfiguredError("not configured")

        with pytest.raises(UploadError, match="upload failed"):
            raise UploadError("upload failed")


class TestLargeFileStorageInit:
    """Tests for LargeFileStorage initialization."""

    def test_init_defaults(self):
        """Init with minimal parameters."""
        storage = LargeFileStorage(storage_url="s3://my-bucket/attachments")

        assert storage.storage_url == "s3://my-bucket/attachments"
        assert storage.public_base_url is None
        assert storage.secret_key == "genro-mail-proxy-default-secret-change-me"
        assert storage._storage_options == {}
        assert storage._fs is None
        assert storage._base_path is None

    def test_init_with_all_params(self):
        """Init with all parameters."""
        storage = LargeFileStorage(
            storage_url="s3://bucket/path",
            public_base_url="https://files.example.com",
            secret_key="my-secret-key",
            storage_options={"region": "us-east-1"},
        )

        assert storage.storage_url == "s3://bucket/path"
        assert storage.public_base_url == "https://files.example.com"
        assert storage.secret_key == "my-secret-key"
        assert storage._storage_options == {"region": "us-east-1"}


class TestLargeFileStorageFilesystemInit:
    """Tests for lazy filesystem initialization."""

    def test_fs_property_initializes_lazily(self):
        """fs property initializes fsspec on first access."""
        storage = LargeFileStorage(storage_url="file:///tmp/test")

        mock_fs = MagicMock()
        mock_fsspec = MagicMock()
        mock_fsspec.core.url_to_fs.return_value = (mock_fs, "/tmp/test")

        with patch.dict("sys.modules", {"fsspec": mock_fsspec}):
            # Access the property - should initialize
            result = storage.fs

        assert result is mock_fs
        mock_fsspec.core.url_to_fs.assert_called_once_with(
            "file:///tmp/test"
        )

    def test_base_path_property(self):
        """base_path property returns the path from fsspec."""
        storage = LargeFileStorage(storage_url="s3://bucket/path/to/files")

        mock_fs = MagicMock()
        mock_fsspec = MagicMock()
        mock_fsspec.core.url_to_fs.return_value = (mock_fs, "path/to/files")

        with patch.dict("sys.modules", {"fsspec": mock_fsspec}):
            result = storage.base_path

        assert result == "path/to/files"

    def test_base_path_none_returns_empty_string(self):
        """base_path returns empty string when fsspec returns None."""
        storage = LargeFileStorage(storage_url="s3://bucket")

        mock_fs = MagicMock()
        mock_fsspec = MagicMock()
        mock_fsspec.core.url_to_fs.return_value = (mock_fs, None)

        with patch.dict("sys.modules", {"fsspec": mock_fsspec}):
            result = storage.base_path

        assert result == ""

    def test_fsspec_import_error(self):
        """Missing fsspec raises ImportError."""
        storage = LargeFileStorage(storage_url="s3://bucket")

        with patch.dict("sys.modules", {"fsspec": None}):
            with pytest.raises((ImportError, TypeError)):
                _ = storage.fs


class TestLargeFileStorageGetFilePath:
    """Tests for _get_file_path method."""

    def test_get_file_path(self):
        """_get_file_path builds correct path."""
        storage = LargeFileStorage(storage_url="s3://bucket/attachments")

        # Mock the base_path
        storage._base_path = "attachments"
        storage._fs = MagicMock()

        path = storage._get_file_path("file-123", "report.pdf")

        assert path == "attachments/file-123/report.pdf"


class TestLargeFileStorageUpload:
    """Tests for upload method."""

    @pytest.fixture
    def storage(self):
        """Create storage with mocked fs."""
        storage = LargeFileStorage(storage_url="s3://bucket/files")
        storage._base_path = "files"
        storage._fs = MagicMock()
        return storage

    async def test_upload_success(self, storage):
        """Upload succeeds and returns path."""
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        storage._fs.open.return_value = mock_file

        result = await storage.upload("file-001", b"file content", "doc.pdf")

        assert result == "files/file-001/doc.pdf"
        storage._fs.makedirs.assert_called_once_with("files/file-001", exist_ok=True)
        storage._fs.open.assert_called_once_with("files/file-001/doc.pdf", "wb")
        mock_file.write.assert_called_once_with(b"file content")

    async def test_upload_failure_raises_upload_error(self, storage):
        """Upload failure raises UploadError."""
        storage._fs.makedirs.side_effect = PermissionError("Access denied")

        with pytest.raises(UploadError, match="Failed to upload"):
            await storage.upload("file-002", b"content", "file.txt")


class TestLargeFileStorageDownloadUrl:
    """Tests for get_download_url method."""

    @pytest.fixture
    def storage(self):
        """Create storage with mocked fs."""
        storage = LargeFileStorage(
            storage_url="s3://bucket/files",
            secret_key="test-secret",
        )
        storage._base_path = "files"
        storage._fs = MagicMock()
        return storage

    def test_native_signing_s3(self, storage):
        """Use native signing for S3 when available."""
        storage._fs.sign.return_value = "https://s3.amazonaws.com/bucket/files/id/file.pdf?signed=1"

        url = storage.get_download_url("file-123", "file.pdf", expires_in=3600)

        assert "signed=1" in url
        storage._fs.sign.assert_called_once()

    def test_fallback_to_token_when_sign_fails(self, storage):
        """Fall back to token URL when native signing fails."""
        storage.public_base_url = "https://files.example.com"
        storage._fs.sign.side_effect = Exception("Signing not supported")

        url = storage.get_download_url("file-123", "report.pdf", expires_in=3600)

        assert url.startswith("https://files.example.com/download/")
        assert "report.pdf" in url

    def test_no_public_base_url_raises_error(self, storage):
        """Missing public_base_url for local storage raises error."""
        # Remove sign method to simulate local filesystem
        del storage._fs.sign

        with pytest.raises(StorageNotConfiguredError, match="public_base_url is required"):
            storage.get_download_url("file-123", "file.pdf")

    def test_token_url_format(self, storage):
        """Token URL has correct format."""
        del storage._fs.sign
        storage.public_base_url = "https://files.example.com/"  # With trailing slash

        url = storage.get_download_url("file-123", "report.pdf", expires_in=3600)

        # Should strip trailing slash and build correct URL
        assert url.startswith("https://files.example.com/download/")
        assert url.endswith("/report.pdf")
        # Token should be in the middle
        token = url.split("/download/")[1].rsplit("/", 1)[0]
        assert token.startswith("file-123-")


class TestLargeFileStorageSignedToken:
    """Tests for token generation and verification."""

    @pytest.fixture
    def storage(self):
        """Create storage with known secret."""
        return LargeFileStorage(
            storage_url="file:///data",
            secret_key="test-secret-key",
        )

    def test_generate_signed_token(self, storage):
        """Token is generated with correct format."""
        token = storage._generate_signed_token("file-123", "doc.pdf", expires_in=3600)

        parts = token.rsplit("-", 2)
        assert len(parts) == 3
        assert parts[0] == "file-123"
        # Expiration should be roughly current time + 3600
        expires_at = int(parts[1])
        assert expires_at > time.time()
        assert expires_at < time.time() + 3700
        # Signature should be 16 chars
        assert len(parts[2]) == 16

    def test_verify_valid_token(self, storage):
        """Valid token is verified successfully."""
        token = storage._generate_signed_token("file-abc", "test.txt", expires_in=3600)

        result = storage.verify_download_token(token, "test.txt")

        assert result == "file-abc"

    def test_verify_expired_token(self, storage):
        """Expired token returns None."""
        # Create token that's already expired
        token = storage._generate_signed_token("file-old", "old.txt", expires_in=-10)

        result = storage.verify_download_token(token, "old.txt")

        assert result is None

    def test_verify_invalid_signature(self, storage):
        """Token with wrong signature returns None."""
        token = "file-123-9999999999-wrongsignature"

        result = storage.verify_download_token(token, "file.pdf")

        assert result is None

    def test_verify_malformed_token(self, storage):
        """Malformed token returns None."""
        # Not enough parts
        result = storage.verify_download_token("invalid", "file.pdf")
        assert result is None

        # Invalid expiration
        result = storage.verify_download_token("id-notanumber-sig", "file.pdf")
        assert result is None

    def test_verify_wrong_filename(self, storage):
        """Token verified with wrong filename fails."""
        token = storage._generate_signed_token("file-xyz", "original.pdf", expires_in=3600)

        # Try to verify with different filename
        result = storage.verify_download_token(token, "different.pdf")

        assert result is None


class TestLargeFileStorageGetFileContent:
    """Tests for get_file_content method."""

    @pytest.fixture
    def storage(self):
        """Create storage with mocked fs."""
        storage = LargeFileStorage(storage_url="s3://bucket/files")
        storage._base_path = "files"
        storage._fs = MagicMock()
        return storage

    def test_get_file_content_success(self, storage):
        """File content is returned."""
        mock_file = MagicMock()
        mock_file.read.return_value = b"file content here"
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        storage._fs.open.return_value = mock_file

        result = storage.get_file_content("file-123", "doc.pdf")

        assert result == b"file content here"
        storage._fs.open.assert_called_once_with("files/file-123/doc.pdf", "rb")

    def test_get_file_content_not_found(self, storage):
        """Non-existent file returns None."""
        storage._fs.open.side_effect = FileNotFoundError("Not found")

        result = storage.get_file_content("nonexistent", "missing.pdf")

        assert result is None


class TestLargeFileStorageExists:
    """Tests for exists method."""

    @pytest.fixture
    def storage(self):
        """Create storage with mocked fs."""
        storage = LargeFileStorage(storage_url="s3://bucket/files")
        storage._base_path = "files"
        storage._fs = MagicMock()
        return storage

    def test_exists_true(self, storage):
        """Returns True when file exists."""
        storage._fs.exists.return_value = True

        result = storage.exists("file-123", "doc.pdf")

        assert result is True
        storage._fs.exists.assert_called_once_with("files/file-123/doc.pdf")

    def test_exists_false(self, storage):
        """Returns False when file doesn't exist."""
        storage._fs.exists.return_value = False

        result = storage.exists("missing", "file.pdf")

        assert result is False


class TestLargeFileStorageCleanup:
    """Tests for cleanup_expired method."""

    @pytest.fixture
    def storage(self):
        """Create storage with mocked fs."""
        storage = LargeFileStorage(storage_url="s3://bucket/files")
        storage._base_path = "files"
        storage._fs = MagicMock()
        return storage

    async def test_cleanup_no_base_path(self, storage):
        """Cleanup returns 0 when base path doesn't exist."""
        storage._fs.exists.return_value = False

        result = await storage.cleanup_expired(ttl_days=30)

        assert result == 0

    async def test_cleanup_deletes_old_directories(self, storage):
        """Cleanup deletes directories older than TTL."""
        storage._fs.exists.return_value = True

        # Old directory (90 days ago)
        old_time = time.time() - (90 * 86400)
        # Recent directory (5 days ago)
        recent_time = time.time() - (5 * 86400)

        storage._fs.ls.return_value = [
            {"type": "directory", "mtime": old_time, "name": "files/old-file"},
            {"type": "directory", "mtime": recent_time, "name": "files/recent-file"},
            {"type": "file", "mtime": old_time, "name": "files/somefile.txt"},  # Not a dir
        ]

        result = await storage.cleanup_expired(ttl_days=30)

        # Should delete only the old directory
        assert result == 1
        storage._fs.rm.assert_called_once_with("files/old-file", recursive=True)

    async def test_cleanup_handles_datetime_mtime(self, storage):
        """Cleanup handles datetime mtime values."""
        from datetime import datetime, timezone

        storage._fs.exists.return_value = True

        old_dt = datetime.fromtimestamp(time.time() - (60 * 86400), tz=timezone.utc)

        storage._fs.ls.return_value = [
            {"type": "directory", "LastModified": old_dt, "Key": "files/old-item"},
        ]

        result = await storage.cleanup_expired(ttl_days=30)

        assert result == 1

    async def test_cleanup_error_handling(self, storage):
        """Cleanup handles errors gracefully."""
        storage._fs.exists.return_value = True
        storage._fs.ls.side_effect = Exception("Connection error")

        result = await storage.cleanup_expired(ttl_days=30)

        assert result == 0
