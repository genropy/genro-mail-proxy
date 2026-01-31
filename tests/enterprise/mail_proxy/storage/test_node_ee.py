# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for StorageNode_EE cloud storage support."""

from unittest.mock import MagicMock, patch

import pytest

from enterprise.mail_proxy.storage.node_ee import StorageNode_EE


class MockStorageNode(StorageNode_EE):
    """Mock storage node with EE mixin for testing."""

    def __init__(self, mount_name: str, path: str, config: dict):
        self._mount_name = mount_name
        self._path = path
        self._config = config

    def child(self, name: str) -> "MockStorageNode":
        """Create child node."""
        new_path = f"{self._path}/{name}" if self._path else name
        return MockStorageNode(self._mount_name, new_path, self._config)


class TestStorageNodeEEGetFS:
    """Tests for _get_fs method."""

    def test_get_fs_s3(self):
        """Get S3 filesystem."""
        node = MockStorageNode(
            mount_name="s3-test",
            path="data/file.txt",
            config={
                "protocol": "s3",
                "aws_access_key_id": "AKIATEST",
                "aws_secret_access_key": "secret",
                "endpoint_url": "http://localhost:9000",
            },
        )
        StorageNode_EE._fs_cache.clear()

        mock_fs = MagicMock()
        mock_fsspec = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        with patch.dict("sys.modules", {"fsspec": mock_fsspec}):
            fs = node._get_fs()

            mock_fsspec.filesystem.assert_called_once_with(
                "s3",
                key="AKIATEST",
                secret="secret",
                endpoint_url="http://localhost:9000",
                client_kwargs={},
            )
            assert fs is mock_fs

    def test_get_fs_gcs(self):
        """Get GCS filesystem."""
        node = MockStorageNode(
            mount_name="gcs-test",
            path="data/file.txt",
            config={
                "protocol": "gcs",
                "project": "my-project",
                "token": "/path/to/token.json",
            },
        )
        StorageNode_EE._fs_cache.clear()

        mock_fs = MagicMock()
        mock_fsspec = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        with patch.dict("sys.modules", {"fsspec": mock_fsspec}):
            node._get_fs()

            mock_fsspec.filesystem.assert_called_once_with(
                "gcs",
                project="my-project",
                token="/path/to/token.json",
            )

    def test_get_fs_azure(self):
        """Get Azure filesystem."""
        node = MockStorageNode(
            mount_name="azure-test",
            path="data/file.txt",
            config={
                "protocol": "azure",
                "account_name": "myaccount",
                "account_key": "secret-key",
            },
        )
        StorageNode_EE._fs_cache.clear()

        mock_fs = MagicMock()
        mock_fsspec = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        with patch.dict("sys.modules", {"fsspec": mock_fsspec}):
            node._get_fs()

            mock_fsspec.filesystem.assert_called_once_with(
                "az",
                account_name="myaccount",
                account_key="secret-key",
                connection_string=None,
            )

    def test_get_fs_unsupported_protocol(self):
        """Unsupported protocol raises ValueError."""
        node = MockStorageNode(
            mount_name="unknown",
            path="data/file.txt",
            config={"protocol": "ftp"},
        )
        StorageNode_EE._fs_cache.clear()

        mock_fsspec = MagicMock()
        with patch.dict("sys.modules", {"fsspec": mock_fsspec}):
            with pytest.raises(ValueError, match="Unsupported cloud protocol"):
                node._get_fs()

    def test_get_fs_caches_filesystem(self):
        """Filesystem is cached per mount."""
        node = MockStorageNode(
            mount_name="cached-test",
            path="data/file.txt",
            config={"protocol": "s3"},
        )
        StorageNode_EE._fs_cache.clear()

        mock_fs = MagicMock()
        mock_fsspec = MagicMock()
        mock_fsspec.filesystem.return_value = mock_fs

        with patch.dict("sys.modules", {"fsspec": mock_fsspec}):
            fs1 = node._get_fs()
            fs2 = node._get_fs()

            # Should only create filesystem once
            assert mock_fsspec.filesystem.call_count == 1
            assert fs1 is fs2

    def test_get_fs_missing_fsspec(self):
        """Missing fsspec raises ImportError."""
        node = MockStorageNode(
            mount_name="no-fsspec",
            path="data/file.txt",
            config={"protocol": "s3"},
        )
        StorageNode_EE._fs_cache.clear()

        # Remove fsspec from cache and simulate import error
        with patch.dict("sys.modules", {"fsspec": None}):
            with pytest.raises((ImportError, TypeError)):
                node._get_fs()


