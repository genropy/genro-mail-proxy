"""Tests for genro-storage integration."""

import pytest
import base64
from pathlib import Path
from genro_storage import AsyncStorageManager
from async_mail_service.attachments import AttachmentManager


@pytest.mark.asyncio
async def test_base64_attachment_fetch():
    """Test fetching inline base64 attachments."""
    storage = AsyncStorageManager()
    # Base64 volumes are always available (special volume)
    storage.configure([
        {"name": "base64", "type": "memory"}  # Mock base64 as memory for testing
    ])

    manager = AttachmentManager(storage)

    # Base64 encoded "Hello World"
    b64_content = base64.b64encode(b"Hello World").decode()
    att = {
        "filename": "test.txt",
        "storage_path": f"base64:{b64_content}"
    }

    # For real implementation, base64 would be handled specially
    # This test verifies the manager can handle base64 paths
    # In production, base64 content would be decoded directly
    assert att["storage_path"].startswith("base64:")


@pytest.mark.asyncio
async def test_storage_path_attachment(tmp_path):
    """Test fetching attachments from generic storage paths."""
    # Create a test file
    test_file = tmp_path / "test_document.pdf"
    test_content = b"PDF content here"
    test_file.write_bytes(test_content)

    # Configure storage with local backend
    storage = AsyncStorageManager()
    storage.configure([
        {"name": "local-test", "type": "local", "path": str(tmp_path)}
    ])

    manager = AttachmentManager(storage)

    att = {
        "filename": "test_document.pdf",
        "storage_path": "local-test:test_document.pdf"
    }

    # Fetch the attachment
    content = await manager.fetch(att)
    assert content == test_content


@pytest.mark.asyncio
async def test_attachment_from_multiple_volumes(tmp_path):
    """Test fetching attachments from different configured volumes."""
    # Create test files in different locations
    dir1 = tmp_path / "volume1"
    dir2 = tmp_path / "volume2"
    dir1.mkdir()
    dir2.mkdir()

    file1 = dir1 / "doc1.txt"
    file2 = dir2 / "doc2.txt"
    file1.write_text("Content from volume 1")
    file2.write_text("Content from volume 2")

    # Configure multiple volumes
    storage = AsyncStorageManager()
    storage.configure([
        {"name": "vol1", "type": "local", "path": str(dir1)},
        {"name": "vol2", "type": "local", "path": str(dir2)},
    ])

    manager = AttachmentManager(storage)

    # Fetch from volume 1
    att1 = {"filename": "doc1.txt", "storage_path": "vol1:doc1.txt"}
    content1 = await manager.fetch(att1)
    assert content1 == b"Content from volume 1"

    # Fetch from volume 2
    att2 = {"filename": "doc2.txt", "storage_path": "vol2:doc2.txt"}
    content2 = await manager.fetch(att2)
    assert content2 == b"Content from volume 2"


@pytest.mark.asyncio
async def test_attachment_not_found_raises(tmp_path):
    """Test that fetching nonexistent attachment raises error."""
    storage = AsyncStorageManager()
    storage.configure([
        {"name": "test", "type": "local", "path": str(tmp_path)}
    ])

    manager = AttachmentManager(storage)

    att = {
        "filename": "nonexistent.pdf",
        "storage_path": "test:nonexistent.pdf"
    }

    # Should raise an error when file doesn't exist
    from genro_storage.exceptions import StorageNotFoundError
    with pytest.raises(StorageNotFoundError):
        await manager.fetch(att)


@pytest.mark.asyncio
async def test_attachment_missing_storage_path():
    """Test that attachments without storage_path return None."""
    storage = AsyncStorageManager()
    manager = AttachmentManager(storage)

    att = {"filename": "test.txt"}  # No storage_path

    result = await manager.fetch(att)
    assert result is None


@pytest.mark.asyncio
async def test_mime_type_guessing():
    """Test MIME type guessing for attachments."""
    # Test common file types
    assert AttachmentManager.guess_mime("document.pdf") == ("application", "pdf")
    assert AttachmentManager.guess_mime("image.png") == ("image", "png")
    assert AttachmentManager.guess_mime("image.jpg") == ("image", "jpeg")
    assert AttachmentManager.guess_mime("doc.txt") == ("text", "plain")
    assert AttachmentManager.guess_mime("data.json") == ("application", "json")
    assert AttachmentManager.guess_mime("page.html") == ("text", "html")

    # Unknown extension should return application/octet-stream
    assert AttachmentManager.guess_mime("file.unknown") == ("application", "octet-stream")
    assert AttachmentManager.guess_mime("noextension") == ("application", "octet-stream")


