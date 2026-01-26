# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Unit tests for event-based reporting system."""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.mail_proxy.core import MailProxy
# Import mixin directly to ensure coverage tracks it
from src.mail_proxy.core.event_reporting import EventReporterMixin  # noqa: F401


@pytest_asyncio.fixture
async def proxy(tmp_path):
    """Create a MailProxy for testing."""
    proxy = MailProxy(
        db_path=str(tmp_path / "test.db"),
        test_mode=True,
    )
    await proxy.init()
    yield proxy
    await proxy.stop()


class TestEventsToPayloads:
    """Test _events_to_payloads conversion."""

    @pytest.mark.asyncio
    async def test_sent_event_to_payload(self, proxy: MailProxy):
        """Sent event should produce payload with sent_ts."""
        events = [
            {
                "event_id": 1,
                "message_id": "msg-001",
                "event_type": "sent",
                "event_ts": 1700000000,
                "description": None,
                "metadata": None,
            }
        ]
        payloads = proxy._events_to_payloads(events)

        assert len(payloads) == 1
        assert payloads[0]["id"] == "msg-001"
        assert payloads[0]["sent_ts"] == 1700000000

    @pytest.mark.asyncio
    async def test_error_event_to_payload(self, proxy: MailProxy):
        """Error event should produce payload with error_ts and error."""
        events = [
            {
                "event_id": 2,
                "message_id": "msg-002",
                "event_type": "error",
                "event_ts": 1700000100,
                "description": "Connection refused",
                "metadata": None,
            }
        ]
        payloads = proxy._events_to_payloads(events)

        assert len(payloads) == 1
        assert payloads[0]["id"] == "msg-002"
        assert payloads[0]["error_ts"] == 1700000100
        assert payloads[0]["error"] == "Connection refused"

    @pytest.mark.asyncio
    async def test_deferred_event_to_payload(self, proxy: MailProxy):
        """Deferred event should produce payload with deferred_ts and reason."""
        events = [
            {
                "event_id": 3,
                "message_id": "msg-003",
                "event_type": "deferred",
                "event_ts": 1700000200,
                "description": "Rate limit exceeded",
                "metadata": None,
            }
        ]
        payloads = proxy._events_to_payloads(events)

        assert len(payloads) == 1
        assert payloads[0]["id"] == "msg-003"
        assert payloads[0]["deferred_ts"] == 1700000200
        assert payloads[0]["deferred_reason"] == "Rate limit exceeded"

    @pytest.mark.asyncio
    async def test_bounce_event_to_payload(self, proxy: MailProxy):
        """Bounce event should produce payload with bounce fields from metadata."""
        events = [
            {
                "event_id": 4,
                "message_id": "msg-004",
                "event_type": "bounce",
                "event_ts": 1700000300,
                "description": "User unknown",
                "metadata": {"bounce_type": "hard", "bounce_code": "550"},
            }
        ]
        payloads = proxy._events_to_payloads(events)

        assert len(payloads) == 1
        assert payloads[0]["id"] == "msg-004"
        assert payloads[0]["bounce_ts"] == 1700000300
        assert payloads[0]["bounce_type"] == "hard"
        assert payloads[0]["bounce_code"] == "550"
        assert payloads[0]["bounce_reason"] == "User unknown"

    @pytest.mark.asyncio
    async def test_pec_event_to_payload(self, proxy: MailProxy):
        """PEC events should produce payload with pec_event and pec_ts."""
        events = [
            {
                "event_id": 5,
                "message_id": "msg-005",
                "event_type": "pec_acceptance",
                "event_ts": 1700000400,
                "description": "Accepted by PEC server",
                "metadata": None,
            }
        ]
        payloads = proxy._events_to_payloads(events)

        assert len(payloads) == 1
        assert payloads[0]["id"] == "msg-005"
        assert payloads[0]["pec_event"] == "pec_acceptance"
        assert payloads[0]["pec_ts"] == 1700000400
        assert payloads[0]["pec_details"] == "Accepted by PEC server"

    @pytest.mark.asyncio
    async def test_multiple_events_to_payloads(self, proxy: MailProxy):
        """Multiple events should produce multiple payloads."""
        events = [
            {
                "event_id": 1,
                "message_id": "msg-001",
                "event_type": "sent",
                "event_ts": 1700000000,
                "description": None,
                "metadata": None,
            },
            {
                "event_id": 2,
                "message_id": "msg-002",
                "event_type": "error",
                "event_ts": 1700000100,
                "description": "Failed",
                "metadata": None,
            },
        ]
        payloads = proxy._events_to_payloads(events)

        assert len(payloads) == 2
        assert payloads[0]["id"] == "msg-001"
        assert "sent_ts" in payloads[0]
        assert payloads[1]["id"] == "msg-002"
        assert "error_ts" in payloads[1]


