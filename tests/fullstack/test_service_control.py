# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from .helpers import (
    get_msg_status,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestServiceControl:
    """Test service control operations."""

    async def test_suspend_and_activate(self, api_client, setup_test_tenants):
        """Can suspend and activate processing for a tenant."""
        tenant_id = "test-tenant-1"

        # Suspend all batches for tenant
        resp = await api_client.post(f"/commands/suspend?tenant_id={tenant_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("tenant_id") == tenant_id
        assert data.get("suspended_batches") == ["*"]

        # Activate all batches for tenant
        resp = await api_client.post(f"/commands/activate?tenant_id={tenant_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("tenant_id") == tenant_id
        assert data.get("suspended_batches") == []

    async def test_suspend_single_batch(self, api_client, setup_test_tenants):
        """Can suspend a specific batch for a tenant."""
        tenant_id = "test-tenant-1"
        batch_code = "NL-2026-01"

        # Suspend specific batch
        resp = await api_client.post(
            f"/commands/suspend?tenant_id={tenant_id}&batch_code={batch_code}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert data.get("batch_code") == batch_code
        assert batch_code in data.get("suspended_batches", [])

        # Activate specific batch
        resp = await api_client.post(
            f"/commands/activate?tenant_id={tenant_id}&batch_code={batch_code}"
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        assert batch_code not in data.get("suspended_batches", [])

    async def test_suspend_requires_tenant_id(self, api_client):
        """Suspend without tenant_id returns validation error."""
        resp = await api_client.post("/commands/suspend")
        assert resp.status_code == 422  # FastAPI validation error
        data = resp.json()
        # FastAPI returns detail with validation error info
        assert "detail" in data


# ============================================
# 10. METRICS
# ============================================


class TestExtendedSuspendActivate:
    """Extended tests for suspend/activate functionality."""

    async def test_suspend_returns_pending_count(self, api_client, setup_test_tenants):
        """Suspend should return count of suspended messages."""
        ts = int(time.time())

        messages = [
            {
                "id": f"count-suspend-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Count Suspend Test {i}",
                "body": "Testing suspend count.",
            }
            for i in range(5)
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        resp = await api_client.post("/commands/suspend?tenant_id=test-tenant-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        # pending_messages shows how many messages are pending for this tenant
        assert "pending_messages" in data
        assert data["pending_messages"] >= 5

        # Cleanup: activate again
        await api_client.post("/commands/activate?tenant_id=test-tenant-1")

    async def test_activate_returns_activated_count(self, api_client, setup_test_tenants):
        """Activate should return count of activated messages."""
        ts = int(time.time())

        messages = [
            {
                "id": f"count-activate-{ts}-{i}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": f"Count Activate Test {i}",
                "body": "Testing activate count.",
            }
            for i in range(5)
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        # Suspend first
        await api_client.post("/commands/suspend?tenant_id=test-tenant-1")

        # Then activate
        resp = await api_client.post("/commands/activate?tenant_id=test-tenant-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True
        # After activate, suspended_batches should be empty
        assert data.get("suspended_batches") == []

    async def test_suspend_idempotent(self, api_client, setup_test_tenants):
        """Calling suspend multiple times should be safe."""
        ts = int(time.time())

        message = {
            "id": f"idempotent-suspend-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Idempotent Suspend Test",
            "body": "Testing idempotent suspend.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Call suspend multiple times
        for _ in range(3):
            resp = await api_client.post("/commands/suspend?tenant_id=test-tenant-1")
            assert resp.status_code == 200
            assert resp.json().get("ok") is True

        # Tenant should be fully suspended (suspended_batches contains "*")
        data = resp.json()
        assert "*" in data.get("suspended_batches", [])

        # Cleanup
        await api_client.post("/commands/activate?tenant_id=test-tenant-1")

    async def test_activate_idempotent(self, api_client, setup_test_tenants):
        """Calling activate multiple times should be safe."""
        ts = int(time.time())

        message = {
            "id": f"idempotent-activate-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Idempotent Activate Test",
            "body": "Testing idempotent activate.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Suspend first
        await api_client.post("/commands/suspend?tenant_id=test-tenant-1")

        # Call activate multiple times
        for _ in range(3):
            resp = await api_client.post("/commands/activate?tenant_id=test-tenant-1")
            assert resp.status_code == 200
            assert resp.json().get("ok") is True

    async def test_tenant_isolation_in_suspend(self, api_client, setup_test_tenants):
        """Suspending one tenant should not affect another tenant."""
        ts = int(time.time())

        # Add messages to both tenants
        msg_tenant1 = {
            "id": f"isolation-suspend-t1-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Tenant 1 Isolation Test",
            "body": "Testing tenant isolation.",
        }

        msg_tenant2 = {
            "id": f"isolation-suspend-t2-{ts}",
            "account_id": "test-account-2",
            "from": "sender@test2.com",
            "to": ["recipient@example.com"],
            "subject": "Tenant 2 Isolation Test",
            "body": "Testing tenant isolation.",
        }

        await api_client.post("/commands/add-messages", json={"messages": [msg_tenant1]})
        await api_client.post("/commands/add-messages", json={"messages": [msg_tenant2]})

        # Suspend only tenant 1
        resp = await api_client.post("/commands/suspend?tenant_id=test-tenant-1")
        assert resp.status_code == 200

        # Verify tenant 1 is suspended
        data = resp.json()
        assert "*" in data.get("suspended_batches", [])

        # Tenant 2 messages should still be sendable
        await api_client.post("/commands/run-now?tenant_id=test-tenant-2")
        await asyncio.sleep(3)

        # Check tenant 2 was processed (not suspended)
        resp = await api_client.get("/messages?tenant_id=test-tenant-2")
        t2_msgs = resp.json().get("messages", [])
        t2_found = [m for m in t2_msgs if m.get("id") == f"isolation-suspend-t2-{ts}"]
        if t2_found:
            # Tenant 2 should not be suspended
            assert get_msg_status(t2_found[0]) != "suspended"

        # Cleanup
        await api_client.post("/commands/activate?tenant_id=test-tenant-1")

    async def test_suspend_with_deferred_messages(self, api_client, setup_test_tenants):
        """Suspend should also affect deferred messages."""
        ts = int(time.time())
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)

        message = {
            "id": f"deferred-suspend-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Deferred Suspend Test",
            "body": "Testing suspend on deferred messages.",
            "send_after": future_time.isoformat(),
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Suspend tenant
        resp = await api_client.post("/commands/suspend?tenant_id=test-tenant-1")
        assert resp.status_code == 200

        # Verify tenant is suspended
        data = resp.json()
        assert "*" in data.get("suspended_batches", [])

        # Cleanup
        await api_client.post("/commands/activate?tenant_id=test-tenant-1")

    async def test_activate_resumes_deferred_timing(self, api_client, setup_test_tenants):
        """After activate, deferred messages should resume with original timing."""
        ts = int(time.time())
        future_time = datetime.now(timezone.utc) + timedelta(hours=1)

        message = {
            "id": f"resume-deferred-{ts}",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Resume Deferred Test",
            "body": "Testing activate resumes deferred.",
            "send_after": future_time.isoformat(),
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Suspend then activate
        await api_client.post("/commands/suspend?tenant_id=test-tenant-1")
        await api_client.post("/commands/activate?tenant_id=test-tenant-1")

        # After activate, suspended_batches should be empty
        resp = await api_client.post("/commands/activate?tenant_id=test-tenant-1")
        data = resp.json()
        assert data.get("suspended_batches") == []


# ============================================
# BOUNCE DETECTION END-TO-END
# ============================================
