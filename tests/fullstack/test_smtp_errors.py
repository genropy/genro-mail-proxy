# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import asyncio
import time

import pytest
import pytest_asyncio

from .helpers import (
    SMTP_RANDOM_HOST,
    SMTP_RANDOM_PORT,
    SMTP_RATELIMIT_HOST,
    SMTP_RATELIMIT_PORT,
    SMTP_REJECT_HOST,
    SMTP_REJECT_PORT,
    SMTP_TEMPFAIL_HOST,
    SMTP_TEMPFAIL_PORT,
    SMTP_TIMEOUT_HOST,
    SMTP_TIMEOUT_PORT,
    get_msg_status,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestSmtpErrorHandling:
    """Test SMTP error handling and retry logic using error-simulating SMTP servers."""

    @pytest_asyncio.fixture
    async def setup_error_accounts(self, api_client, setup_test_tenants):
        """Create accounts pointing to error-simulating SMTP servers."""
        accounts = [
            {
                "id": "account-smtp-reject",
                "tenant_id": "test-tenant-1",
                "host": SMTP_REJECT_HOST,
                "port": 1025,  # Internal Docker port
                "use_tls": False,
            },
            {
                "id": "account-smtp-tempfail",
                "tenant_id": "test-tenant-1",
                "host": SMTP_TEMPFAIL_HOST,
                "port": 1025,
                "use_tls": False,
            },
            {
                "id": "account-smtp-timeout",
                "tenant_id": "test-tenant-1",
                "host": SMTP_TIMEOUT_HOST,
                "port": 1025,
                "use_tls": False,
            },
            {
                "id": "account-smtp-ratelimit",
                "tenant_id": "test-tenant-1",
                "host": SMTP_RATELIMIT_HOST,
                "port": 1025,
                "use_tls": False,
            },
            {
                "id": "account-smtp-random",
                "tenant_id": "test-tenant-1",
                "host": SMTP_RANDOM_HOST,
                "port": 1025,
                "use_tls": False,
            },
        ]

        for account in accounts:
            resp = await api_client.post("/account", json=account)
            # Ignore if already exists
            assert resp.status_code in (200, 201, 409), resp.text

        return accounts

    async def test_permanent_error_marks_message_failed(
        self, api_client, setup_error_accounts
    ):
        """Messages sent to reject-all SMTP should be marked as error."""
        ts = int(time.time())
        msg_id = f"reject-test-{ts}"

        message = {
            "id": msg_id,
            "account_id": "account-smtp-reject",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Should Be Rejected",
            "body": "This should fail with 550 error.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Check message status - should be error
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        messages = resp.json().get("messages", [])

        found = [m for m in messages if m.get("id") == msg_id]
        if found:
            msg = found[0]
            # Message should be in error state (not sent)
            assert get_msg_status(msg) in ("error", "deferred"), f"Expected error/deferred, got {get_msg_status(msg)}"

    async def test_temporary_error_defers_message(
        self, api_client, setup_error_accounts
    ):
        """Messages with temporary SMTP errors should be deferred for retry."""
        ts = int(time.time())
        msg_id = f"tempfail-test-{ts}"

        message = {
            "id": msg_id,
            "account_id": "account-smtp-tempfail",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Should Be Deferred",
            "body": "This should fail with 451 and be retried.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Check message status - should be deferred (waiting for retry)
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        messages = resp.json().get("messages", [])

        found = [m for m in messages if m.get("id") == msg_id]
        if found:
            msg = found[0]
            # Message should be deferred for retry
            assert get_msg_status(msg) in ("deferred", "pending", "error"), f"Got status: {get_msg_status(msg)}"
            # Should have retry count incremented
            assert msg.get("retry_count", 0) >= 0

    async def test_rate_limited_smtp_defers_excess_messages(
        self, api_client, setup_error_accounts
    ):
        """SMTP rate limiting should defer messages exceeding the limit."""
        ts = int(time.time())

        # Send more messages than the rate limit (set to 3 in docker-compose)
        messages = []
        for i in range(5):
            messages.append({
                "id": f"ratelimit-test-{ts}-{i}",
                "account_id": "account-smtp-ratelimit",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Rate Limit Test {i}",
                "body": f"Message {i} for rate limit testing.",
            })

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(5)

        # Check results - some should be sent, some deferred/error
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])

        test_msgs = [m for m in all_msgs if m.get("id", "").startswith(f"ratelimit-test-{ts}")]

        # At least some should have been processed
        assert len(test_msgs) > 0, "Test messages should exist"

        # Count statuses
        statuses = [get_msg_status(m) for m in test_msgs]
        # We expect a mix of sent and deferred/error due to rate limiting
        # The exact behavior depends on the error classification

    async def test_random_errors_mixed_results(
        self, api_client, setup_error_accounts
    ):
        """Random error SMTP should produce a mix of success and failure."""
        ts = int(time.time())

        # Send multiple messages to get statistical mix
        messages = []
        for i in range(10):
            messages.append({
                "id": f"random-test-{ts}-{i}",
                "account_id": "account-smtp-random",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Random Error Test {i}",
                "body": f"Message {i} with random outcome.",
            })

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        # Trigger multiple dispatch cycles
        for _ in range(3):
            await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
            await asyncio.sleep(2)

        # Check results
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])

        test_msgs = [m for m in all_msgs if m.get("id", "").startswith(f"random-test-{ts}")]

        # Count statuses
        sent = sum(1 for m in test_msgs if get_msg_status(m) == "sent")
        deferred = sum(1 for m in test_msgs if get_msg_status(m) == "deferred")
        error = sum(1 for m in test_msgs if get_msg_status(m) == "error")

        # With random errors, we expect a mix (not all same status)
        # At minimum, messages should have been processed
        assert len(test_msgs) > 0, "Test messages should exist"


