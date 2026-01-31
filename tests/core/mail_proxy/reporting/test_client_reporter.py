# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for ClientReporter."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.mail_proxy.reporting.client_reporter import (
    ClientReporter,
    DEFAULT_SYNC_INTERVAL,
)


class MockProxy:
    """Mock proxy for ClientReporter tests."""

    def __init__(self):
        self.db = MagicMock()
        self._tables = {
            "messages": MagicMock(),
            "tenants": MagicMock(),
            "message_events": MagicMock(),
        }
        self.db.table = MagicMock(side_effect=self._get_table)
        self.logger = MagicMock()
        self.metrics = MagicMock()
        self._test_mode = True
        self._active = True
        self._smtp_batch_size = 10
        self._report_retention_seconds = 3600
        self._client_sync_url = None
        self._client_sync_token = None
        self._client_sync_user = None
        self._client_sync_password = None
        self._report_delivery_callable = None
        self._log_delivery_activity = True
        self._refresh_queue_gauge = AsyncMock()

    def _get_table(self, name):
        if name not in self._tables:
            self._tables[name] = MagicMock()
        return self._tables[name]


class TestClientReporterInit:
    """Tests for ClientReporter initialization."""

    def test_init_creates_control_events(self):
        """Init creates stop and wake events."""
        proxy = MockProxy()
        reporter = ClientReporter(proxy)
        assert isinstance(reporter._stop, asyncio.Event)
        assert isinstance(reporter._wake_event, asyncio.Event)

    def test_init_stores_proxy_reference(self):
        """Init stores reference to proxy."""
        proxy = MockProxy()
        reporter = ClientReporter(proxy)
        assert reporter.proxy is proxy

    def test_init_empty_last_sync(self):
        """Init starts with empty last_sync dict."""
        proxy = MockProxy()
        reporter = ClientReporter(proxy)
        assert reporter._last_sync == {}


class TestClientReporterProperties:
    """Tests for ClientReporter properties."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    def test_db_delegates_to_proxy(self, reporter):
        """db property delegates to proxy."""
        assert reporter.db is reporter.proxy.db

    def test_logger_delegates_to_proxy(self, reporter):
        """logger property delegates to proxy."""
        assert reporter.logger is reporter.proxy.logger

    def test_metrics_delegates_to_proxy(self, reporter):
        """metrics property delegates to proxy."""
        assert reporter.metrics is reporter.proxy.metrics

    def test_test_mode_delegates_to_proxy(self, reporter):
        """_test_mode property delegates to proxy."""
        assert reporter._test_mode is reporter.proxy._test_mode

    def test_active_delegates_to_proxy(self, reporter):
        """_active property delegates to proxy."""
        assert reporter._active is reporter.proxy._active


class TestClientReporterWake:
    """Tests for wake() method."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    def test_wake_sets_event(self, reporter):
        """wake() sets the wake event."""
        reporter._wake_event.clear()
        reporter.wake()
        assert reporter._wake_event.is_set()

    def test_wake_with_tenant_resets_last_sync(self, reporter):
        """wake(tenant_id) resets last_sync for that tenant."""
        reporter._last_sync["t1"] = 9999999
        reporter.wake("t1")
        assert reporter._last_sync["t1"] == 0
        assert reporter._run_now_tenant_id == "t1"

    def test_wake_without_tenant_keeps_last_sync(self, reporter):
        """wake() without tenant_id doesn't modify last_sync."""
        reporter._last_sync["t1"] = 9999999
        reporter.wake()
        assert reporter._last_sync["t1"] == 9999999
        assert reporter._run_now_tenant_id is None


