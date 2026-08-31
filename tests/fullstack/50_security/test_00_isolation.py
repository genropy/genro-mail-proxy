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
        subject_t1 = f"Tenant 1 Isolation Test {ts}"
        subject_t2 = f"Tenant 2 Isolation Test {ts}"

        # Message for tenant 1
        msg1 = {
            "id": f"isolation-t1-{ts}",
            "account_id": "test-account-1",
            "from": "sender@tenant1.com",
            "to": ["recipient@example.com"],
            "subject": subject_t1,
            "body": "This should go to tenant 1 SMTP.",
        }

        # Message for tenant 2
        msg2 = {
            "id": f"isolation-t2-{ts}",
            "account_id": "test-account-2",
            "from": "sender@tenant2.com",
            "to": ["recipient@example.com"],
            "subject": subject_t2,
            "body": "This should go to tenant 2 SMTP.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [msg1, msg2]})
        assert resp.status_code == 200

        # Trigger dispatch for both tenants
        await trigger_dispatch(api_client, tenant_id="test-tenant-1")
        await trigger_dispatch(api_client, tenant_id="test-tenant-2")

        # Verify isolation - filter by subject to avoid interference from other tests
        msgs_t1 = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        msgs_t2 = await wait_for_messages(MAILHOG_TENANT2_API, 1)

        # Filter for our specific test messages
        our_msgs_t1 = [m for m in msgs_t1 if subject_t1 in m["Content"]["Headers"]["Subject"][0]]
        our_msgs_t2 = [m for m in msgs_t2 if subject_t2 in m["Content"]["Headers"]["Subject"][0]]

        assert len(our_msgs_t1) == 1, f"Tenant 1 should have exactly 1 message with subject '{subject_t1}'"
        assert len(our_msgs_t2) == 1, f"Tenant 2 should have exactly 1 message with subject '{subject_t2}'"

        # Verify cross-tenant isolation - our messages should NOT appear in the wrong mailhog
        wrong_t1 = [m for m in msgs_t1 if subject_t2 in m["Content"]["Headers"]["Subject"][0]]
        wrong_t2 = [m for m in msgs_t2 if subject_t1 in m["Content"]["Headers"]["Subject"][0]]

        assert len(wrong_t1) == 0, "Tenant 2 message should NOT appear in Tenant 1 MailHog"
        assert len(wrong_t2) == 0, "Tenant 1 message should NOT appear in Tenant 2 MailHog"

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
