# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import asyncio
import base64
import time

import pytest

from .helpers import (
    MAILHOG_TENANT1_API,
    clear_mailhog,
    get_msg_status,
    trigger_dispatch,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestAttachmentsBase64:
    """Test base64-encoded attachments."""

    async def test_base64_attachment(self, api_client, setup_test_tenants):
        """Send email with base64-encoded attachment."""
        await clear_mailhog(MAILHOG_TENANT1_API)
        await asyncio.sleep(0.5)

        ts = int(time.time())
        msg_id = f"base64-att-{ts}"
        content = "Hello, this is a test attachment content!"
        b64_content = base64.b64encode(content.encode()).decode()

        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Base64 Attachment Test",
            "body": "See attached file.",
            "attachments": [{
                "filename": "test.txt",
                "storage_path": f"base64:{b64_content}",
                "fetch_mode": "base64",
            }],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await trigger_dispatch(api_client)

        # Poll for message to be sent
        for _ in range(15):
            await asyncio.sleep(1)
            resp = await api_client.get("/messages?tenant_id=test-tenant-1")
            all_msgs = resp.json().get("messages", [])
            found = [m for m in all_msgs if m.get("id") == msg_id]
            if found and found[0].get("sent_ts"):
                break

        # Wait for message in MailHog
        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1, timeout=10)
        assert len(messages) >= 1, f"Expected at least 1 message, got {len(messages)}"

        # Verify attachment is present
        msg = messages[0]
        assert "MIME" in str(msg) or "multipart" in str(msg).lower() or len(msg.get("MIME", {}).get("Parts", [])) > 0


# ============================================
# 8. PRIORITY HANDLING
# ============================================


class TestHttpAttachmentFetch:
    """Test fetching attachments from HTTP URLs."""

    async def test_fetch_attachment_from_http_url(self, api_client, setup_test_tenants):
        """Can fetch attachment from HTTP URL."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())

        message = {
            "id": f"http-fetch-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "HTTP Attachment Fetch Test",
            "body": "Testing HTTP URL attachment fetch.",
            "attachments": [{
                "filename": "small.txt",
                "storage_path": "http://attachment-server:8080/small.txt",
                "fetch_mode": "http_url",
            }],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(5)

        # Verify message was sent
        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

        # Check message status
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == f"http-fetch-{ts}"]

        if found:
            assert get_msg_status(found[0]) == "sent"

    async def test_fetch_multiple_http_attachments(self, api_client, setup_test_tenants):
        """Can fetch multiple attachments from HTTP URLs."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())

        message = {
            "id": f"multi-http-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Multiple HTTP Attachments Test",
            "body": "Testing multiple HTTP URL attachments.",
            "attachments": [
                {
                    "filename": "small.txt",
                    "storage_path": "http://attachment-server:8080/small.txt",
                    "fetch_mode": "http_url",
                },
                {
                    "filename": "document.html",
                    "storage_path": "http://attachment-server:8080/document.html",
                    "fetch_mode": "http_url",
                },
            ],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(5)

        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

    async def test_http_attachment_timeout(self, api_client, setup_test_tenants):
        """Attachment fetch timeout should be handled gracefully."""
        ts = int(time.time())

        # Use a non-existent URL that will timeout or fail
        message = {
            "id": f"http-timeout-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "HTTP Timeout Test",
            "body": "Testing HTTP fetch timeout.",
            "attachments": [{
                "filename": "nonexistent.txt",
                "storage_path": "http://attachment-server:8080/nonexistent-file-12345.txt",
                "fetch_mode": "http_url",
            }],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(5)

        # Message should fail gracefully (not crash the server)
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == f"http-timeout-{ts}"]

        if found:
            # Should be error or deferred, not sent
            assert get_msg_status(found[0]) in ("error", "deferred")

    async def test_http_attachment_invalid_url(self, api_client, setup_test_tenants):
        """Invalid HTTP URLs should be handled gracefully."""
        ts = int(time.time())

        message = {
            "id": f"invalid-url-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Invalid URL Test",
            "body": "Testing invalid URL handling.",
            "attachments": [{
                "filename": "test.txt",
                "storage_path": "not-a-valid-url",
                "fetch_mode": "http_url",
            }],
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        # Should either reject immediately or fail during processing
        # Server should not crash
        assert resp.status_code != 500


# ============================================
# 21. BOUNCE DETECTION AND TRACKING
# ============================================
