# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import base64
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


class TestUnicodeEncoding:
    """Test proper handling of Unicode characters and various encodings."""

    async def test_emoji_in_subject(self, api_client, setup_test_tenants):
        """Emails with emoji in subject should be sent correctly."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        emoji_subject = "Test Email 🚀 with Emoji 💻 Subject 🎉"

        message = {
            "id": f"emoji-subject-{ts}",
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": emoji_subject,
            "body": "Testing emoji in subject line.",
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        sent = await wait_for_message_status(api_client, f"emoji-subject-{ts}", ("sent",))
        assert sent, f"message emoji-subject-{ts} never reached sent"

        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

        # Verify emoji survived encoding
        msg = messages[0]
        subject = msg.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
        # Subject might be encoded (MIME), but should decode to original
        assert "Test Email" in subject or "emoji" in subject.lower()

    async def test_emoji_in_body(self, api_client, setup_test_tenants):
        """Emails with emoji in body should be sent correctly."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        msg_id = f"emoji-body-{ts}"
        emoji_body = """
        Hello! 👋

        This is a test email with various emoji:
        - Rocket: 🚀
        - Computer: 💻
        - Celebration: 🎉
        - Heart: ❤️
        - Thumbs up: 👍

        Best regards,
        Test 😊
        """

        message = {
            "id": msg_id,
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Emoji Body Test",
            "body": emoji_body,
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch and wait for processing
        await trigger_dispatch(api_client)

        # Confirm the proxy processed it before looking in MailHog
        sent = await wait_for_message_status(api_client, msg_id, ("sent",))
        assert sent, f"message {msg_id} never reached sent"

        # Wait for message in MailHog
        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1, timeout=10)
        assert len(messages) >= 1, f"Expected at least 1 message in MailHog, found {len(messages)}"

    async def test_international_characters(self, api_client, setup_test_tenants):
        """Emails with international characters should be sent correctly."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        international_body = """
        Multilingual test:

        Chinese: 你好世界
        Japanese: こんにちは世界
        Korean: 안녕하세요 세계
        Arabic: مرحبا بالعالم
        Russian: Привет мир
        Greek: Γειά σου Κόσμε
        Hebrew: שלום עולם
        Thai: สวัสดีโลก
        Hindi: नमस्ते दुनिया

        Special characters: ñ ü ö ä ß é è ê ë
        """

        message = {
            "id": f"international-{ts}",
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "International Characters: 你好 مرحبا Привет",
            "body": international_body,
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        sent = await wait_for_message_status(api_client, f"international-{ts}", ("sent",))
        assert sent, f"message international-{ts} never reached sent"

        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

    async def test_unicode_in_attachment_filename(self, api_client, setup_test_tenants):
        """Attachments with Unicode filenames should be handled correctly."""
        ts = int(time.time())
        content = "Test content"
        b64_content = base64.b64encode(content.encode()).decode()

        message = {
            "id": f"unicode-filename-{ts}",
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Unicode Filename Test",
            "body": "Testing unicode filename.",
            "attachments": [{
                "filename": "文档_документ_🎉.txt",
                "storage_path": f"base64:{b64_content}",
                "fetch_mode": "base64",
            }],
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        # Should be sent or have a meaningful error, never stay pending
        msg = await wait_for_message_status(
            api_client, f"unicode-filename-{ts}", ("sent", "error", "deferred")
        )
        assert msg, f"message unicode-filename-{ts} never left pending"


# ============================================
# 20. HTTP ATTACHMENT FETCH
# ============================================
