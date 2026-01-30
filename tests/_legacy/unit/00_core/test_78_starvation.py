# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Tests for issue #78: tenant starvation prevention and do-not-disturb.

These tests verify:
1. Tenant starvation prevention - tenants without events are called periodically
2. Do Not Disturb - tenants can set next_sync_after to postpone sync calls
3. Run-now with tenant token - resets that tenant's last_sync to force immediate call
"""

from __future__ import annotations

import time

import pytest
import pytest_asyncio

from core.mail_proxy.core import MailProxy
from core.mail_proxy.core.reporting import DEFAULT_SYNC_INTERVAL


@pytest_asyncio.fixture
async def proxy(tmp_path):
    """Create a MailProxy for testing with default tenant."""
    proxy = MailProxy(
        db_path=str(tmp_path / "test.db"),
        test_mode=True,
    )
    await proxy.init()
    yield proxy
    await proxy.stop()


class TestTenantStarvationPrevention:
    """Test that tenants without events are called after sync interval."""

    @pytest.mark.asyncio
    async def test_tenant_without_events_called_after_interval(self, proxy: MailProxy):
        """Tenant without events is called when sync interval expires."""
        # Add tenant with client_base_url
        await proxy.db.table('tenants').add({
            "id": "silent-tenant",
            "name": "Silent Tenant",
            "active": True,
            "client_base_url": "http://example.com",
        })

        # Track calls
        sync_calls = []

        async def mock_send_reports(tenant, payloads):
            sync_calls.append((tenant["id"], payloads))
            return [], 0, None  # acked, queued, next_sync_after

        proxy._send_reports_to_tenant = mock_send_reports
        proxy._active = True

        # First cycle - no events, tenant should be called (no last_sync entry yet)
        await proxy._process_client_cycle()
        assert len(sync_calls) == 1
        assert sync_calls[0][0] == "silent-tenant"
        assert sync_calls[0][1] == []  # Empty payloads

        # Second cycle immediately - should NOT be called (interval not expired)
        sync_calls.clear()
        await proxy._process_client_cycle()
        assert len(sync_calls) == 0

        # Simulate time passing by setting last_sync to past
        proxy._last_sync["silent-tenant"] = time.time() - DEFAULT_SYNC_INTERVAL - 1

        # Third cycle - should be called again
        await proxy._process_client_cycle()
        assert len(sync_calls) == 1
        assert sync_calls[0][0] == "silent-tenant"

    @pytest.mark.asyncio
    async def test_active_tenant_does_not_block_others(self, proxy: MailProxy):
        """Active tenant with events doesn't starve other tenants."""
        # Add two tenants
        await proxy.db.table('tenants').add({
            "id": "active-tenant",
            "name": "Active Tenant",
            "active": True,
            "client_base_url": "http://active.example.com",
        })
        await proxy.db.table('tenants').add({
            "id": "silent-tenant",
            "name": "Silent Tenant",
            "active": True,
            "client_base_url": "http://silent.example.com",
        })

        # Add message for active-tenant
        inserted = await proxy.db.table('messages').insert_batch([{
            "id": "msg-active",
            "tenant_id": "active-tenant",
            "account_id": None,
            "payload": {"from": "sender@example.com", "to": ["dest@example.com"], "subject": "Test"},
        }])
        msg_pk = inserted[0]["pk"]

        # Add event for active-tenant
        await proxy.db.table('message_events').add_event(
            message_pk=msg_pk,
            event_type="sent",
            event_ts=int(time.time()),
        )

        # Track calls
        sync_calls = []

        async def mock_send_reports(tenant, payloads):
            sync_calls.append((tenant["id"], payloads))
            return [p["id"] for p in payloads], 0, None

        proxy._send_reports_to_tenant = mock_send_reports
        proxy._active = True

        # First cycle - both tenants should be called
        await proxy._process_client_cycle()

        tenant_ids_called = [call[0] for call in sync_calls]
        assert "active-tenant" in tenant_ids_called
        assert "silent-tenant" in tenant_ids_called


