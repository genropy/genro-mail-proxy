# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import time

import pytest

from tests import api_routes
from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    clear_mailhog,
    trigger_dispatch,
    wait_for_message_status,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestSecurityInputSanitization:
    """Test security measures and input sanitization.

    Verify that potentially malicious inputs are handled safely.
    """

    async def test_sql_injection_in_tenant_id(self, api_client):
        """SQL injection attempts in tenant_id should be handled safely."""
        # Try various SQL injection patterns
        injection_patterns = [
            "'; DROP TABLE messages; --",
            "1 OR 1=1",
            "test-tenant' OR '1'='1",
            "test; DELETE FROM tenants WHERE 1=1; --",
            "UNION SELECT * FROM accounts--",
        ]

        for pattern in injection_patterns:
            # These should either fail validation or be treated as literal strings
            resp = await api_client.get(api_routes.messages(tenant_id=pattern))
            # Should not cause server error (500)
            assert resp.status_code != 500, f"SQL injection caused server error: {pattern}"

    async def test_sql_injection_in_message_id(self, api_client, setup_test_tenants):
        """SQL injection in message IDs should be handled safely."""
        injection_ids = [
            "'; DROP TABLE messages; --",
            "msg-1' OR '1'='1",
            "1; DELETE FROM messages;--",
        ]

        # Try deleting with injection IDs
        resp = await api_client.post(
            api_routes.delete_messages(tenant_id="test-tenant-1"),
            json={"ids": injection_ids}
        )
        # Should not cause server error
        assert resp.status_code != 500, "SQL injection in message IDs caused server error"

    async def test_xss_in_message_subject(self, api_client, setup_test_tenants):
        """XSS attempts in message fields should be stored literally (not executed)."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        xss_subject = "<script>alert('XSS')</script>"
        xss_body = "<img src=x onerror=alert('XSS')>"

        message = {
            "id": f"xss-test-{ts}",
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": xss_subject,
            "body": xss_body,
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)
        sent = await wait_for_message_status(api_client, f"xss-test-{ts}", ("sent",))
        assert sent, f"message xss-test-{ts} never reached sent"

        # Verify the message was sent with literal content (not sanitized)
        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

        # The content should be stored as-is (email systems don't execute JS)
        msg = messages[0]
        subject = msg.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
        assert "<script>" in subject or "script" in subject.lower()

    async def test_path_traversal_in_attachment_path(self, api_client, setup_test_tenants):
        """Path traversal attempts should be handled safely."""
        ts = int(time.time())

        # Try path traversal in storage_path
        message = {
            "id": f"path-traversal-{ts}",
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Path Traversal Test",
            "body": "Testing path traversal.",
            "attachments": [{
                "filename": "../../etc/passwd",
                "storage_path": "../../../../etc/passwd",
                "fetch_mode": "endpoint",
            }],
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        # Should either reject or handle safely
        assert resp.status_code != 500, "Path traversal caused server error"

    async def test_oversized_payload_rejection(self, api_client, setup_test_tenants):
        """Extremely large payloads should be rejected."""
        ts = int(time.time())

        # Create a very large body (10MB of text)
        large_body = "A" * (10 * 1024 * 1024)

        message = {
            "id": f"oversized-{ts}",
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Oversized Payload Test",
            "body": large_body,
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        # Should either reject (413/422) or accept with warning
        # Server should not crash
        assert resp.status_code != 500, "Oversized payload caused server error"


# ============================================
# 19. UNICODE AND ENCODING
# ============================================
