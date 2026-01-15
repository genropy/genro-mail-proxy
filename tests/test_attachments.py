"""Tests for the attachments module."""

import base64
import pytest

from async_mail_service.attachments import AttachmentManager, is_storage_available


@pytest.mark.asyncio
async def test_attachment_manager_without_storage():
    """Test AttachmentManager works without storage_manager."""
    mgr = AttachmentManager(None)
    # Without storage, fetch returns None
    data = await mgr.fetch({"filename": "a.txt", "storage_path": "vol:path/to/file"})
    assert data is None


@pytest.mark.asyncio
async def test_fetch_returns_none_for_missing_path():
    """Test that fetch returns None when storage_path is missing."""
    mgr = AttachmentManager(None)
    data = await mgr.fetch({"filename": "file.bin"})
    assert data is None


def test_guess_mime_known():
    """Test MIME type detection for known extensions."""
    maintype, subtype = AttachmentManager.guess_mime("report.pdf")
    assert maintype == "application"
    assert subtype == "pdf"


def test_guess_mime_html():
    """Test MIME type detection for HTML files."""
    maintype, subtype = AttachmentManager.guess_mime("page.html")
    assert maintype == "text"
    assert subtype == "html"


def test_guess_mime_image():
    """Test MIME type detection for images."""
    maintype, subtype = AttachmentManager.guess_mime("photo.jpg")
    assert maintype == "image"
    assert subtype == "jpeg"


def test_guess_mime_unknown():
    """Test MIME type detection for unknown extensions."""
    maintype, subtype = AttachmentManager.guess_mime("file.unknownext")
    assert maintype == "application"
    assert subtype == "octet-stream"


def test_is_storage_available():
    """Test the is_storage_available function."""
    # This test just verifies the function exists and returns a boolean
    result = is_storage_available()
    assert isinstance(result, bool)


@pytest.mark.asyncio
async def test_attachment_manager_with_mock_storage(monkeypatch):
    """Test AttachmentManager with a mock storage manager."""
    if not is_storage_available():
        pytest.skip("genro-storage not installed")

    from genro_storage import AsyncStorageManager

    expected_content = b"test file content"

    class MockNode:
        async def read(self, mode='r'):
            return expected_content

    class MockStorageManager:
        def configure(self, configs):
            pass

        def node(self, path):
            return MockNode()

    mock_storage = MockStorageManager()
    mgr = AttachmentManager(mock_storage)

    data = await mgr.fetch({"filename": "test.txt", "storage_path": "vol:test.txt"})
    assert data == expected_content
