# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for the attachments module."""

import base64
import pytest

from async_mail_service.attachments import AttachmentManager


@pytest.mark.asyncio
async def test_fetch_returns_none_for_missing_path():
    """Test that fetch returns None when storage_path is missing."""
    mgr = AttachmentManager()
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