class TestEventReportingCycle:
    """Test _process_client_cycle method."""

    @pytest.mark.asyncio
    async def test_cycle_returns_zero_when_inactive(self, proxy: MailProxy):
        """Cycle should return 0 when proxy is not active."""
        proxy._active = False
        result = await proxy._process_client_cycle()
        assert result == 0

    @pytest.mark.asyncio
    async def test_cycle_with_no_events(self, proxy: MailProxy):
        """Cycle should handle case with no unreported events."""
        proxy._active = True
        result = await proxy._process_client_cycle()
        assert result == 0

    @pytest.mark.asyncio
    async def test_cycle_processes_events_with_callable(self, proxy: MailProxy):
        """Cycle should process events and call report_delivery_callable."""
        # Insert message without account_id so it uses global callable
        await proxy.db.insert_messages([{
            "id": "msg-001",
            "account_id": None,
            "payload": {"from": "a@test.com", "to": ["b@test.com"], "subject": "Test"},
        }])

        # Create a sent event
        await proxy.db.add_event("msg-001", "sent", 1700000000)

        # Track reported payloads
        reported_payloads = []

        async def track_reports(payload):
            reported_payloads.append(payload)

        proxy._report_delivery_callable = track_reports
        proxy._active = True

        result = await proxy._process_client_cycle()

        # Should have called the callable with the payload
        assert len(reported_payloads) == 1
        assert reported_payloads[0]["id"] == "msg-001"
        assert reported_payloads[0]["sent_ts"] == 1700000000

        # Event should be marked as reported
        events = await proxy.db.fetch_unreported_events(limit=10)
        assert len(events) == 0


class TestApplyRetention:
    """Test _apply_retention method."""

    @pytest.mark.asyncio
    async def test_retention_disabled_when_zero(self, proxy: MailProxy):
        """Retention should not run when configured to 0."""
        proxy._report_retention_seconds = 0
        # This should return immediately without doing anything
        await proxy._apply_retention()
        # No error means success

    @pytest.mark.asyncio
    async def test_retention_disabled_when_negative(self, proxy: MailProxy):
        """Retention should not run when configured to negative."""
        proxy._report_retention_seconds = -1
        # This should return immediately without doing anything
        await proxy._apply_retention()
        # No error means success

    @pytest.mark.asyncio
    async def test_retention_positive_calls_db(self, proxy: MailProxy):
        """Retention with positive value should attempt cleanup."""
        proxy._report_retention_seconds = 3600  # 1 hour

        # The actual method will be called but won't do anything
        # since there are no reported messages. Success means no error.
        await proxy._apply_retention()
        # No error means success


class TestWaitForClientWakeup:
    """Test _wait_for_client_wakeup method."""

    @pytest.mark.asyncio
    async def test_returns_immediately_when_stop_is_set(self, proxy: MailProxy):
        """Should return immediately when stop event is set."""
        proxy._stop.set()
        # Should not hang
        await proxy._wait_for_client_wakeup(10.0)

    @pytest.mark.asyncio
    async def test_returns_on_zero_timeout(self, proxy: MailProxy):
        """Should return immediately on zero timeout."""
        import asyncio
        start = asyncio.get_event_loop().time()
        await proxy._wait_for_client_wakeup(0)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed < 0.1  # Should be nearly instant

    @pytest.mark.asyncio
    async def test_waits_for_wake_event_on_none_timeout(self, proxy: MailProxy):
        """Should wait for wake event when timeout is None."""
        import asyncio

        async def wake_after_delay():
            await asyncio.sleep(0.05)
            proxy._wake_client_event.set()

        task = asyncio.create_task(wake_after_delay())
        await proxy._wait_for_client_wakeup(None)
        await task
        # Event should be cleared
        assert not proxy._wake_client_event.is_set()

    @pytest.mark.asyncio
    async def test_waits_for_wake_event_on_inf_timeout(self, proxy: MailProxy):
        """Should wait for wake event when timeout is inf."""
        import asyncio
        import math

        async def wake_after_delay():
            await asyncio.sleep(0.05)
            proxy._wake_client_event.set()

        task = asyncio.create_task(wake_after_delay())
        await proxy._wait_for_client_wakeup(math.inf)
        await task
        assert not proxy._wake_client_event.is_set()

    @pytest.mark.asyncio
    async def test_timeout_returns_without_event(self, proxy: MailProxy):
        """Should return after timeout even if event not set."""
        import asyncio
        start = asyncio.get_event_loop().time()
        await proxy._wait_for_client_wakeup(0.1)
        elapsed = asyncio.get_event_loop().time() - start
        # Should have waited approximately 0.1 seconds
        assert 0.08 < elapsed < 0.3


