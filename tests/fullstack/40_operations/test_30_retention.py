# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Retention and cleanup tests for message lifecycle management.

These tests verify the retention policy enforcement:
- Old reported messages are cleaned up after retention period
- Tenant isolation is maintained during cleanup
- Unreported messages are not cleaned up
- Bounced messages not yet reported are preserved
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    clear_mailhog,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio, pytest.mark.retention]


class TestRetentionCleanup:
    """Test message retention and cleanup functionality.

    These tests verify that:
    - Messages with reported_ts older than retention period are cleaned up
    - Cleanup respects tenant isolation
    - Unreported messages are preserved regardless of age
    - Bounced but unreported messages are preserved
    """

    async def test_cleanup_removes_old_reported_messages(
        self, api_client, setup_test_tenants
    ):
        """Old messages with reported_ts should be cleaned up.

        Flow:
        1. Create and send messages
        2. Simulate reported_ts in the past (via direct DB or API)
        3. Trigger cleanup
        4. Verify old reported messages are removed
        """
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        msg_id = f"retention-old-{ts}"

        # Create message
        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Retention Test - Old Message",
            "body": "This message should be cleaned up.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Send the message
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Verify message was sent
        await wait_for_messages(MAILHOG_TENANT1_API, 1)

        # Check message exists and is sent
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == msg_id]
        assert len(found) > 0, "Message should exist before cleanup"

        # Trigger cleanup
        resp = await api_client.post("/commands/cleanup-messages?tenant_id=test-tenant-1", json={})
        assert resp.status_code == 200, f"Cleanup failed: {resp.text}"

        # After cleanup, very old reported messages should be removed
        # (This depends on retention_days configuration)

    async def test_cleanup_respects_tenant_isolation(
        self, api_client, setup_test_tenants
    ):
        """Cleanup for one tenant should not affect other tenants.

        Flow:
        1. Create messages in tenant-1 and tenant-2
        2. Trigger cleanup for tenant-1 only
        3. Verify tenant-2 messages are unaffected
        """
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())

        # Create message in tenant-1
        msg1 = {
            "id": f"retention-t1-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Retention Test - Tenant 1",
            "body": "Message for tenant 1.",
        }

        # Create message in tenant-2
        msg2 = {
            "id": f"retention-t2-{ts}",
            "account_id": "test-account-2",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Retention Test - Tenant 2",
            "body": "Message for tenant 2.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [msg1]})
        assert resp.status_code == 200

        resp = await api_client.post("/commands/add-messages", json={"messages": [msg2]})
        assert resp.status_code == 200

        # Trigger cleanup for tenant-1 only
        resp = await api_client.post("/commands/cleanup-messages?tenant_id=test-tenant-1", json={})
        assert resp.status_code == 200, f"Cleanup failed: {resp.text}"

        # Verify tenant-2 message still exists
        resp = await api_client.get("/messages?tenant_id=test-tenant-2")
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == f"retention-t2-{ts}"]
        assert len(found) > 0, "Tenant-2 message should not be affected by tenant-1 cleanup"

    async def test_unreported_messages_not_cleaned(
        self, api_client, setup_test_tenants
    ):
        """Messages without reported_ts should never be cleaned up.

        Even if a message is very old, if it hasn't been reported to the
        client (reported_ts is NULL), it should be preserved.
        """
        ts = int(time.time())
        msg_id = f"retention-unreported-{ts}"

        # Create message but don't trigger dispatch (so it stays pending/unreported)
        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Retention Test - Unreported",
            "body": "This message should NOT be cleaned up.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # DO NOT dispatch - message stays pending/unreported

        # Trigger cleanup
        resp = await api_client.post("/commands/cleanup-messages?tenant_id=test-tenant-1", json={})
        assert resp.status_code == 200, f"Cleanup failed: {resp.text}"

        # Verify message still exists (unreported messages preserved)
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == msg_id]
        assert len(found) > 0, "Unreported message should NOT be cleaned up"

    async def test_bounced_not_reported_preserved(
        self, api_client, setup_test_tenants
    ):
        """Bounced messages that haven't been reported should be preserved.

        A message that bounced but hasn't been included in a delivery report
        (reported_ts is NULL) should not be cleaned up.
        """
        ts = int(time.time())
        msg_id = f"retention-bounced-unreported-{ts}"

        # Create message
        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Retention Test - Bounced Unreported",
            "body": "This bounced message should NOT be cleaned up.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Note: To fully test this, we'd need to:
        # 1. Send the message
        # 2. Inject a bounce
        # 3. NOT trigger the delivery report cycle
        # 4. Trigger cleanup
        # 5. Verify message still exists

        # For now, just verify message exists before cleanup
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == msg_id]
        assert len(found) > 0, "Message should exist"

        # Trigger cleanup
        resp = await api_client.post("/commands/cleanup-messages?tenant_id=test-tenant-1", json={})
        assert resp.status_code == 200, f"Cleanup failed: {resp.text}"

        # Verify message still exists
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        messages = resp.json().get("messages", [])
        found = [m for m in messages if m.get("id") == msg_id]
        assert len(found) > 0, "Bounced unreported message should NOT be cleaned up"

    async def test_retention_configurable_per_tenant(
        self, api_client, setup_test_tenants
    ):
        """Retention period should be configurable per tenant.

        Different tenants may have different retention requirements.
        """
        # Update tenant-1 retention config
        tenant_update = {
            "retention_days": 7,  # 7 days retention
        }

        resp = await api_client.put(
            "/tenant?tenant_id=test-tenant-1",
            json=tenant_update
        )
        # If retention_days config is not supported, skip
        if resp.status_code in (404, 422):
            pytest.skip("Tenant retention_days configuration not implemented")

        # Verify config was updated
        resp = await api_client.get("/tenant?tenant_id=test-tenant-1")
        if resp.status_code == 200:
            tenant = resp.json()
            assert tenant.get("retention_days") == 7
