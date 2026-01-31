# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for StorageNode."""

import pytest

from storage import StorageManager, StorageNode


class TestStorageNodeProperties:
    """Tests for StorageNode non-I/O properties."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create storage with local mount."""
        s = StorageManager()
        s.configure([
            {"name": "data", "protocol": "local", "base_path": str(tmp_path)}
        ])
        return s

    def test_basename(self, storage):
        """basename returns filename with extension."""
        node = storage.node("data:path/to/report.pdf")
        assert node.basename == "report.pdf"

    def test_stem(self, storage):
        """stem returns filename without extension."""
        node = storage.node("data:path/to/report.pdf")
        assert node.stem == "report"

    def test_suffix(self, storage):
        """suffix returns file extension."""
        node = storage.node("data:path/to/report.pdf")
        assert node.suffix == ".pdf"

    def test_path(self, storage):
        """path returns path within mount."""
        node = storage.node("data:files/report.pdf")
        assert node.path == "files/report.pdf"

    def test_fullpath(self, storage):
        """fullpath returns mount:path format."""
        node = storage.node("data:files/report.pdf")
        assert node.fullpath == "data:files/report.pdf"

    def test_mount_name(self, storage):
        """mount_name returns mount name."""
        node = storage.node("data:files/report.pdf")
        assert node.mount_name == "data"

    def test_mimetype_pdf(self, storage):
        """mimetype returns correct MIME type for PDF."""
        node = storage.node("data:report.pdf")
        assert node.mimetype == "application/pdf"

    def test_mimetype_jpg(self, storage):
        """mimetype returns correct MIME type for JPEG."""
        node = storage.node("data:photo.jpg")
        assert node.mimetype == "image/jpeg"

    def test_mimetype_unknown(self, storage):
        """mimetype returns octet-stream for unknown extension."""
        node = storage.node("data:file.xyz123")
        assert node.mimetype == "application/octet-stream"


class TestStorageNodeNavigation:
    """Tests for StorageNode navigation methods."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create storage with local mount."""
        s = StorageManager()
        s.configure([
            {"name": "data", "protocol": "local", "base_path": str(tmp_path)}
        ])
        return s

    def test_parent(self, storage):
        """parent returns parent directory node."""
        node = storage.node("data:files/reports/2024/q4.pdf")
        parent = node.parent

        assert parent.path == "files/reports/2024"
        assert parent.basename == "2024"

    def test_parent_of_root(self, storage):
        """parent of root returns empty path."""
        node = storage.node("data:file.txt")
        parent = node.parent

        assert parent.path == ""

    def test_child(self, storage):
        """child returns child node."""
        node = storage.node("data:files")
        child = node.child("subdir", "report.pdf")

        assert child.path == "files/subdir/report.pdf"

    def test_child_from_root(self, storage):
        """child from root works correctly."""
        node = storage.node("data:")
        child = node.child("files", "report.pdf")

        assert child.path == "files/report.pdf"


class TestStorageNodeLocalIO:
    """Tests for StorageNode I/O operations on local filesystem."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create storage with local mount."""
        s = StorageManager()
        s.configure([
            {"name": "data", "protocol": "local", "base_path": str(tmp_path)}
        ])
        return s

    async def test_write_and_read_bytes(self, storage):
        """Write and read bytes."""
        node = storage.node("data:test.bin")
        content = b"\x00\x01\x02\x03\xff"

        await node.write_bytes(content)
        result = await node.read_bytes()

        assert result == content

    async def test_write_and_read_text(self, storage):
        """Write and read text."""
        node = storage.node("data:test.txt")
        content = "Hello, World!"

        await node.write_text(content)
        result = await node.read_text()

        assert result == content

    async def test_write_creates_parent_dirs(self, storage):
        """Write creates parent directories automatically."""
        node = storage.node("data:deep/nested/path/file.txt")

        await node.write_text("content")

        assert await node.exists()
        assert await node.read_text() == "content"

    async def test_exists_true(self, storage):
        """exists returns True for existing file."""
        node = storage.node("data:exists.txt")
        await node.write_text("hello")

        assert await node.exists()

    async def test_exists_false(self, storage):
        """exists returns False for non-existing file."""
        node = storage.node("data:not-exists.txt")

        assert not await node.exists()

    async def test_is_file(self, storage):
        """is_file returns True for file."""
        node = storage.node("data:file.txt")
        await node.write_text("content")

        assert await node.is_file()
        assert not await node.is_dir()

    async def test_is_dir(self, storage):
        """is_dir returns True for directory."""
        node = storage.node("data:mydir")
        await node.mkdir()

        assert await node.is_dir()
        assert not await node.is_file()

    async def test_size(self, storage):
        """size returns file size in bytes."""
        node = storage.node("data:sized.txt")
        content = b"12345"
        await node.write_bytes(content)

        assert await node.size() == 5

    async def test_mtime(self, storage):
        """mtime returns modification time."""
        import time

        node = storage.node("data:timed.txt")
        before = time.time() - 1  # 1 second tolerance for clock skew
        await node.write_text("content")
        after = time.time() + 1  # 1 second tolerance for clock skew

        mtime = await node.mtime()

        assert before <= mtime <= after

    async def test_delete_file(self, storage):
        """delete removes file."""
        node = storage.node("data:to-delete.txt")
        await node.write_text("content")

        result = await node.delete()

        assert result is True
        assert not await node.exists()

    async def test_delete_directory(self, storage):
        """delete removes directory recursively."""
        parent = storage.node("data:dir-to-delete")
        child = parent.child("file.txt")
        await child.write_text("content")

        result = await parent.delete()

        assert result is True
        assert not await parent.exists()

    async def test_delete_not_found(self, storage):
        """delete returns False for non-existing."""
        node = storage.node("data:not-there.txt")

        result = await node.delete()

        assert result is False

    async def test_mkdir(self, storage):
        """mkdir creates directory."""
        node = storage.node("data:newdir")

        await node.mkdir()

        assert await node.exists()
        assert await node.is_dir()

    async def test_mkdir_parents(self, storage):
        """mkdir with parents creates nested directories."""
        node = storage.node("data:a/b/c/d")

        await node.mkdir(parents=True)

        assert await node.exists()
        assert await node.is_dir()

    async def test_children(self, storage):
        """children lists directory contents."""
        parent = storage.node("data:parent")
        await parent.mkdir()
        await parent.child("file1.txt").write_text("1")
        await parent.child("file2.txt").write_text("2")
        await parent.child("subdir").mkdir()

        children = await parent.children()

        names = [c.basename for c in children]
        assert "file1.txt" in names
        assert "file2.txt" in names
        assert "subdir" in names

    async def test_md5hash(self, storage):
        """md5hash returns correct hash."""
        node = storage.node("data:hash-test.txt")
        await node.write_bytes(b"test content")

        md5 = await node.md5hash()

        # MD5 of "test content"
        assert md5 == "9473fdd0d880a43c21b7778d34872157"


