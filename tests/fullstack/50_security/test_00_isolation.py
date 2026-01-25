# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Tenant isolation tests."""

from __future__ import annotations

import time

import pytest

from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    MAILHOG_TENANT2_API,
    clear_mailhog,
    trigger_dispatch,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestTenantIsolation:
    """Test that tenants are properly isolated."""

    async def test_messages_routed_to_correct_smtp(self, api_client, setup_test_tenants):
        """Messages should be routed to correct tenant's SMTP server."""
        await clear_mailhog(MAILHOG_TENANT1_API)
        await clear_mailhog(MAILHOG_TENANT2_API)

        ts = int(time.time())

        # Message for tenant 1
        msg1 = {
            "id": f"isolation-t1-{ts}",
            "account_id": "test-account-1",
            "from": "sender@tenant1.com",
            "to": ["recipient@example.com"],
            "subject": "Tenant 1 Isolation Test",
            "body": "This should go to tenant 1 SMTP.",
        }

        # Message for tenant 2
        msg2 = {
            "id": f"isolation-t2-{ts}",
            "account_id": "test-account-2",
            "from": "sender@tenant2.com",
            "to": ["recipient@example.com"],
            "subject": "Tenant 2 Isolation Test",
            "body": "This should go to tenant 2 SMTP.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [msg1, msg2]})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        # Verify isolation
        msgs_t1 = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        msgs_t2 = await wait_for_messages(MAILHOG_TENANT2_API, 1)

        assert len(msgs_t1) == 1, "Tenant 1 should have exactly 1 message"
        assert len(msgs_t2) == 1, "Tenant 2 should have exactly 1 message"

        assert msgs_t1[0]["Content"]["Headers"]["Subject"][0] == "Tenant 1 Isolation Test"
        assert msgs_t2[0]["Content"]["Headers"]["Subject"][0] == "Tenant 2 Isolation Test"

    async def test_run_now_triggers_dispatch(self, api_client, setup_test_tenants):
        """run-now should trigger immediate message dispatch."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())

        # Add message for tenant 1
        message = {
            "id": f"run-now-test-{ts}",
            "account_id": "test-account-1",
            "from": "sender@tenant1.com",
            "to": ["recipient@example.com"],
            "subject": "Run Now Test",
            "body": "Message triggered by run-now.",
        }
        await api_client.post("/commands/add-messages", json={"messages": [message]})

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")

        # Verify message was sent
        msgs = await wait_for_messages(MAILHOG_TENANT1_API, 1, timeout=10)
        assert len(msgs) >= 1
        assert msgs[0]["Content"]["Headers"]["Subject"][0] == "Run Now Test"
