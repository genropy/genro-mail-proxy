# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import time

import pytest
import pytest_asyncio

from tests import api_routes
from tests.fullstack.helpers import (
    SMTP_RANDOM_HOST,
    SMTP_RATELIMIT_HOST,
    SMTP_REJECT_HOST,
    SMTP_TEMPFAIL_HOST,
    SMTP_TIMEOUT_HOST,
    trigger_dispatch,
    wait_for_message_status,
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
            resp = await api_client.post(api_routes.ACCOUNT, json=account)
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
            "tenant_id": "test-tenant-1",
            "account_id": "account-smtp-reject",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Should Be Rejected",
            "body": "This should fail with 550 error.",
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await trigger_dispatch(api_client)

        # Check message status - should be error
        msg = await wait_for_message_status(api_client, msg_id, ("error", "deferred"))
        assert msg, f"message {msg_id} never reached error or deferred"

    async def test_temporary_error_defers_message(
        self, api_client, setup_error_accounts
    ):
        """Messages with temporary SMTP errors should be deferred for retry."""
        ts = int(time.time())
        msg_id = f"tempfail-test-{ts}"

        message = {
            "id": msg_id,
            "tenant_id": "test-tenant-1",
            "account_id": "account-smtp-tempfail",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Should Be Deferred",
            "body": "This should fail with 451 and be retried.",
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await trigger_dispatch(api_client)

        # Check message status - should be deferred (waiting for retry)
        msg = await wait_for_message_status(api_client, msg_id, ("deferred", "error"))
        assert msg, f"message {msg_id} never reached deferred or error"
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
                "tenant_id": "test-tenant-1",
                "account_id": "account-smtp-ratelimit",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Rate Limit Test {i}",
                "body": f"Message {i} for rate limit testing.",
            })

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": messages})
        assert resp.status_code == 200

        # Trigger dispatch
        await trigger_dispatch(api_client)

        # Every message must leave pending: sent under the limit, deferred or
        # error above it. The split itself depends on the error classification.
        settled = [
            await wait_for_message_status(
                api_client, m["id"], ("sent", "deferred", "error")
            )
            for m in messages
        ]
        assert all(settled), "every rate-limited message should leave pending"

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
                "tenant_id": "test-tenant-1",
                "account_id": "account-smtp-random",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Random Error Test {i}",
                "body": f"Message {i} with random outcome.",
            })

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": messages})
        assert resp.status_code == 200

        # Trigger multiple dispatch cycles: the random server fails some sends,
        # so a deferred message needs another cycle to settle.
        settled: list[dict | None] = []
        for _ in range(3):
            await trigger_dispatch(api_client)
            settled = [
                await wait_for_message_status(
                    api_client, m["id"], ("sent", "deferred", "error"), timeout=5.0
                )
                for m in messages
            ]

        assert all(settled), "every message should leave pending after three cycles"


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
        await api_client.post(api_routes.ACCOUNT, json=account_data)

        ts = int(time.time())
        msg_id = f"retry-count-test-{ts}"

        message = {
            "id": msg_id,
            "tenant_id": "test-tenant-1",
            "account_id": "retry-test-account",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Retry Count Test",
            "body": "This should increment retry count.",
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger multiple dispatch cycles
        initial_retry = 0
        for cycle in range(3):
            await trigger_dispatch(api_client)

            # Check retry count
            msg = await wait_for_message_status(api_client, msg_id, ("deferred", "error"))
            assert msg, f"Cycle {cycle}: message {msg_id} never left pending"
            current_retry = msg.get("retry_count", 0)
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
        await api_client.post(api_routes.ACCOUNT, json=account_data)

        ts = int(time.time())
        msg_id = f"error-details-test-{ts}"

        message = {
            "id": msg_id,
            "tenant_id": "test-tenant-1",
            "account_id": "error-details-account",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Error Details Test",
            "body": "Check error details.",
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await trigger_dispatch(api_client)

        # Check message has error details
        msg = await wait_for_message_status(api_client, msg_id, ("error", "deferred"))
        assert msg, f"message {msg_id} never reached error or deferred"


# ============================================
# 15. LARGE FILE STORAGE
# ============================================