class TestSyncTenantsWithoutReports:
    """Test _sync_tenants_without_reports method."""

    @pytest.mark.asyncio
    async def test_sync_specific_tenant(self, proxy: MailProxy):
        """Should sync specific tenant when target_tenant_id provided."""
        # Add tenant with client_base_url (used by get_tenant_sync_url)
        await proxy.db.add_tenant({
            "id": "tenant-1",
            "name": "Test Tenant",
            "active": True,
            "client_base_url": "http://example.com",
        })

        # Track calls
        sync_calls = []

        async def mock_send_reports(tenant, payloads):
            sync_calls.append((tenant["id"], payloads))
            return [], 0

        proxy._send_reports_to_tenant = mock_send_reports

        result = await proxy._sync_tenants_without_reports("tenant-1")

        assert result == 0
        assert len(sync_calls) == 1
        assert sync_calls[0][0] == "tenant-1"
        assert sync_calls[0][1] == []  # Empty payloads

    @pytest.mark.asyncio
    async def test_sync_all_tenants(self, proxy: MailProxy):
        """Should sync all active tenants when no target specified."""
        # Add multiple tenants with client_base_url
        await proxy.db.add_tenant({
            "id": "tenant-1",
            "name": "Tenant 1",
            "active": True,
            "client_base_url": "http://example1.com",
        })
        await proxy.db.add_tenant({
            "id": "tenant-2",
            "name": "Tenant 2",
            "active": True,
            "client_base_url": "http://example2.com",
        })

        sync_calls = []

        async def mock_send_reports(tenant, payloads):
            sync_calls.append((tenant["id"], payloads))
            return [], 5  # Return 5 queued messages

        proxy._send_reports_to_tenant = mock_send_reports
        proxy._client_sync_url = None  # No global URL

        result = await proxy._sync_tenants_without_reports(None)

        assert result == 10  # 5 + 5 from two tenants
        assert len(sync_calls) == 2

    @pytest.mark.asyncio
    async def test_sync_with_global_url(self, proxy: MailProxy):
        """Should sync global URL when no tenants."""
        sync_calls = []

        async def mock_send_delivery_reports(payloads):
            sync_calls.append(payloads)
            return [], 3

        proxy._send_delivery_reports = mock_send_delivery_reports
        proxy._client_sync_url = "http://global.example.com/sync"
        proxy._report_delivery_callable = None

        result = await proxy._sync_tenants_without_reports(None)

        assert result == 3
        assert len(sync_calls) == 1


class TestEventsToPayloadsEdgeCases:
    """Test edge cases for _events_to_payloads."""

    @pytest.mark.asyncio
    async def test_pec_event_without_description(self, proxy: MailProxy):
        """PEC event without description should not have pec_details."""
        events = [
            {
                "event_id": 10,
                "message_id": "msg-pec",
                "event_type": "pec_delivery",
                "event_ts": 1700000500,
                "description": None,
                "metadata": None,
            }
        ]
        payloads = proxy._events_to_payloads(events)

        assert len(payloads) == 1
        assert payloads[0]["pec_event"] == "pec_delivery"
        assert payloads[0]["pec_ts"] == 1700000500
        assert "pec_details" not in payloads[0]

    @pytest.mark.asyncio
    async def test_bounce_without_metadata(self, proxy: MailProxy):
        """Bounce event without metadata should have None values."""
        events = [
            {
                "event_id": 11,
                "message_id": "msg-bounce",
                "event_type": "bounce",
                "event_ts": 1700000600,
                "description": "Delivery failed",
                "metadata": None,
            }
        ]
        payloads = proxy._events_to_payloads(events)

        assert len(payloads) == 1
        assert payloads[0]["bounce_ts"] == 1700000600
        assert payloads[0]["bounce_type"] is None
        assert payloads[0]["bounce_code"] is None
        assert payloads[0]["bounce_reason"] == "Delivery failed"

    @pytest.mark.asyncio
    async def test_empty_events_list(self, proxy: MailProxy):
        """Empty events list should return empty payloads."""
        payloads = proxy._events_to_payloads([])
        assert payloads == []