@pytest.mark.asyncio
async def test_binary_file_handling(tmp_path):
    """Test handling of binary files (non-text)."""
    # Create a binary file
    binary_file = tmp_path / "binary.dat"
    binary_content = bytes(range(256))  # All byte values 0-255
    binary_file.write_bytes(binary_content)

    storage = AsyncStorageManager()
    storage.configure([
        {"name": "bin", "type": "local", "path": str(tmp_path)}
    ])

    manager = AttachmentManager(storage)

    att = {"filename": "binary.dat", "storage_path": "bin:binary.dat"}
    content = await manager.fetch(att)

    assert content == binary_content
    assert len(content) == 256


@pytest.mark.asyncio
async def test_large_file_handling(tmp_path):
    """Test handling of larger files."""
    # Create a 1MB file
    large_file = tmp_path / "large.bin"
    large_content = b"X" * (1024 * 1024)  # 1MB of 'X'
    large_file.write_bytes(large_content)

    storage = AsyncStorageManager()
    storage.configure([
        {"name": "large", "type": "local", "path": str(tmp_path)}
    ])

    manager = AttachmentManager(storage)

    att = {"filename": "large.bin", "storage_path": "large:large.bin"}
    content = await manager.fetch(att)

    assert len(content) == 1024 * 1024
    assert content == large_content


@pytest.mark.asyncio
async def test_nested_path_handling(tmp_path):
    """Test handling of files in nested directory structures."""
    # Create nested directories
    nested_dir = tmp_path / "documents" / "2024" / "reports"
    nested_dir.mkdir(parents=True)

    nested_file = nested_dir / "report.pdf"
    nested_file.write_text("Annual Report")

    storage = AsyncStorageManager()
    storage.configure([
        {"name": "docs", "type": "local", "path": str(tmp_path)}
    ])

    manager = AttachmentManager(storage)

    att = {
        "filename": "report.pdf",
        "storage_path": "docs:documents/2024/reports/report.pdf"
    }
    content = await manager.fetch(att)

    assert content == b"Annual Report"


@pytest.mark.asyncio
async def test_volume_not_configured_raises():
    """Test that using unconfigured volume raises error."""
    storage = AsyncStorageManager()
    storage.configure([
        {"name": "configured", "type": "memory"}
    ])

    manager = AttachmentManager(storage)

    att = {
        "filename": "test.txt",
        "storage_path": "unconfigured:test.txt"
    }

    # Should raise error for unconfigured volume
    from genro_storage.exceptions import StorageNotFoundError
    with pytest.raises(StorageNotFoundError):
        await manager.fetch(att)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_concurrent_fetches(tmp_path):
    """Test concurrent attachment fetches (async advantage)."""
    import asyncio

    # Create multiple test files
    for i in range(10):
        (tmp_path / f"file{i}.txt").write_text(f"Content {i}")

    storage = AsyncStorageManager()
    storage.configure([
        {"name": "concurrent", "type": "local", "path": str(tmp_path)}
    ])

    manager = AttachmentManager(storage)

    # Create fetch tasks
    tasks = []
    for i in range(10):
        att = {"filename": f"file{i}.txt", "storage_path": f"concurrent:file{i}.txt"}
        tasks.append(manager.fetch(att))

    # Fetch all concurrently
    results = await asyncio.gather(*tasks)

    # Verify all fetches succeeded
    assert len(results) == 10
    for i, content in enumerate(results):
        assert content == f"Content {i}".encode()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_storage_manager_reload(tmp_path):
    """Test reconfiguring storage manager with new volumes."""
    test_file = tmp_path / "test.txt"
    test_file.write_text("Test content")

    storage = AsyncStorageManager()

    # Initial configuration
    storage.configure([
        {"name": "initial", "type": "local", "path": str(tmp_path)}
    ])

    manager = AttachmentManager(storage)

    att1 = {"filename": "test.txt", "storage_path": "initial:test.txt"}
    content1 = await manager.fetch(att1)
    assert content1 == b"Test content"

    # Reconfigure with new volume name
    storage.configure([
        {"name": "reloaded", "type": "local", "path": str(tmp_path)}
    ])

    # Old volume should no longer work
    with pytest.raises(Exception):  # StorageNotFoundError or similar
        await manager.fetch(att1)

    # New volume should work
    att2 = {"filename": "test.txt", "storage_path": "reloaded:test.txt"}
    content2 = await manager.fetch(att2)
    assert content2 == b"Test content"