class TestStorageNodeEECloudPath:
    """Tests for _get_cloud_path method."""

    def test_s3_path_with_bucket_and_prefix(self):
        """S3 path with bucket and prefix."""
        node = MockStorageNode(
            mount_name="s3-test",
            path="file.txt",
            config={
                "protocol": "s3",
                "bucket": "my-bucket",
                "prefix": "attachments",
            },
        )

        path = node._get_cloud_path()

        assert path == "my-bucket/attachments/file.txt"

    def test_s3_path_without_prefix(self):
        """S3 path without prefix."""
        node = MockStorageNode(
            mount_name="s3-test",
            path="data/file.txt",
            config={
                "protocol": "s3",
                "bucket": "my-bucket",
            },
        )

        path = node._get_cloud_path()

        assert path == "my-bucket/data/file.txt"

    def test_gcs_path(self):
        """GCS path construction."""
        node = MockStorageNode(
            mount_name="gcs-test",
            path="uploads/doc.pdf",
            config={
                "protocol": "gcs",
                "bucket": "gcs-bucket",
                "prefix": "mail",
            },
        )

        path = node._get_cloud_path()

        assert path == "gcs-bucket/mail/uploads/doc.pdf"

    def test_azure_path(self):
        """Azure path construction."""
        node = MockStorageNode(
            mount_name="azure-test",
            path="file.txt",
            config={
                "protocol": "azure",
                "container": "my-container",
                "prefix": "data/",  # Should strip trailing slash
            },
        )

        path = node._get_cloud_path()

        assert path == "my-container/data/file.txt"


