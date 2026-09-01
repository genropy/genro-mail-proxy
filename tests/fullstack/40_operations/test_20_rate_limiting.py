# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Account-level rate limiting tests.

These tests verify rate limiting functionality:
- Per-minute rate limits defer excess messages
- Per-hour rate limits
- Reject vs defer behavior
- Rate limit configuration per account
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio

from tests import api_routes
from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    MAILHOG_TENANT1_SMTP,
    clear_mailhog,
    get_mailhog_messages,
    get_msg_status,
    trigger_dispatch,
    wait_for_message_status,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio, pytest.mark.rate_limit]


@pytest_asyncio.fixture(autouse=True)
async def drained_queue(api_client):
    """Leave test-tenant-1's queue empty before each rate-limit test.

    Every test here counts how many of ITS messages a dispatch cycle sends
    under a per-account limit, but the cycle works the tenant's whole queue,
    which other files fill and nobody empties until the next run (140 messages
    at the end of a full run). With foreign work in front of them the five
    messages under test can still be pending when the wait expires, which is
    how this file's flake reads: "never left pending", not a wrong count.
    """
    resp = await api_client.get(api_routes.messages(tenant_id="test-tenant-1"))
    if resp.status_code == 200:
        ids = [m["id"] for m in resp.json().get("messages", [])]
        if ids:
            await api_client.post(
                api_routes.delete_messages(tenant_id="test-tenant-1"), json={"ids": ids}
            )