class TestClientReporterLifecycle:
    """Tests for start/stop lifecycle."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    async def test_start_clears_stop_event(self, reporter):
        """start() clears stop event."""
        reporter._stop.set()
        # Mock the loop to stop immediately
        reporter._report_loop = AsyncMock()
        await reporter.start()
        assert not reporter._stop.is_set()
        reporter._stop.set()  # Stop the task

    async def test_start_creates_task(self, reporter):
        """start() creates background task."""
        reporter._report_loop = AsyncMock()
        await reporter.start()
        assert reporter._task is not None
        reporter._stop.set()

    async def test_stop_sets_stop_event(self, reporter):
        """stop() sets stop event."""
        reporter._task = None
        await reporter.stop()
        assert reporter._stop.is_set()

    async def test_stop_sets_wake_event(self, reporter):
        """stop() sets wake event to unblock waiting."""
        reporter._task = None
        await reporter.stop()
        assert reporter._wake_event.is_set()


class TestClientReporterWaitForWakeup:
    """Tests for _wait_for_wakeup method."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    async def test_returns_immediately_if_stopped(self, reporter):
        """Returns immediately if stop is set."""
        reporter._stop.set()
        # Should return immediately without waiting
        await reporter._wait_for_wakeup(10.0)

    async def test_none_timeout_waits_for_event(self, reporter):
        """None timeout waits indefinitely for event."""
        async def set_event():
            await asyncio.sleep(0.05)
            reporter._wake_event.set()

        asyncio.create_task(set_event())
        await reporter._wait_for_wakeup(None)
        # Should have completed due to event

    async def test_zero_timeout_yields(self, reporter):
        """Zero timeout yields control."""
        await reporter._wait_for_wakeup(0)
        # Should complete immediately

    async def test_returns_on_timeout(self, reporter):
        """Returns after timeout expires."""
        start = asyncio.get_event_loop().time()
        await reporter._wait_for_wakeup(0.1)
        elapsed = asyncio.get_event_loop().time() - start
        assert elapsed >= 0.1 or reporter._wake_event.is_set()

    async def test_returns_on_wake_event(self, reporter):
        """Returns when wake event is set."""
        async def set_event():
            await asyncio.sleep(0.02)
            reporter._wake_event.set()

        task = asyncio.create_task(set_event())
        await reporter._wait_for_wakeup(10.0)  # Long timeout
        await task


class TestClientReporterEventsToPayloads:
    """Tests for _events_to_payloads method."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    def test_sent_event_payload(self, reporter):
        """Sent events include sent_ts."""
        events = [{"event_type": "sent", "message_id": "m1", "event_ts": 1234567890}]
        payloads = reporter._events_to_payloads(events)
        assert len(payloads) == 1
        assert payloads[0]["id"] == "m1"
        assert payloads[0]["sent_ts"] == 1234567890

    def test_error_event_payload(self, reporter):
        """Error events include error_ts and error."""
        events = [{
            "event_type": "error",
            "message_id": "m1",
            "event_ts": 1234567890,
            "description": "Connection refused",
        }]
        payloads = reporter._events_to_payloads(events)
        assert payloads[0]["error_ts"] == 1234567890
        assert payloads[0]["error"] == "Connection refused"

    def test_deferred_event_payload(self, reporter):
        """Deferred events include deferred_ts and reason."""
        events = [{
            "event_type": "deferred",
            "message_id": "m1",
            "event_ts": 1234567890,
            "description": "Rate limited",
        }]
        payloads = reporter._events_to_payloads(events)
        assert payloads[0]["deferred_ts"] == 1234567890
        assert payloads[0]["deferred_reason"] == "Rate limited"

    def test_bounce_event_payload(self, reporter):
        """Bounce events include bounce details."""
        events = [{
            "event_type": "bounce",
            "message_id": "m1",
            "event_ts": 1234567890,
            "description": "User unknown",
            "metadata": {"bounce_type": "hard", "bounce_code": "550"},
        }]
        payloads = reporter._events_to_payloads(events)
        assert payloads[0]["bounce_ts"] == 1234567890
        assert payloads[0]["bounce_type"] == "hard"
        assert payloads[0]["bounce_code"] == "550"
        assert payloads[0]["bounce_reason"] == "User unknown"

    def test_pec_event_payload(self, reporter):
        """PEC events include pec_event type."""
        events = [{
            "event_type": "pec_acceptance",
            "message_id": "m1",
            "event_ts": 1234567890,
            "description": "PEC accepted",
        }]
        payloads = reporter._events_to_payloads(events)
        assert payloads[0]["pec_event"] == "pec_acceptance"
        assert payloads[0]["pec_ts"] == 1234567890
        assert payloads[0]["pec_details"] == "PEC accepted"

    def test_multiple_events(self, reporter):
        """Multiple events are converted."""
        events = [
            {"event_type": "sent", "message_id": "m1", "event_ts": 1},
            {"event_type": "sent", "message_id": "m2", "event_ts": 2},
        ]
        payloads = reporter._events_to_payloads(events)
        assert len(payloads) == 2


class TestClientReporterProcessCycle:
    """Tests for _process_cycle method."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        proxy._tables["message_events"].fetch_unreported = AsyncMock(return_value=[])
        proxy._tables["tenants"].list_all = AsyncMock(return_value=[])
        proxy._tables["messages"].remove_fully_reported_before = AsyncMock(return_value=0)
        r = ClientReporter(proxy)
        return r

    async def test_returns_zero_when_inactive(self, reporter):
        """Returns 0 when proxy is inactive."""
        reporter.proxy._active = False
        result = await reporter._process_cycle()
        assert result == 0

    async def test_fetches_unreported_events(self, reporter):
        """Fetches unreported events."""
        await reporter._process_cycle()
        reporter.db.table("message_events").fetch_unreported.assert_called_once()

    async def test_applies_retention(self, reporter):
        """Applies retention after processing."""
        await reporter._process_cycle()
        reporter.db.table("messages").remove_fully_reported_before.assert_called()