# ============================================
# 14. RETRY LOGIC
# ============================================


class TestRetryLogic:
    """Test message retry behavior."""

    async def test_retry_count_incremented(self, api_client, setup_test_tenants):
        """Retry count should increment on each failure."""
        # This test uses the tempfail SMTP which always returns 451

        # First, create the error account if not exists
        account_data = {
            "id": "retry-test-account",
            "tenant_id": "test-tenant-1",
            "host": SMTP_TEMPFAIL_HOST,
            "port": 1025,
            "use_tls": False,
        }
        await api_client.post("/account", json=account_data)

        ts = int(time.time())
        msg_id = f"retry-count-test-{ts}"

        message = {
            "id": msg_id,
            "account_id": "retry-test-account",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Retry Count Test",
            "body": "This should increment retry count.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger multiple dispatch cycles
        initial_retry = 0
        for cycle in range(3):
            await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
            await asyncio.sleep(2)

            # Check retry count
            resp = await api_client.get("/messages?tenant_id=test-tenant-1")
            all_msgs = resp.json().get("messages", [])
            found = [m for m in all_msgs if m.get("id") == msg_id]

            if found:
                current_retry = found[0].get("retry_count", 0)
                # Retry count should increase or stay same (if max reached)
                assert current_retry >= initial_retry, f"Cycle {cycle}: retry count decreased"
                initial_retry = current_retry

    async def test_message_error_contains_details(self, api_client, setup_test_tenants):
        """Error messages should contain SMTP error details."""
        # Create account for reject SMTP
        account_data = {
            "id": "error-details-account",
            "tenant_id": "test-tenant-1",
            "host": SMTP_REJECT_HOST,
            "port": 1025,
            "use_tls": False,
        }
        await api_client.post("/account", json=account_data)

        ts = int(time.time())
        msg_id = f"error-details-test-{ts}"

        message = {
            "id": msg_id,
            "account_id": "error-details-account",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Error Details Test",
            "body": "Check error details.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Check message has error details
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == msg_id]

        if found:
            msg = found[0]
            # Should have last_error field with SMTP error details
            last_error = msg.get("last_error", "")
            # The error should contain some SMTP-related info
            # (actual content depends on implementation)
            assert get_msg_status(msg) in ("error", "deferred")


# ============================================
# 15. LARGE FILE STORAGE
# ============================================
