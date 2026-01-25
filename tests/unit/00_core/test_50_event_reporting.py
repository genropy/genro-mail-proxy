# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Unit tests for event-based reporting system."""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.mail_proxy.core import MailProxy


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
        # Setup account without tenant (uses global callable)
        await proxy.db.add_account({
            "id": "test-account",
            "tenant_id": None,
            "host": "smtp.test.com",
            "port": 587,
            "user": "test",
            "password": "test",
        })
        await proxy.db.insert_messages([{
            "id": "msg-001",
            "account_id": "test-account",
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