class TestClientReporterSendDeliveryReports:
    """Tests for _send_delivery_reports method."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    async def test_callable_invoked_for_each_payload(self, reporter):
        """Custom callable is invoked for each payload."""
        callback = AsyncMock()
        reporter.proxy._report_delivery_callable = callback

        payloads = [{"id": "m1"}, {"id": "m2"}]
        acked, queued, next_sync = await reporter._send_delivery_reports(payloads)

        assert callback.call_count == 2
        assert acked == ["m1", "m2"]
        assert queued == 0

    async def test_raises_without_url_or_callable(self, reporter):
        """Raises RuntimeError if no URL or callable configured."""
        reporter.proxy._client_sync_url = None
        reporter.proxy._report_delivery_callable = None

        payloads = [{"id": "m1"}]
        with pytest.raises(RuntimeError, match="Client sync URL is not configured"):
            await reporter._send_delivery_reports(payloads)

    async def test_returns_empty_for_empty_payloads_without_url(self, reporter):
        """Returns empty list for empty payloads without URL."""
        reporter.proxy._client_sync_url = None
        reporter.proxy._report_delivery_callable = None

        acked, queued, next_sync = await reporter._send_delivery_reports([])
        assert acked == []
        assert queued == 0

    async def test_http_post_with_bearer_token(self, reporter):
        """Uses bearer token for authentication."""
        reporter.proxy._client_sync_url = "http://example.com/sync"
        reporter.proxy._client_sync_token = "secret-token"

        with patch('aiohttp.ClientSession') as mock_session:
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value={"ok": True, "queued": 5})
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()

            mock_post = MagicMock(return_value=mock_response)
            mock_session_instance = MagicMock()
            mock_session_instance.post = mock_post
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()
            mock_session.return_value = mock_session_instance

            payloads = [{"id": "m1"}]
            acked, queued, next_sync = await reporter._send_delivery_reports(payloads)

            mock_post.assert_called_once()
            call_kwargs = mock_post.call_args[1]
            assert "Authorization" in call_kwargs.get("headers", {})
            assert call_kwargs["headers"]["Authorization"] == "Bearer secret-token"


class TestClientReporterApplyRetention:
    """Tests for _apply_retention method."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        proxy._tables["messages"].remove_fully_reported_before = AsyncMock(return_value=0)
        return ClientReporter(proxy)

    async def test_skips_if_retention_zero(self, reporter):
        """Skips cleanup if retention is 0."""
        reporter.proxy._report_retention_seconds = 0
        await reporter._apply_retention()
        reporter.db.table("messages").remove_fully_reported_before.assert_not_called()

    async def test_removes_old_messages(self, reporter):
        """Removes messages older than retention."""
        reporter.proxy._report_retention_seconds = 3600
        await reporter._apply_retention()
        reporter.db.table("messages").remove_fully_reported_before.assert_called_once()

    async def test_refreshes_gauge_when_removed(self, reporter):
        """Refreshes queue gauge when messages removed."""
        reporter.proxy._report_retention_seconds = 3600
        reporter.db.table("messages").remove_fully_reported_before = AsyncMock(return_value=5)
        await reporter._apply_retention()
        reporter.proxy._refresh_queue_gauge.assert_called()


class TestDefaultSyncInterval:
    """Tests for default sync interval constant."""

    def test_default_sync_interval_is_5_minutes(self):
        """Default sync interval is 300 seconds (5 minutes)."""
        assert DEFAULT_SYNC_INTERVAL == 300
