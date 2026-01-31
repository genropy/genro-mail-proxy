# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Pytest configuration for fullstack tests.

These tests require Docker with Mailpit, Minio, and Proxy running:
    cd tests/fullstack && docker compose up -d

Tests are skipped if Docker services are not available.
"""

from __future__ import annotations

import socket
from pathlib import Path

import httpx
import pytest

from .imap_injector import IMAPBounceInjector

# Mailpit default ports
MAILPIT_SMTP_PORT = 1025
MAILPIT_IMAP_PORT = 1143
MAILPIT_API_PORT = 8025
MAILPIT_HOST = "localhost"

# Minio (S3-compatible storage)
MINIO_HOST = "localhost"
MINIO_API_PORT = 9000
MINIO_CONSOLE_PORT = 9001
MINIO_ACCESS_KEY = "minioadmin"
MINIO_SECRET_KEY = "minioadmin"
MINIO_TEST_BUCKET = "test-attachments"

# Proxy API
PROXY_HOST = "localhost"
PROXY_PORT = 8000

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _check_service(host: str, port: int) -> bool:
    """Check if a TCP service is available."""
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except (OSError, socket.timeout):
        return False


# Check availability at module load time (once)
_MAILPIT_AVAILABLE = _check_service(MAILPIT_HOST, MAILPIT_SMTP_PORT)
_MINIO_AVAILABLE = _check_service(MINIO_HOST, MINIO_API_PORT)
_PROXY_AVAILABLE = _check_service(PROXY_HOST, PROXY_PORT)


def is_infrastructure_ready() -> bool:
    """Check if Mailpit and Proxy are running (minimum for SMTP tests)."""
    return _MAILPIT_AVAILABLE and _PROXY_AVAILABLE


def is_minio_ready() -> bool:
    """Check if Minio is available for storage tests."""
    return _MINIO_AVAILABLE


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    """Skip fullstack tests if Docker services are not available."""
    if not is_infrastructure_ready():
        missing = []
        if not _MAILPIT_AVAILABLE:
            missing.append("Mailpit")
        if not _PROXY_AVAILABLE:
            missing.append("Proxy")

        skip_marker = pytest.mark.skip(
            reason=f"Docker services not available ({', '.join(missing)}). "
            "Run: cd tests/fullstack && docker compose up -d"
        )
        for item in items:
            if "fullstack" in str(item.fspath):
                item.add_marker(skip_marker)


@pytest.fixture
def imap_injector() -> IMAPBounceInjector:
    """Create IMAP injector for bounce simulation."""
    return IMAPBounceInjector(
        host=MAILPIT_HOST,
        port=MAILPIT_IMAP_PORT,
    )


@pytest.fixture
def mailpit_api() -> "MailpitAPI":
    """Create Mailpit API client for verification."""
    return MailpitAPI(f"http://{MAILPIT_HOST}:{MAILPIT_API_PORT}")


class MailpitAPI:
    """Simple client for Mailpit REST API."""

    def __init__(self, base_url: str):
        self.base_url = base_url

    async def get_messages(self, limit: int = 50) -> list[dict]:
        """Get all messages from Mailpit."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/api/v1/messages", params={"limit": limit})
            resp.raise_for_status()
            data = resp.json()
            return data.get("messages", [])

    async def get_message(self, message_id: str) -> dict:
        """Get a specific message by ID."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{self.base_url}/api/v1/message/{message_id}")
            resp.raise_for_status()
            return resp.json()

    async def delete_all(self) -> None:
        """Delete all messages from Mailpit."""
        async with httpx.AsyncClient() as client:
            await client.delete(f"{self.base_url}/api/v1/messages")

    async def find_by_subject(self, subject: str) -> dict | None:
        """Find a message by subject."""
        messages = await self.get_messages()
        for msg in messages:
            if msg.get("Subject") == subject:
                return msg
        return None

    async def count_messages(self) -> int:
        """Count total messages."""
        messages = await self.get_messages()
        return len(messages)


@pytest.fixture
def minio_config() -> dict:
    """Minio S3 configuration for storage tests."""
    return {
        "protocol": "s3",
        "bucket": MINIO_TEST_BUCKET,
        "endpoint_url": f"http://{MINIO_HOST}:{MINIO_API_PORT}",
        "aws_access_key_id": MINIO_ACCESS_KEY,
        "aws_secret_access_key": MINIO_SECRET_KEY,
    }


@pytest.fixture
def minio_available() -> bool:
    """Check if Minio is available."""
    return is_minio_ready()