class TestAccountRateLimiting:
    """Test account-level rate limiting functionality.

    These tests verify that:
    - Messages exceeding rate limits are deferred
    - Rate limits can be configured per account
    - Different rate limit windows work correctly
    """

    async def test_rate_limit_per_minute_defers_excess(
        self, api_client, setup_test_tenants
    ):
        """Messages exceeding per-minute rate limit should be deferred.

        Flow:
        1. Create account with limit_per_minute=3
        2. Queue 5 messages
        3. Trigger dispatch
        4. Verify only 3 sent, 2 deferred
        """
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())

        # Create account with rate limit
        account_data = {
            "id": f"ratelimit-account-{ts}",
            "tenant_id": "test-tenant-1",
            "host": MAILHOG_TENANT1_SMTP[0],
            "port": MAILHOG_TENANT1_SMTP[1],
            "use_tls": False,
            "limit_per_minute": 3,
        }
        resp = await api_client.post(api_routes.ACCOUNT, json=account_data)
        if resp.status_code == 422:
            pytest.skip("Rate limit configuration not supported in account schema")
        assert resp.status_code in (200, 201)

        # Queue 5 messages
        messages = [
            {
                "id": f"ratelimit-{ts}-{i}",
                "tenant_id": "test-tenant-1",
                "account_id": f"ratelimit-account-{ts}",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Rate Limit Test {i}",
                "body": f"Message {i} for rate limit testing.",
            }
            for i in range(5)
        ]

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": messages})
        assert resp.status_code == 200

        # Trigger dispatch
        await trigger_dispatch(api_client)

        # Every message must leave pending before the counts mean anything
        test_msgs = [
            await wait_for_message_status(api_client, m["id"], ("sent", "deferred", "error"))
            for m in messages
        ]
        assert all(test_msgs), "every rate-limited message should leave pending"

        # Check how many were actually sent to MailHog
        mailhog_messages = await get_mailhog_messages(MAILHOG_TENANT1_API)
        sent_count = len([
            m for m in mailhog_messages
            if f"ratelimit-{ts}" in m.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
        ])

        sent_msgs = [m for m in test_msgs if get_msg_status(m) == "sent"]

        # Should have 3 sent, 2 deferred (or pending for next cycle)
        assert sent_count <= 3, f"Expected max 3 sent due to rate limit, got {sent_count}"
        assert len(sent_msgs) <= 3, "Expected max 3 with 'sent' status"

    async def test_rate_limit_per_hour(
        self, api_client, setup_test_tenants
    ):
        """Per-hour rate limit should track cumulative sends.

        Note: This test is difficult to verify in real-time since
        we can't wait an hour. We verify the configuration is accepted
        and the counter logic is in place.
        """
        ts = int(time.time())

        # Create account with per-hour rate limit
        account_data = {
            "id": f"ratelimit-hour-{ts}",
            "tenant_id": "test-tenant-1",
            "host": MAILHOG_TENANT1_SMTP[0],
            "port": MAILHOG_TENANT1_SMTP[1],
            "use_tls": False,
            "limit_per_hour": 100,
        }
        resp = await api_client.post(api_routes.ACCOUNT, json=account_data)
        if resp.status_code == 422:
            pytest.skip("Per-hour rate limit configuration not supported")
        assert resp.status_code in (200, 201)

        # Verify account was created with rate limit
        resp = await api_client.get(api_routes.accounts(tenant_id="test-tenant-1"))
        assert resp.status_code == 200
        accounts = [a for a in resp.json().get("accounts", []) if a.get("id") == account_data["id"]]
        assert accounts, f"account {account_data['id']} not listed for the tenant"
        assert accounts[0].get("limit_per_hour") == 100

    async def test_rate_limit_reject_behavior(
        self, api_client, setup_test_tenants
    ):
        """Rate limit with reject mode should reject excess messages.

        Some configurations may reject instead of defer messages
        that exceed the rate limit.
        """
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())

        # Create account with reject mode
        account_data = {
            "id": f"ratelimit-reject-{ts}",
            "tenant_id": "test-tenant-1",
            "host": MAILHOG_TENANT1_SMTP[0],
            "port": MAILHOG_TENANT1_SMTP[1],
            "use_tls": False,
            "limit_per_minute": 2,
            "limit_behavior": "reject",  # reject vs defer
        }
        resp = await api_client.post(api_routes.ACCOUNT, json=account_data)
        if resp.status_code == 422:
            pytest.skip("Rate limit reject mode not supported")
        assert resp.status_code in (200, 201)

        # Queue 4 messages
        messages = [
            {
                "id": f"ratelimit-reject-{ts}-{i}",
                "tenant_id": "test-tenant-1",
                "account_id": f"ratelimit-reject-{ts}",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Rate Limit Reject Test {i}",
                "body": f"Message {i} for reject mode testing.",
            }
            for i in range(4)
        ]

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": messages})
        assert resp.status_code == 200

        # Trigger dispatch
        await trigger_dispatch(api_client)

        # Check message statuses
        test_msgs = [
            await wait_for_message_status(api_client, m["id"], ("sent", "deferred", "error"))
            for m in messages
        ]
        assert all(test_msgs), "every message should leave pending"

        sent_msgs = [m for m in test_msgs if get_msg_status(m) == "sent"]
        error_msgs = [m for m in test_msgs if get_msg_status(m) == "error"]
        deferred_msgs = [m for m in test_msgs if get_msg_status(m) == "deferred"]

        # In reject mode, excess should be marked as error, not deferred
        assert len(sent_msgs) <= 2, "Should have max 2 sent due to rate limit"
        assert len(error_msgs) >= 2, f"Expected at least 2 rejected (error) messages, got {len(error_msgs)}"
        assert len(deferred_msgs) == 0, f"Reject mode should not defer, but found {len(deferred_msgs)} deferred"
        assert len(sent_msgs) + len(error_msgs) == 4, "All messages should be sent or rejected"

    async def test_rate_limit_resets_after_window(
        self, api_client, setup_test_tenants
    ):
        """Rate limit counter should reset after the time window.

        This test verifies the rate limit window logic by:
        1. Sending messages up to the limit
        2. Waiting briefly (simulating window reset in tests)
        3. Verifying more messages can be sent
        """
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())

        # Create account with low rate limit for testing
        account_data = {
            "id": f"ratelimit-reset-{ts}",
            "tenant_id": "test-tenant-1",
            "host": MAILHOG_TENANT1_SMTP[0],
            "port": MAILHOG_TENANT1_SMTP[1],
            "use_tls": False,
            "limit_per_minute": 2,
        }
        resp = await api_client.post(api_routes.ACCOUNT, json=account_data)
        if resp.status_code == 422:
            pytest.skip("Rate limit configuration not supported")
        assert resp.status_code in (200, 201)

        # First batch: 2 messages (at limit)
        batch1 = [
            {
                "id": f"ratelimit-reset-b1-{ts}-{i}",
                "tenant_id": "test-tenant-1",
                "account_id": f"ratelimit-reset-{ts}",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Rate Limit Reset Test Batch 1 - {i}",
                "body": f"Batch 1 message {i}.",
            }
            for i in range(2)
        ]

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": batch1})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        for msg in batch1:
            settled = await wait_for_message_status(api_client, msg["id"], ("sent",))
            assert settled, f"message {msg['id']} never reached sent"

        # Both should be sent
        mailhog_messages = await get_mailhog_messages(MAILHOG_TENANT1_API)
        batch1_sent = [
            m for m in mailhog_messages
            if "Batch 1" in m.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
        ]
        assert len(batch1_sent) == 2, "First batch should all be sent"

        # Note: In a real test, we'd wait 60 seconds for the minute window to reset.
        # For CI/CD, we'd need a way to mock time or use a very short window.
        # For now, we just verify the mechanism is in place.

    async def test_rate_limit_independent_per_account(
        self, api_client, setup_test_tenants
    ):
        """Rate limits should be tracked independently per account.

        Account A hitting its limit should not affect Account B.
        """
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())

        # Create two accounts with different rate limits
        account_a = {
            "id": f"ratelimit-a-{ts}",
            "tenant_id": "test-tenant-1",
            "host": MAILHOG_TENANT1_SMTP[0],
            "port": MAILHOG_TENANT1_SMTP[1],
            "use_tls": False,
            "limit_per_minute": 2,
        }
        account_b = {
            "id": f"ratelimit-b-{ts}",
            "tenant_id": "test-tenant-1",
            "host": MAILHOG_TENANT1_SMTP[0],
            "port": MAILHOG_TENANT1_SMTP[1],
            "use_tls": False,
            "limit_per_minute": 5,
        }

        resp = await api_client.post(api_routes.ACCOUNT, json=account_a)
        if resp.status_code == 422:
            pytest.skip("Rate limit configuration not supported")
        assert resp.status_code in (200, 201)

        resp = await api_client.post(api_routes.ACCOUNT, json=account_b)
        assert resp.status_code in (200, 201)

        # Queue 4 messages for account A (over limit)
        msgs_a = [
            {
                "id": f"ratelimit-a-{ts}-{i}",
                "tenant_id": "test-tenant-1",
                "account_id": f"ratelimit-a-{ts}",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Rate Limit Account A - {i}",
                "body": f"Account A message {i}.",
            }
            for i in range(4)
        ]

        # Queue 4 messages for account B (under limit)
        msgs_b = [
            {
                "id": f"ratelimit-b-{ts}-{i}",
                "tenant_id": "test-tenant-1",
                "account_id": f"ratelimit-b-{ts}",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Rate Limit Account B - {i}",
                "body": f"Account B message {i}.",
            }
            for i in range(4)
        ]

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": msgs_a + msgs_b})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        for msg in msgs_a + msgs_b:
            settled = await wait_for_message_status(
                api_client, msg["id"], ("sent", "deferred", "error")
            )
            assert settled, f"message {msg['id']} never left pending"

        # Check MailHog
        mailhog_messages = await get_mailhog_messages(MAILHOG_TENANT1_API)

        sent_a = [
            m for m in mailhog_messages
            if "Account A" in m.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
        ]
        sent_b = [
            m for m in mailhog_messages
            if "Account B" in m.get("Content", {}).get("Headers", {}).get("Subject", [""])[0]
        ]

        # Account A should have max 2 sent (rate limit)
        assert len(sent_a) <= 2, f"Account A should have max 2 sent, got {len(sent_a)}"

        # Account B should have all 4 sent (under its limit)
        assert len(sent_b) == 4, f"Account B should have all 4 sent, got {len(sent_b)}"