class TestStorageNodeURL:
    """Tests for StorageNode URL generation."""

    @pytest.fixture
    def storage(self, tmp_path):
        """Create storage with local mount and public URL."""
        s = StorageManager()
        s.configure([{
            "name": "data",
            "protocol": "local",
            "base_path": str(tmp_path),
            "public_base_url": "https://files.example.com",
            "secret_key": "test-secret-key",
        }])
        return s

    def test_url_generates_signed_url(self, storage):
        """url() generates signed URL for local storage."""
        node = storage.node("data:files/report.pdf")

        url = node.url(expires_in=3600)

        assert url.startswith("https://files.example.com/files/report.pdf")
        assert "token=" in url

    def test_url_without_public_base_url_raises(self, tmp_path):
        """url() without public_base_url raises error."""
        storage = StorageManager()
        storage.configure([{
            "name": "data",
            "protocol": "local",
            "base_path": str(tmp_path),
        }])

        node = storage.node("data:file.txt")

        with pytest.raises(Exception, match="public_base_url"):
            node.url()

    def test_verify_url_token_valid(self, storage):
        """verify_url_token returns True for valid token."""
        node = storage.node("data:files/report.pdf")
        url = node.url(expires_in=3600)

        # Extract token from URL
        token = url.split("token=")[1]

        assert node.verify_url_token(token) is True

    def test_verify_url_token_expired(self, storage):
        """verify_url_token returns False for expired token."""
        node = storage.node("data:files/report.pdf")
        url = node.url(expires_in=-1)  # Already expired

        token = url.split("token=")[1]

        assert node.verify_url_token(token) is False

    def test_verify_url_token_invalid(self, storage):
        """verify_url_token returns False for invalid token."""
        node = storage.node("data:files/report.pdf")

        assert node.verify_url_token("invalid-token") is False
        assert node.verify_url_token("") is False


# Check if EE is installed
try:
    from enterprise.mail_proxy.storage.node_ee import StorageNode_EE
    _HAS_EE = True
except ImportError:
    _HAS_EE = False


@pytest.mark.skipif(_HAS_EE, reason="Test only valid when EE is not installed")
class TestStorageNodeCloudProtocolsRaiseInCE:
    """Tests that cloud protocols raise NotImplementedError in CE.

    These tests are only valid when EE is NOT installed.
    When EE is installed, cloud protocols are supported via the mixin.
    """

    @pytest.fixture
    def storage(self):
        """Create storage with S3 mount (CE mode)."""
        s = StorageManager()
        s.configure([{
            "name": "s3data",
            "protocol": "s3",
            "bucket": "test-bucket",
        }])
        return s

    async def test_cloud_read_raises(self, storage):
        """Reading from cloud raises NotImplementedError in CE."""
        node = storage.node("s3data:file.txt")

        with pytest.raises(NotImplementedError, match="Enterprise Edition"):
            await node.read_bytes()

    async def test_cloud_write_raises(self, storage):
        """Writing to cloud raises NotImplementedError in CE."""
        node = storage.node("s3data:file.txt")

        with pytest.raises(NotImplementedError, match="Enterprise Edition"):
            await node.write_bytes(b"data")

    async def test_cloud_exists_raises(self, storage):
        """exists on cloud raises NotImplementedError in CE."""
        node = storage.node("s3data:file.txt")

        with pytest.raises(NotImplementedError, match="Enterprise Edition"):
            await node.exists()

    def test_cloud_url_raises(self, storage):
        """url on cloud raises NotImplementedError in CE."""
        node = storage.node("s3data:file.txt")

        with pytest.raises(NotImplementedError, match="Enterprise Edition"):
            node.url()