class TestDoNotDisturb:
    """Test that next_sync_after postpones sync calls."""

    @pytest.mark.asyncio
    async def test_dnd_skips_tenant_until_time(self, proxy: MailProxy):
        """Tenant with future next_sync_after is skipped until time passes."""
        await proxy.db.table('tenants').add({
            "id": "dnd-tenant",
            "name": "DND Tenant",
            "active": True,
            "client_base_url": "http://example.com",
        })

        call_count = 0

        async def mock_send_reports(tenant, payloads):
            nonlocal call_count
            call_count += 1
            # Return next_sync_after 1 hour in the future
            return [], 0, time.time() + 3600

        proxy._send_reports_to_tenant = mock_send_reports
        proxy._active = True

        # First cycle - tenant is called
        await proxy._process_client_cycle()
        assert call_count == 1

        # Second cycle immediately - should NOT be called (DND active)
        await proxy._process_client_cycle()
        assert call_count == 1  # Still 1, not called again

        # Simulate time passing by setting last_sync to past the DND period
        proxy._last_sync["dnd-tenant"] = time.time() - 3601

        # Third cycle - should be called again
        await proxy._process_client_cycle()
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_dnd_tenant_with_events_still_called(self, proxy: MailProxy):
        """Tenant in DND mode is still called if there are events to report."""
        await proxy.db.table('tenants').add({
            "id": "dnd-tenant",
            "name": "DND Tenant",
            "active": True,
            "client_base_url": "http://example.com",
        })

        # Set DND (future timestamp)
        proxy._last_sync["dnd-tenant"] = time.time() + 3600

        # Add message and event
        inserted = await proxy.db.table('messages').insert_batch([{
            "id": "msg-dnd",
            "tenant_id": "dnd-tenant",
            "account_id": None,
            "payload": {"from": "sender@example.com", "to": ["dest@example.com"], "subject": "Test"},
        }])
        msg_pk = inserted[0]["pk"]

        await proxy.db.table('message_events').add_event(
            message_pk=msg_pk,
            event_type="sent",
            event_ts=int(time.time()),
        )

        call_count = 0

        async def mock_send_reports(tenant, payloads):
            nonlocal call_count
            call_count += 1
            return [p["id"] for p in payloads], 0, None

        proxy._send_reports_to_tenant = mock_send_reports
        proxy._active = True

        # Cycle - tenant should be called because there are events
        await proxy._process_client_cycle()
        assert call_count == 1


class TestRunNowWithTenantToken:
    """Test that run-now with tenant_id resets that tenant's last_sync."""

    @pytest.mark.asyncio
    async def test_run_now_resets_last_sync(self, proxy: MailProxy):
        """run-now with tenant_id resets that tenant's last_sync to 0."""
        await proxy.db.table('tenants').add({
            "id": "urgent-tenant",
            "name": "Urgent Tenant",
            "active": True,
            "client_base_url": "http://example.com",
        })

        # Set DND (future timestamp)
        proxy._last_sync["urgent-tenant"] = time.time() + 3600

        call_count = 0

        async def mock_send_reports(tenant, payloads):
            nonlocal call_count
            call_count += 1
            return [], 0, None

        proxy._send_reports_to_tenant = mock_send_reports
        proxy._active = True

        # Normal cycle - tenant in DND, should NOT be called
        await proxy._process_client_cycle()
        assert call_count == 0

        # Call run-now with tenant_id
        result = await proxy.handle_command("run now", {"tenant_id": "urgent-tenant"})
        assert result["ok"] is True

        # Verify last_sync was reset
        assert proxy._last_sync["urgent-tenant"] == 0
        assert proxy._run_now_tenant_id == "urgent-tenant"

    @pytest.mark.asyncio
    async def test_run_now_without_tenant_does_not_reset(self, proxy: MailProxy):
        """run-now without tenant_id does not reset any specific tenant's DND."""
        await proxy.db.table('tenants').add({
            "id": "dnd-tenant",
            "name": "DND Tenant",
            "active": True,
            "client_base_url": "http://example.com",
        })

        # Set DND (future timestamp)
        future_time = time.time() + 3600
        proxy._last_sync["dnd-tenant"] = future_time

        # Call run-now without tenant_id (admin)
        result = await proxy.handle_command("run now", {})
        assert result["ok"] is True

        # Verify last_sync was NOT reset
        assert proxy._last_sync["dnd-tenant"] == future_time

    @pytest.mark.asyncio
    async def test_run_now_tenant_id_targets_specific_tenant(self, proxy: MailProxy):
        """run-now with tenant_id sets _run_now_tenant_id for targeting."""
        await proxy.db.table('tenants').add({
            "id": "target-tenant",
            "name": "Target Tenant",
            "active": True,
            "client_base_url": "http://target.example.com",
        })
        await proxy.db.table('tenants').add({
            "id": "other-tenant",
            "name": "Other Tenant",
            "active": True,
            "client_base_url": "http://other.example.com",
        })

        calls = []

        async def mock_send_reports(tenant, payloads):
            calls.append(tenant["id"])
            return [], 0, None

        proxy._send_reports_to_tenant = mock_send_reports
        proxy._active = True

        # Reset both tenants to past interval so they would be called
        past_time = time.time() - DEFAULT_SYNC_INTERVAL - 1
        proxy._last_sync["target-tenant"] = past_time
        proxy._last_sync["other-tenant"] = past_time

        # Call run-now with specific tenant_id
        await proxy.handle_command("run now", {"tenant_id": "target-tenant"})

        # Process cycle with target_tenant_id set
        await proxy._process_client_cycle()

        # Only target-tenant should be called (due to _run_now_tenant_id filter)
        assert "target-tenant" in calls
        assert "other-tenant" not in calls