class TestStorageNodeEECloudOperations:
    """Tests for cloud I/O operations."""

    @pytest.fixture
    def node(self):
        """Create node with mocked filesystem."""
        node = MockStorageNode(
            mount_name="test-fs",
            path="test/file.txt",
            config={"protocol": "s3", "bucket": "bucket"},
        )
        StorageNode_EE._fs_cache.clear()
        return node

    @pytest.fixture
    def mock_fs(self):
        """Create mock filesystem."""
        return MagicMock()

    async def test_cloud_exists(self, node, mock_fs):
        """Test _cloud_exists."""
        mock_fs.exists.return_value = True

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_exists()

        assert result is True
        mock_fs.exists.assert_called_once()

    async def test_cloud_is_file(self, node, mock_fs):
        """Test _cloud_is_file."""
        mock_fs.isfile.return_value = True

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_is_file()

        assert result is True

    async def test_cloud_is_dir(self, node, mock_fs):
        """Test _cloud_is_dir."""
        mock_fs.isdir.return_value = True

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_is_dir()

        assert result is True

    async def test_cloud_size(self, node, mock_fs):
        """Test _cloud_size."""
        mock_fs.size.return_value = 12345

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_size()

        assert result == 12345

    async def test_cloud_size_none(self, node, mock_fs):
        """Test _cloud_size when size is None."""
        mock_fs.size.return_value = None

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_size()

        assert result == 0

    async def test_cloud_mtime(self, node, mock_fs):
        """Test _cloud_mtime."""
        mock_fs.info.return_value = {"mtime": 1706000000.0}

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_mtime()

        assert result == 1706000000.0

    async def test_cloud_mtime_with_datetime(self, node, mock_fs):
        """Test _cloud_mtime with datetime object."""
        from datetime import datetime

        mock_dt = MagicMock()
        mock_dt.timestamp.return_value = 1706000000.0
        mock_fs.info.return_value = {"LastModified": mock_dt}

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_mtime()

        assert result == 1706000000.0

    async def test_cloud_read_bytes(self, node, mock_fs):
        """Test _cloud_read_bytes."""
        mock_file = MagicMock()
        mock_file.read.return_value = b"file content"
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_fs.open.return_value = mock_file

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_read_bytes()

        assert result == b"file content"

    async def test_cloud_write_bytes(self, node, mock_fs):
        """Test _cloud_write_bytes."""
        mock_file = MagicMock()
        mock_file.__enter__ = MagicMock(return_value=mock_file)
        mock_file.__exit__ = MagicMock(return_value=False)
        mock_fs.open.return_value = mock_file

        with patch.object(node, "_get_fs", return_value=mock_fs):
            await node._cloud_write_bytes(b"new content")

        mock_fs.makedirs.assert_called_once()
        mock_file.write.assert_called_once_with(b"new content")

    async def test_cloud_delete_file(self, node, mock_fs):
        """Test _cloud_delete for file."""
        mock_fs.exists.return_value = True
        mock_fs.isdir.return_value = False

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_delete()

        assert result is True
        mock_fs.rm.assert_called_once()

    async def test_cloud_delete_dir(self, node, mock_fs):
        """Test _cloud_delete for directory."""
        mock_fs.exists.return_value = True
        mock_fs.isdir.return_value = True

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_delete()

        assert result is True
        mock_fs.rm.assert_called_once_with(node._get_cloud_path(), recursive=True)

    async def test_cloud_delete_not_exists(self, node, mock_fs):
        """Test _cloud_delete when path doesn't exist."""
        mock_fs.exists.return_value = False

        with patch.object(node, "_get_fs", return_value=mock_fs):
            result = await node._cloud_delete()

        assert result is False

    async def test_cloud_mkdir(self, node, mock_fs):
        """Test _cloud_mkdir."""
        with patch.object(node, "_get_fs", return_value=mock_fs):
            await node._cloud_mkdir(parents=True, exist_ok=True)

        mock_fs.makedirs.assert_called_once()

    async def test_cloud_children(self, node, mock_fs):
        """Test _cloud_children."""
        mock_fs.isdir.return_value = True
        mock_fs.ls.return_value = [
            "bucket/test/file.txt/child1.txt",
            "bucket/test/file.txt/child2.txt",
        ]

        with patch.object(node, "_get_fs", return_value=mock_fs):
            children = await node._cloud_children()

        assert len(children) == 2

    async def test_cloud_children_not_dir(self, node, mock_fs):
        """Test _cloud_children when path is not directory."""
        mock_fs.isdir.return_value = False

        with patch.object(node, "_get_fs", return_value=mock_fs):
            children = await node._cloud_children()

        assert children == []


class TestStorageNodeEECloudURL:
    """Tests for presigned URL generation."""

    def test_cloud_url_with_sign(self):
        """Generate presigned URL with sign method."""
        node = MockStorageNode(
            mount_name="s3-test",
            path="file.txt",
            config={"protocol": "s3", "bucket": "bucket"},
        )

        mock_fs = MagicMock()
        mock_fs.sign.return_value = "https://bucket.s3.amazonaws.com/file.txt?signed=1"

        with patch.object(node, "_get_fs", return_value=mock_fs):
            url = node._cloud_url(expires_in=3600)

        assert "signed" in url
        mock_fs.sign.assert_called_once()

    def test_cloud_url_without_sign(self):
        """Presigned URL not supported raises error."""
        node = MockStorageNode(
            mount_name="local-test",
            path="file.txt",
            config={"protocol": "local"},
        )

        mock_fs = MagicMock(spec=[])  # No sign method

        with patch.object(node, "_get_fs", return_value=mock_fs):
            with pytest.raises(NotImplementedError, match="does not support presigned"):
                node._cloud_url(expires_in=3600)
