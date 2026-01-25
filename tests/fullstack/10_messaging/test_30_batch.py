# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    clear_mailhog,
    get_msg_status,
    trigger_dispatch,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestBatchOperations:
    """Test batch message operations."""

    async def test_batch_enqueue(self, api_client, setup_test_tenants):
        """Can enqueue multiple messages in one request."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        messages = []
        for i in range(5):
            messages.append({
                "id": f"batch-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": [f"recipient{i}@example.com"],
                "subject": f"Batch Message {i}",
                "body": f"Batch message content {i}",
            })

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("queued") == 5

        await trigger_dispatch(api_client)

        msgs = await wait_for_messages(MAILHOG_TENANT1_API, 5, timeout=15)
        assert len(msgs) == 5

    async def test_already_sent_rejected(self, api_client, setup_test_tenants):
        """Resubmitting an already-sent message ID should be rejected."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        msg_id = f"already-sent-test-{ts}"

        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Already Sent Test",
            "body": "First message",
        }

        # First send - should be queued and sent
        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200
        assert resp.json().get("queued") == 1

        # Trigger dispatch and wait for message to be sent
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await wait_for_messages(MAILHOG_TENANT1_API, 1, timeout=10)

        # Second send with same ID - should be rejected as "already sent"
        message["body"] = "Updated message"
        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        data = resp.json()
        # Should be rejected because message was already sent (sent_ts IS NOT NULL)
        rejected = data.get("rejected", [])
        queued = data.get("queued", 0)
        assert queued == 0, f"Expected 0 queued (already sent), got {queued}"
        assert any(r.get("reason") == "already sent" for r in rejected), f"Expected 'already sent' rejection, got {rejected}"


# ============================================
# 7. ATTACHMENTS - BASE64
# ============================================


class TestBatchCodeOperations:
    """Test batch_code functionality for message grouping and control."""

    async def test_send_messages_with_batch_code(self, api_client, setup_test_tenants):
        """Messages can be grouped with batch_code."""
        ts = int(time.time())
        batch_code = f"campaign-{ts}"

        messages = [
            {
                "id": f"batch-msg-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": [f"recipient{i}@example.com"],
                "subject": f"Batch Test {i}",
                "body": f"Message {i} in batch.",
                "batch_code": batch_code,
            }
            for i in range(5)
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        # Verify messages were queued
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        assert resp.status_code == 200
        all_msgs = resp.json().get("messages", [])
        batch_msgs = [m for m in all_msgs if m.get("batch_code") == batch_code]
        assert len(batch_msgs) == 5

    async def test_suspend_specific_batch_code(self, api_client, setup_test_tenants):
        """Can suspend messages with a specific batch_code."""
        ts = int(time.time())
        batch_code = f"suspend-batch-{ts}"

        messages = [
            {
                "id": f"suspend-batch-msg-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": [f"recipient{i}@example.com"],
                "subject": f"Suspend Batch Test {i}",
                "body": f"Message {i} to be suspended.",
                "batch_code": batch_code,
            }
            for i in range(3)
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        # Suspend only this batch
        resp = await api_client.post(
            f"/commands/suspend?tenant_id=test-tenant-1&batch_code={batch_code}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        # pending_messages returns count of pending messages for this batch
        assert batch_code in data.get("suspended_batches", [])

    async def test_activate_specific_batch_code(self, api_client, setup_test_tenants):
        """Can activate messages with a specific batch_code."""
        ts = int(time.time())
        batch_code = f"activate-batch-{ts}"

        messages = [
            {
                "id": f"activate-batch-msg-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": [f"recipient{i}@example.com"],
                "subject": f"Activate Batch Test {i}",
                "body": f"Message {i} to be activated.",
                "batch_code": batch_code,
            }
            for i in range(3)
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        # Suspend first
        resp = await api_client.post(
            f"/commands/suspend?tenant_id=test-tenant-1&batch_code={batch_code}"
        )
        assert resp.status_code == 200

        # Then activate
        resp = await api_client.post(
            f"/commands/activate?tenant_id=test-tenant-1&batch_code={batch_code}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        # After activate, the batch should no longer be suspended
        assert batch_code not in data.get("suspended_batches", [])

    async def test_suspend_batch_does_not_affect_others(self, api_client, setup_test_tenants):
        """Suspending one batch_code should not affect other batches."""
        ts = int(time.time())
        batch_a = f"batch-a-{ts}"
        batch_b = f"batch-b-{ts}"

        # Create messages in two different batches
        messages_a = [
            {
                "id": f"batch-a-msg-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": [f"recipient{i}@example.com"],
                "subject": f"Batch A Test {i}",
                "body": f"Message {i} in batch A.",
                "batch_code": batch_a,
            }
            for i in range(2)
        ]

        messages_b = [
            {
                "id": f"batch-b-msg-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": [f"recipient{i}@example.com"],
                "subject": f"Batch B Test {i}",
                "body": f"Message {i} in batch B.",
                "batch_code": batch_b,
            }
            for i in range(2)
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages_a + messages_b})
        assert resp.status_code == 200

        # Suspend only batch A
        resp = await api_client.post(
            f"/commands/suspend?tenant_id=test-tenant-1&batch_code={batch_a}"
        )
        assert resp.status_code == 200

        # Batch B messages should still be sendable
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])

        batch_a_msgs = [m for m in all_msgs if m.get("batch_code") == batch_a]
        batch_b_msgs = [m for m in all_msgs if m.get("batch_code") == batch_b]

        # Batch A should still be pending (not sent because suspended)
        for msg in batch_a_msgs:
            assert get_msg_status(msg) == "pending", \
                f"Suspended batch A message {msg.get('id')} should remain pending, got {get_msg_status(msg)}"

        # Batch B should have been sent (not suspended)
        for msg in batch_b_msgs:
            assert get_msg_status(msg) == "sent", \
                f"Non-suspended batch B message {msg.get('id')} should be sent, got {get_msg_status(msg)}"

    async def test_suspended_batch_messages_not_sent(self, api_client, setup_test_tenants):
        """Suspended batch messages should not be sent even with run-now."""
        ts = int(time.time())
        batch_code = f"no-send-batch-{ts}"

        messages = [
            {
                "id": f"no-send-msg-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": [f"recipient{i}@example.com"],
                "subject": f"No Send Batch Test {i}",
                "body": f"Message {i} should not be sent.",
                "batch_code": batch_code,
            }
            for i in range(2)
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        # Suspend before sending
        resp = await api_client.post(
            f"/commands/suspend?tenant_id=test-tenant-1&batch_code={batch_code}"
        )
        assert resp.status_code == 200

        # Try to send
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Messages should still be pending (not sent because batch is suspended)
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])
        batch_msgs = [m for m in all_msgs if m.get("batch_code") == batch_code]

        for msg in batch_msgs:
            assert get_msg_status(msg) == "pending", \
                f"Suspended batch message {msg.get('id')} should remain pending, got {get_msg_status(msg)}"


# ============================================
# 23. EXTENDED SUSPEND/ACTIVATE TESTS
# ============================================