class TestListTenantsSyncStatus:
    """Test the listTenantsSyncStatus command."""

    @pytest.mark.asyncio
    async def test_sync_status_returns_all_tenants(self, proxy: MailProxy):
        """listTenantsSyncStatus returns status for all tenants."""
        # Add two tenants
        await proxy.db.table('tenants').add({
            "id": "tenant-a",
            "name": "Tenant A",
            "active": True,
            "client_base_url": "http://a.example.com",
        })
        await proxy.db.table('tenants').add({
            "id": "tenant-b",
            "name": "Tenant B",
            "active": False,
            "client_base_url": "http://b.example.com",
        })

        result = await proxy.handle_command("listTenantsSyncStatus", {})

        assert result["ok"] is True
        assert "tenants" in result
        assert len(result["tenants"]) == 2
        assert result["sync_interval_seconds"] == DEFAULT_SYNC_INTERVAL

        # Check tenant-a
        tenant_a = next(t for t in result["tenants"] if t["id"] == "tenant-a")
        assert tenant_a["name"] == "Tenant A"
        assert tenant_a["active"] is True
        assert tenant_a["client_base_url"] == "http://a.example.com"
        assert tenant_a["last_sync_ts"] is None  # Never synced
        assert tenant_a["next_sync_due"] is True  # Due because never synced
        assert tenant_a["in_dnd"] is False

        # Check tenant-b
        tenant_b = next(t for t in result["tenants"] if t["id"] == "tenant-b")
        assert tenant_b["name"] == "Tenant B"
        assert tenant_b["active"] is False

    @pytest.mark.asyncio
    async def test_sync_status_shows_last_sync_time(self, proxy: MailProxy):
        """listTenantsSyncStatus shows last sync timestamp."""
        await proxy.db.table('tenants').add({
            "id": "synced-tenant",
            "name": "Synced Tenant",
            "active": True,
            "client_base_url": "http://example.com",
        })

        # Simulate a recent sync
        now = time.time()
        proxy._last_sync["synced-tenant"] = now

        result = await proxy.handle_command("listTenantsSyncStatus", {})

        tenant = result["tenants"][0]
        assert tenant["last_sync_ts"] == now
        assert tenant["next_sync_due"] is False  # Recently synced
        assert tenant["in_dnd"] is False

    @pytest.mark.asyncio
    async def test_sync_status_shows_dnd_mode(self, proxy: MailProxy):
        """listTenantsSyncStatus detects Do Not Disturb mode."""
        await proxy.db.table('tenants').add({
            "id": "dnd-tenant",
            "name": "DND Tenant",
            "active": True,
            "client_base_url": "http://example.com",
        })

        # Set DND (future timestamp)
        future_time = time.time() + 3600
        proxy._last_sync["dnd-tenant"] = future_time

        result = await proxy.handle_command("listTenantsSyncStatus", {})

        tenant = result["tenants"][0]
        assert tenant["last_sync_ts"] == future_time
        assert tenant["next_sync_due"] is False  # Not due (in DND)
        assert tenant["in_dnd"] is True

    @pytest.mark.asyncio
    async def test_sync_status_shows_sync_due(self, proxy: MailProxy):
        """listTenantsSyncStatus detects when sync is due."""
        await proxy.db.table('tenants').add({
            "id": "overdue-tenant",
            "name": "Overdue Tenant",
            "active": True,
            "client_base_url": "http://example.com",
        })

        # Simulate old sync (past the interval)
        old_time = time.time() - DEFAULT_SYNC_INTERVAL - 100
        proxy._last_sync["overdue-tenant"] = old_time

        result = await proxy.handle_command("listTenantsSyncStatus", {})

        tenant = result["tenants"][0]
        assert tenant["last_sync_ts"] == old_time
        assert tenant["next_sync_due"] is True  # Due because interval expired
        assert tenant["in_dnd"] is False
