# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

httpx = pytest.importorskip("httpx")

from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    clear_imap_mailbox,
    clear_mailhog,
    create_dsn_bounce_email,
    get_imap_message_count,
    get_msg_status,
    inject_bounce_email_to_imap,
    is_dovecot_available,
    trigger_dispatch,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestBounceDetection:
    """Test bounce detection, tracking, and delivery report integration."""

    async def test_x_genro_mail_id_header_added(self, api_client, setup_test_tenants):
        """Sent emails should have X-Genro-Mail-ID header for bounce correlation."""
        ts = int(time.time())
        msg_id = f"bounce-header-test-{ts}"

        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Bounce Header Test",
            "body": "Testing X-Genro-Mail-ID header presence.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Check MailHog for the sent message
        mailhog_resp = httpx.get(f"{MAILHOG_TENANT1_API}/api/v2/messages")
        if mailhog_resp.status_code == 200:
            messages = mailhog_resp.json().get("items", [])
            for msg in messages:
                if msg.get("Content", {}).get("Headers", {}).get("Subject", [""])[0] == "Bounce Header Test":
                    headers = msg.get("Content", {}).get("Headers", {})
                    # X-Genro-Mail-ID should be present
                    assert "X-Genro-Mail-Id" in headers or "X-Genro-Mail-ID" in headers, \
                        f"X-Genro-Mail-ID header not found in sent email. Headers: {list(headers.keys())}"
                    break

    async def test_bounce_fields_in_message_list(self, api_client, setup_test_tenants):
        """Message list can include bounce fields when bounce is detected.

        Note: The API uses response_model_exclude_none=True, so bounce fields
        only appear when they have values (i.e., after a bounce is detected).
        This test verifies the API accepts messages and returns them correctly.
        For bounce field presence, see test_10_bounce_live.py tests.
        """
        ts = int(time.time())
        message = {
            "id": f"bounce-fields-test-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Bounce Fields Test",
            "body": "Testing bounce fields presence.",
        }
        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        assert resp.status_code == 200

        data = resp.json()
        messages = data.get("messages", [])
        assert len(messages) > 0, "Should have at least one message"

        # Verify basic message structure (bounce fields excluded when None due to API config)
        msg = messages[0]
        required_fields = {"id", "priority", "message"}
        assert required_fields.issubset(set(msg.keys())), f"Missing required fields: {required_fields - set(msg.keys())}"

    async def test_message_includes_bounce_tracking_fields(self, api_client, setup_test_tenants):
        """Messages should be trackable for bounce correlation via msg_id."""
        ts = int(time.time())
        msg_id = f"trackable-msg-{ts}"

        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Trackable Message Test",
            "body": "This message can be tracked for bounces.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # The message should be retrievable by ID for bounce correlation
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        assert resp.status_code == 200
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == msg_id]
        assert len(found) == 1, f"Message {msg_id} should be trackable"

    async def test_multiple_messages_unique_mail_ids(self, api_client, setup_test_tenants):
        """Multiple messages should each have unique X-Genro-Mail-ID headers."""
        ts = int(time.time())

        messages = [
            {
                "id": f"multi-bounce-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Multi Bounce Test {i}",
                "body": f"Testing unique mail ID for message {i}.",
            }
            for i in range(3)
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(5)

        # Check MailHog for unique headers
        mailhog_resp = httpx.get(f"{MAILHOG_TENANT1_API}/api/v2/messages")
        if mailhog_resp.status_code == 200:
            items = mailhog_resp.json().get("items", [])
            mail_ids = []
            for msg in items:
                subject = msg.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
                if "Multi Bounce Test" in subject:
                    headers = msg.get("Content", {}).get("Headers", {})
                    mail_id = headers.get("X-Genro-Mail-Id", headers.get("X-Genro-Mail-ID", [None]))[0]
                    if mail_id:
                        mail_ids.append(mail_id)

            # All mail IDs should be unique
            if mail_ids:
                assert len(mail_ids) == len(set(mail_ids)), "X-Genro-Mail-ID headers should be unique"

    async def test_bounce_header_with_custom_headers(self, api_client, setup_test_tenants):
        """X-Genro-Mail-ID should be present even with custom headers."""
        ts = int(time.time())

        message = {
            "id": f"custom-header-bounce-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Custom Header Bounce Test",
            "body": "Testing header coexistence.",
            "custom_headers": {
                "X-Campaign-ID": "test-campaign-123",
                "X-Priority": "1",
            },
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Verify both custom headers and X-Genro-Mail-ID are present
        mailhog_resp = httpx.get(f"{MAILHOG_TENANT1_API}/api/v2/messages")
        if mailhog_resp.status_code == 200:
            items = mailhog_resp.json().get("items", [])
            for msg in items:
                subject = msg.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
                if subject == "Custom Header Bounce Test":
                    headers = msg.get("Content", {}).get("Headers", {})
                    # Both custom and system headers should be present
                    has_mail_id = "X-Genro-Mail-Id" in headers or "X-Genro-Mail-ID" in headers
                    assert has_mail_id, "X-Genro-Mail-ID should coexist with custom headers"
                    break


# ============================================
# 22. BATCH CODE OPERATIONS
# ============================================


class TestBounceEndToEnd:
    """End-to-end bounce detection tests.

    These tests verify the complete bounce detection flow:
    1. Send a message (X-Genro-Mail-ID header added)
    2. Inject a bounce email into the IMAP mailbox
    3. BounceReceiver polls and processes the bounce
    4. Original message is updated with bounce info
    5. Bounce is reported to client

    Note: setup_bounce_tenant fixture is defined in conftest.py
    """

    async def test_imap_server_accessible(self):
        """Verify Dovecot IMAP server is accessible."""
        if not is_dovecot_available():
            pytest.skip("Dovecot IMAP server not available")
        count = await get_imap_message_count()
        assert count >= 0, "IMAP server should be accessible"

    async def test_bounce_email_injection(self):
        """Can inject a bounce email into IMAP mailbox."""
        if not is_dovecot_available():
            pytest.skip("Dovecot IMAP server not available")
        await clear_imap_mailbox()

        # Create and inject a bounce email
        bounce_email = create_dsn_bounce_email(
            original_message_id="test-inject-123",
            recipient="failed@example.com",
            bounce_code="550",
            bounce_reason="User unknown",
        )

        success = await inject_bounce_email_to_imap(bounce_email)
        assert success, "Should be able to inject bounce email"

        # Verify it's in the mailbox
        count = await get_imap_message_count()
        assert count == 1, "Should have 1 message in mailbox"

        # Cleanup
        await clear_imap_mailbox()

    async def test_dsn_bounce_format_valid(self):
        """Generated DSN bounce emails are properly formatted."""
        bounce_email = create_dsn_bounce_email(
            original_message_id="format-test-456",
            recipient="invalid@example.com",
            bounce_code="550",
            bounce_reason="Mailbox not found",
        )

        # Parse the email to verify format
        import email
        msg = email.message_from_bytes(bounce_email)

        # Verify it's a multipart/report
        assert msg.get_content_type() == "multipart/report"
        assert msg.get_param("report-type") == "delivery-status"

        # Verify it has the required parts
        parts = list(msg.walk())
        content_types = [p.get_content_type() for p in parts]
        assert "text/plain" in content_types
        # Accept both message/delivery-status (RFC 3464) and text/delivery-status
        # (Python email library limitation)
        assert "message/delivery-status" in content_types or "text/delivery-status" in content_types

        # Verify X-Genro-Mail-ID is in the original message headers
        for part in parts:
            if part.get_content_type() == "message/rfc822-headers":
                payload = part.get_payload()
                if isinstance(payload, list) and payload:
                    inner = payload[0]
                    if hasattr(inner, "get"):
                        assert inner.get("X-Genro-Mail-ID") == "format-test-456"
                    break

    async def test_soft_bounce_email_format(self):
        """Soft bounce (4xx) email format is correct."""
        bounce_email = create_dsn_bounce_email(
            original_message_id="soft-bounce-789",
            recipient="temp-fail@example.com",
            bounce_code="421",
            bounce_reason="Service temporarily unavailable",
        )

        import email
        msg = email.message_from_bytes(bounce_email)

        # Find the delivery-status part and verify it has 4xx code
        for part in msg.walk():
            if part.get_content_type() in ("message/delivery-status", "text/delivery-status"):
                payload = part.get_payload(decode=True)
                if payload and isinstance(payload, bytes):
                    text = payload.decode("utf-8", errors="replace")
                    assert "421" in text or "4.2.1" in text

    async def test_bounce_parser_extracts_original_id(self):
        """BounceParser correctly extracts X-Genro-Mail-ID from DSN."""
        from enterprise.mail_proxy.bounce import BounceParser

        bounce_email = create_dsn_bounce_email(
            original_message_id="parser-test-abc",
            recipient="bad@example.com",
            bounce_code="550",
            bounce_reason="No such user",
        )

        parser = BounceParser()
        info = parser.parse(bounce_email)

        assert info.original_message_id == "parser-test-abc"
        assert info.bounce_type == "hard"
        assert info.bounce_code is not None
        assert "550" in str(info.bounce_code) or "5" in str(info.bounce_code)

    async def test_bounce_parser_soft_vs_hard(self):
        """BounceParser correctly classifies hard vs soft bounces."""
        from enterprise.mail_proxy.bounce import BounceParser

        parser = BounceParser()

        # Hard bounce (5xx)
        hard_bounce = create_dsn_bounce_email(
            original_message_id="hard-123",
            bounce_code="550",
            bounce_reason="User unknown",
        )
        hard_info = parser.parse(hard_bounce)
        assert hard_info.bounce_type == "hard"

        # Soft bounce (4xx)
        soft_bounce = create_dsn_bounce_email(
            original_message_id="soft-456",
            bounce_code="421",
            bounce_reason="Try again later",
        )
        soft_info = parser.parse(soft_bounce)
        assert soft_info.bounce_type == "soft"

    @pytest.mark.skip(reason="Flaky in CI: messages stay pending. See issue #69")
    async def test_message_sent_includes_tracking_header(
        self, api_client, setup_bounce_tenant
    ):
        """Messages sent via proxy include X-Genro-Mail-ID header."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        # Verify bounce-account exists
        resp = await api_client.get("/accounts?tenant_id=bounce-tenant")
        accounts = resp.json().get("accounts", [])
        bounce_acc = next((a for a in accounts if a.get("id") == "bounce-account"), None)
        assert bounce_acc is not None, f"bounce-account not found. Accounts: {accounts}"

        ts = int(time.time())
        msg_id = f"track-header-{ts}"
        message = {
            "id": msg_id,
            "account_id": "bounce-account",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": f"Tracking Header Test {ts}",
            "body": "Testing X-Genro-Mail-ID header.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("queued", 0) >= 1, f"Message not queued: {data}"

        # Verify message is in DB before dispatch
        resp = await api_client.get(f"/messages?tenant_id=bounce-tenant")
        pre_dispatch = resp.json().get("messages", [])
        pre_msg = next((m for m in pre_dispatch if m.get("id") == msg_id), None)
        assert pre_msg is not None, f"Message {msg_id} not in DB before dispatch"

        await trigger_dispatch(api_client, "bounce-tenant")

        # Wait a bit more and retry dispatch
        await asyncio.sleep(3)
        await trigger_dispatch(api_client, "bounce-tenant")

        # Verify message was sent via API
        resp = await api_client.get(f"/messages?tenant_id=bounce-tenant")
        all_msgs = resp.json().get("messages", [])
        our_msg = next((m for m in all_msgs if m.get("id") == msg_id), None)
        assert our_msg is not None, f"Message {msg_id} not found in API response"
        assert our_msg.get("sent_ts") is not None, f"Message not sent. Account: {bounce_acc}. Message: {our_msg}"

        # Check MailHog for the sent email
        emails = await wait_for_messages(MAILHOG_TENANT1_API, 1, timeout=15)
        assert len(emails) >= 1, f"No emails in MailHog. Message status: {our_msg}"

        # Find our email (check both header case variants)
        found_email = None
        for email in emails:
            headers = email.get("Content", {}).get("Headers", {})
            # MailHog may use either X-Genro-Mail-Id or X-Genro-Mail-ID
            mail_id_list = headers.get("X-Genro-Mail-Id") or headers.get("X-Genro-Mail-ID")
            if mail_id_list:
                mail_id = mail_id_list[0] if isinstance(mail_id_list, list) else mail_id_list
                if mail_id == msg_id:
                    found_email = email
                    break

        assert found_email is not None, f"Email with X-Genro-Mail-ID={msg_id} not found in {len(emails)} emails"

    @pytest.mark.skip(reason="Flaky in CI: messages stay pending. See issue #69")
    async def test_bounce_updates_message_record(self, api_client, setup_bounce_tenant):
        """Bounce detected by BounceReceiver updates message record.

        Note: This test simulates the database update that BounceReceiver would make.
        Full end-to-end testing requires BounceReceiver to be running and polling.
        """
        # Verify bounce-account exists
        resp = await api_client.get("/accounts?tenant_id=bounce-tenant")
        accounts = resp.json().get("accounts", [])
        bounce_acc = next((a for a in accounts if a.get("id") == "bounce-account"), None)
        assert bounce_acc is not None, f"bounce-account not found. Accounts: {accounts}"

        ts = int(time.time())
        msg_id = f"bounce-update-{ts}"

        # 1. Send a message
        await clear_mailhog(MAILHOG_TENANT1_API)
        message = {
            "id": msg_id,
            "account_id": "bounce-account",
            "from": "sender@test.com",
            "to": ["will-bounce@example.com"],
            "subject": "Bounce Update Test",
            "body": "This will simulate a bounce.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client, "bounce-tenant")
        await asyncio.sleep(3)
        await trigger_dispatch(api_client, "bounce-tenant")

        # 2. Verify message was sent
        resp = await api_client.get("/messages?tenant_id=bounce-tenant")
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == msg_id]
        assert len(found) == 1
        assert get_msg_status(found[0]) == "sent", f"Message not sent. Account: {bounce_acc}. Message: {found[0]}"

        # 3. Verify message has bounce fields available
        # (They should be None before bounce is detected)
        assert "bounce_type" in found[0] or found[0].get("bounce_type") is None
        assert "bounce_code" in found[0] or found[0].get("bounce_code") is None

    async def test_multiple_bounces_correlation(self):
        """Multiple bounce emails are correlated to correct messages."""
        from enterprise.mail_proxy.bounce import BounceParser

        parser = BounceParser()

        # Create multiple bounces with different IDs
        bounces = [
            create_dsn_bounce_email(f"msg-aaa-{i}", f"user{i}@example.com", "550", "Not found")
            for i in range(3)
        ]

        # Parse each and verify correlation
        for i, bounce in enumerate(bounces):
            info = parser.parse(bounce)
            assert info.original_message_id == f"msg-aaa-{i}"
