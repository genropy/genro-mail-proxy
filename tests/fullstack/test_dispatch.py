# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Basic message dispatch tests."""

from __future__ import annotations

import time

import pytest

from .helpers import (
    MAILHOG_TENANT1_API,
    clear_mailhog,
    trigger_dispatch,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestBasicMessageDispatch:
    """Test basic email sending functionality."""

    async def test_send_simple_text_email(self, api_client, setup_test_tenants):
        """Send a simple text email."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        msg_id = f"simple-text-{int(time.time())}"
        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Simple Text Email",
            "body": "This is a simple text email.",
        }
        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

        msg = messages[0]
        assert msg["Content"]["Headers"]["Subject"][0] == "Simple Text Email"

    async def test_send_html_email(self, api_client, setup_test_tenants):
        """Send an HTML email."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        msg_id = f"html-email-{int(time.time())}"
        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "HTML Email Test",
            "body": "<html><body><h1>Hello!</h1><p>HTML content.</p></body></html>",
            "content_type": "html",
        }
        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

    async def test_send_email_with_cc_bcc(self, api_client, setup_test_tenants):
        """Send email with CC and BCC."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        msg_id = f"cc-bcc-{int(time.time())}"
        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "cc_addr": ["cc@example.com"],
            "bcc_addr": ["bcc@example.com"],
            "subject": "CC/BCC Test",
            "body": "Email with CC and BCC.",
        }
        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

    async def test_send_email_with_custom_headers(self, api_client, setup_test_tenants):
        """Send email with custom headers."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        msg_id = f"custom-headers-{int(time.time())}"
        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Custom Headers Test",
            "body": "Email with custom headers.",
            "headers": {
                "X-Custom-Header": "custom-value",
                "X-Priority": "1",
                "Reply-To": "reply@test.com",
            },
        }
        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

        msg = messages[0]
        headers = msg["Content"]["Headers"]
        assert headers.get("X-Custom-Header", [""])[0] == "custom-value"
        assert headers.get("X-Priority", [""])[0] == "1"