class TestProcessClientCycleWithTenants:
    """Test _process_client_cycle with tenant-specific scenarios."""

    @pytest.mark.asyncio
    async def test_cycle_groups_events_by_tenant(self, proxy: MailProxy):
        """Events should be grouped and sent to correct tenant endpoints."""
        # Setup two tenants with client_base_url
        await proxy.db.add_tenant({
            "id": "tenant-a",
            "name": "Tenant A",
            "active": True,
            "client_base_url": "http://a.example.com",
        })
        await proxy.db.add_tenant({
            "id": "tenant-b",
            "name": "Tenant B",
            "active": True,
            "client_base_url": "http://b.example.com",
        })

        # Create accounts for each tenant
        await proxy.db.add_account({
            "id": "account-a",
            "tenant_id": "tenant-a",
            "host": "smtp.a.com",
            "port": 587,
            "user": "test",
            "password": "test",
        })
        await proxy.db.add_account({
            "id": "account-b",
            "tenant_id": "tenant-b",
            "host": "smtp.b.com",
            "port": 587,
            "user": "test",
            "password": "test",
        })

        # Insert messages
        await proxy.db.insert_messages([
            {
                "id": "msg-a1",
                "account_id": "account-a",
                "payload": {"from": "a@test.com", "to": ["b@test.com"], "subject": "A1"},
            },
            {
                "id": "msg-b1",
                "account_id": "account-b",
                "payload": {"from": "x@test.com", "to": ["y@test.com"], "subject": "B1"},
            },
        ])

        # Create events
        await proxy.db.add_event("msg-a1", "sent", 1700000000)
        await proxy.db.add_event("msg-b1", "sent", 1700000100)

        tenant_calls = {}

        async def mock_send_to_tenant(tenant, payloads):
            tenant_calls[tenant["id"]] = payloads
            return [p["id"] for p in payloads], 0

        proxy._send_reports_to_tenant = mock_send_to_tenant
        proxy._active = True

        await proxy._process_client_cycle()

        # Each tenant should receive their own events
        assert "tenant-a" in tenant_calls
        assert "tenant-b" in tenant_calls
        assert len(tenant_calls["tenant-a"]) == 1
        assert tenant_calls["tenant-a"][0]["id"] == "msg-a1"
        assert len(tenant_calls["tenant-b"]) == 1
        assert tenant_calls["tenant-b"][0]["id"] == "msg-b1"

    @pytest.mark.asyncio
    async def test_cycle_warns_on_no_sync_url(self, proxy: MailProxy):
        """Should log warning when tenant has no sync URL."""
        # Add tenant without client_base_url
        await proxy.db.add_tenant({
            "id": "tenant-no-url",
            "name": "No URL Tenant",
            "active": True,
            "client_base_url": None,
        })
        await proxy.db.add_account({
            "id": "account-no-url",
            "tenant_id": "tenant-no-url",
            "host": "smtp.test.com",
            "port": 587,
            "user": "test",
            "password": "test",
        })
        await proxy.db.insert_messages([{
            "id": "msg-no-url",
            "account_id": "account-no-url",
            "payload": {"from": "a@test.com", "to": ["b@test.com"], "subject": "Test"},
        }])
        await proxy.db.add_event("msg-no-url", "sent", 1700000000)

        proxy._active = True
        proxy._client_sync_url = None  # No fallback

        # Should not raise, but event remains unreported
        result = await proxy._process_client_cycle()
        assert result == 0

        # Event should still be unreported
        events = await proxy.db.fetch_unreported_events(limit=10)
        assert len(events) == 1
