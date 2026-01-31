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


# =============================================================================
# Additional Coverage Tests
# =============================================================================


class TestClientReporterProcessCycleWithEvents:
    """Tests for _process_cycle with actual events."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        proxy._tables["message_events"].fetch_unreported = AsyncMock(return_value=[])
        proxy._tables["message_events"].mark_reported = AsyncMock()
        proxy._tables["tenants"].list_all = AsyncMock(return_value=[])
        proxy._tables["tenants"].get = AsyncMock(return_value=None)
        proxy._tables["messages"].remove_fully_reported_before = AsyncMock(return_value=0)
        return ClientReporter(proxy)

    async def test_process_cycle_with_tenant_events(self, reporter):
        """Process cycle handles events grouped by tenant."""
        reporter.proxy._tables["message_events"].fetch_unreported = AsyncMock(
            return_value=[
                {"event_id": 1, "event_type": "sent", "message_id": "m1", "tenant_id": "t1", "event_ts": 1234567890},
                {"event_id": 2, "event_type": "sent", "message_id": "m2", "tenant_id": "t1", "event_ts": 1234567891},
            ]
        )
        reporter.proxy._tables["tenants"].get = AsyncMock(
            return_value={"id": "t1", "client_base_url": "http://test.com", "client_sync_path": "/sync"}
        )

        # Mock _send_reports_to_tenant
        with patch.object(reporter, "_send_reports_to_tenant") as mock_send:
            mock_send.return_value = (["m1", "m2"], 5, None)
            result = await reporter._process_cycle()

            mock_send.assert_called_once()
            assert result == 5

    async def test_process_cycle_with_global_events(self, reporter):
        """Process cycle handles events without tenant_id."""
        reporter.proxy._client_sync_url = "http://global.com/sync"
        reporter.proxy._tables["message_events"].fetch_unreported = AsyncMock(
            return_value=[
                {"event_id": 1, "event_type": "sent", "message_id": "m1", "tenant_id": None, "event_ts": 1234567890},
            ]
        )

        with patch.object(reporter, "_send_delivery_reports") as mock_send:
            mock_send.return_value = (["m1"], 3, None)
            result = await reporter._process_cycle()

            mock_send.assert_called_once()
            assert result == 3

    async def test_process_cycle_with_callable(self, reporter):
        """Process cycle uses callable for events without tenant."""
        callback = AsyncMock()
        reporter.proxy._report_delivery_callable = callback
        reporter.proxy._tables["message_events"].fetch_unreported = AsyncMock(
            return_value=[
                {"event_id": 1, "event_type": "sent", "message_id": "m1", "tenant_id": None, "event_ts": 1234567890},
            ]
        )

        result = await reporter._process_cycle()
        callback.assert_called_once()
        reporter.proxy._tables["message_events"].mark_reported.assert_called()

    async def test_process_cycle_marks_reported_events(self, reporter):
        """Process cycle marks events as reported."""
        reporter.proxy._report_delivery_callable = AsyncMock()
        reporter.proxy._tables["message_events"].fetch_unreported = AsyncMock(
            return_value=[
                {"event_id": 1, "event_type": "sent", "message_id": "m1", "tenant_id": None, "event_ts": 1234567890},
                {"event_id": 2, "event_type": "sent", "message_id": "m2", "tenant_id": None, "event_ts": 1234567891},
            ]
        )

        await reporter._process_cycle()
        reporter.proxy._tables["message_events"].mark_reported.assert_called_once()
        call_args = reporter.proxy._tables["message_events"].mark_reported.call_args
        assert 1 in call_args[0][0]  # event_id 1
        assert 2 in call_args[0][0]  # event_id 2

    async def test_process_cycle_handles_http_error(self, reporter):
        """Process cycle handles HTTP errors gracefully."""
        import aiohttp

        reporter.proxy._client_sync_url = "http://global.com/sync"
        reporter.proxy._tables["message_events"].fetch_unreported = AsyncMock(
            return_value=[
                {"event_id": 1, "event_type": "sent", "message_id": "m1", "tenant_id": None, "event_ts": 1234567890},
            ]
        )

        with patch.object(reporter, "_send_delivery_reports") as mock_send:
            mock_send.side_effect = aiohttp.ClientError("Connection failed")
            # Should not raise, just log warning
            result = await reporter._process_cycle()
            assert result == 0

    async def test_process_cycle_skips_missing_tenant(self, reporter):
        """Process cycle skips events for non-existent tenant."""
        reporter.proxy._tables["message_events"].fetch_unreported = AsyncMock(
            return_value=[
                {"event_id": 1, "event_type": "sent", "message_id": "m1", "tenant_id": "unknown", "event_ts": 1234567890},
            ]
        )
        reporter.proxy._tables["tenants"].get = AsyncMock(return_value=None)

        result = await reporter._process_cycle()
        assert result == 0

    async def test_process_cycle_fallback_to_global_url(self, reporter):
        """Process cycle uses global URL when tenant has no sync URL."""
        reporter.proxy._client_sync_url = "http://global.com/sync"
        reporter.proxy._tables["message_events"].fetch_unreported = AsyncMock(
            return_value=[
                {"event_id": 1, "event_type": "sent", "message_id": "m1", "tenant_id": "t1", "event_ts": 1234567890},
            ]
        )
        # Tenant exists but has no sync URL
        reporter.proxy._tables["tenants"].get = AsyncMock(
            return_value={"id": "t1", "client_base_url": None, "client_sync_path": None}
        )

        with patch.object(reporter, "_send_delivery_reports") as mock_send:
            mock_send.return_value = (["m1"], 0, None)
            await reporter._process_cycle()
            mock_send.assert_called_once()


class TestClientReporterSyncInterval:
    """Tests for sync interval handling in _process_cycle."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        proxy._tables["message_events"].fetch_unreported = AsyncMock(return_value=[])
        proxy._tables["message_events"].mark_reported = AsyncMock()
        proxy._tables["tenants"].list_all = AsyncMock(return_value=[])
        proxy._tables["tenants"].get = AsyncMock(return_value=None)
        proxy._tables["messages"].remove_fully_reported_before = AsyncMock(return_value=0)
        return ClientReporter(proxy)

    async def test_calls_tenants_without_events_after_interval(self, reporter):
        """Syncs tenants without events after sync interval."""
        import time

        reporter.proxy._tables["tenants"].list_all = AsyncMock(
            return_value=[
                {"id": "t1", "active": True, "client_base_url": "http://t1.com", "client_sync_path": "/sync"},
            ]
        )
        # Set last sync to far past
        reporter._last_sync["t1"] = time.time() - 1000

        with patch.object(reporter, "_send_reports_to_tenant") as mock_send:
            mock_send.return_value = ([], 2, None)
            result = await reporter._process_cycle()

            mock_send.assert_called_once()
            assert result == 2

    async def test_skips_tenant_within_interval(self, reporter):
        """Skips tenant if within sync interval."""
        import time

        reporter.proxy._tables["tenants"].list_all = AsyncMock(
            return_value=[
                {"id": "t1", "active": True, "client_base_url": "http://t1.com", "client_sync_path": "/sync"},
            ]
        )
        # Set last sync to recent
        reporter._last_sync["t1"] = time.time()

        with patch.object(reporter, "_send_reports_to_tenant") as mock_send:
            mock_send.return_value = ([], 0, None)
            await reporter._process_cycle()

            mock_send.assert_not_called()

    async def test_skips_inactive_tenant(self, reporter):
        """Skips inactive tenants."""
        reporter.proxy._tables["tenants"].list_all = AsyncMock(
            return_value=[
                {"id": "t1", "active": False, "client_base_url": "http://t1.com", "client_sync_path": "/sync"},
            ]
        )

        with patch.object(reporter, "_send_reports_to_tenant") as mock_send:
            await reporter._process_cycle()
            mock_send.assert_not_called()

    async def test_skips_tenant_without_sync_url(self, reporter):
        """Skips tenants without sync URL configured."""
        import time

        reporter.proxy._tables["tenants"].list_all = AsyncMock(
            return_value=[
                {"id": "t1", "active": True, "client_base_url": None, "client_sync_path": None},
            ]
        )
        reporter._last_sync["t1"] = time.time() - 1000

        with patch.object(reporter, "_send_reports_to_tenant") as mock_send:
            await reporter._process_cycle()
            mock_send.assert_not_called()

    async def test_run_now_filters_to_specific_tenant(self, reporter):
        """wake(tenant_id) filters to specific tenant."""
        import time

        reporter.proxy._tables["tenants"].list_all = AsyncMock(
            return_value=[
                {"id": "t1", "active": True, "client_base_url": "http://t1.com", "client_sync_path": "/sync"},
                {"id": "t2", "active": True, "client_base_url": "http://t2.com", "client_sync_path": "/sync"},
            ]
        )
        reporter._last_sync["t1"] = time.time() - 1000
        reporter._last_sync["t2"] = time.time() - 1000
        reporter._run_now_tenant_id = "t1"

        with patch.object(reporter, "_send_reports_to_tenant") as mock_send:
            mock_send.return_value = ([], 0, None)
            await reporter._process_cycle()

            # Should only call for t1
            assert mock_send.call_count == 1
            call_tenant = mock_send.call_args[0][0]
            assert call_tenant["id"] == "t1"


class TestClientReporterSendReportsToTenant:
    """Tests for _send_reports_to_tenant method."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    async def test_raises_if_no_sync_url(self, reporter):
        """Raises RuntimeError if tenant has no sync URL."""
        tenant = {"id": "t1", "client_base_url": None}

        with pytest.raises(RuntimeError, match="has no sync URL"):
            await reporter._send_reports_to_tenant(tenant, [])

    async def test_uses_bearer_auth(self, reporter):
        """Uses bearer token from tenant config."""
        tenant = {
            "id": "t1",
            "client_base_url": "http://t1.com",
            "client_sync_path": "/sync",
            "client_auth": {"method": "bearer", "token": "tenant-secret"},
        }

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value={"ok": True, "queued": 0})
            mock_response.text = AsyncMock(return_value="")
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()

            mock_post = MagicMock(return_value=mock_response)
            mock_session_instance = MagicMock()
            mock_session_instance.post = mock_post
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()
            mock_session.return_value = mock_session_instance

            await reporter._send_reports_to_tenant(tenant, [{"id": "m1"}])

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["headers"]["Authorization"] == "Bearer tenant-secret"

    async def test_uses_basic_auth(self, reporter):
        """Uses basic auth from tenant config."""
        tenant = {
            "id": "t1",
            "client_base_url": "http://t1.com",
            "client_sync_path": "/sync",
            "client_auth": {"method": "basic", "user": "admin", "password": "secret"},
        }

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value={"ok": True, "queued": 0})
            mock_response.text = AsyncMock(return_value="")
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()

            mock_post = MagicMock(return_value=mock_response)
            mock_session_instance = MagicMock()
            mock_session_instance.post = mock_post
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()
            mock_session.return_value = mock_session_instance

            await reporter._send_reports_to_tenant(tenant, [{"id": "m1"}])

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["auth"] is not None
            assert call_kwargs["auth"].login == "admin"

    async def test_parses_next_sync_after(self, reporter):
        """Parses next_sync_after from response."""
        tenant = {
            "id": "t1",
            "client_base_url": "http://t1.com",
            "client_sync_path": "/sync",
        }

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value={"ok": True, "queued": 5, "next_sync_after": 1234567890.5})
            mock_response.text = AsyncMock(return_value="")
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()

            mock_post = MagicMock(return_value=mock_response)
            mock_session_instance = MagicMock()
            mock_session_instance.post = mock_post
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()
            mock_session.return_value = mock_session_instance

            _, queued, next_sync = await reporter._send_reports_to_tenant(tenant, [])

            assert queued == 5
            assert next_sync == 1234567890.5

    async def test_handles_non_json_response(self, reporter):
        """Handles non-JSON response gracefully."""
        tenant = {
            "id": "t1",
            "client_base_url": "http://t1.com",
            "client_sync_path": "/sync",
        }

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(side_effect=ValueError("Invalid JSON"))
            mock_response.text = AsyncMock(return_value="Not JSON")
            mock_response.content_type = "text/html"
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()

            mock_post = MagicMock(return_value=mock_response)
            mock_session_instance = MagicMock()
            mock_session_instance.post = mock_post
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()
            mock_session.return_value = mock_session_instance

            # Should not raise
            ids, queued, next_sync = await reporter._send_reports_to_tenant(tenant, [{"id": "m1"}])
            assert ids == ["m1"]
            assert queued == 0


class TestClientReporterReportLoop:
    """Tests for _report_loop method."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        proxy._tables["message_events"].fetch_unreported = AsyncMock(return_value=[])
        proxy._tables["tenants"].list_all = AsyncMock(return_value=[])
        proxy._tables["messages"].remove_fully_reported_before = AsyncMock(return_value=0)
        return ClientReporter(proxy)

    async def test_report_loop_waits_in_test_mode(self, reporter):
        """In test mode, first iteration waits for wake."""
        reporter.proxy._test_mode = True

        # Start loop and stop immediately
        async def stop_soon():
            await asyncio.sleep(0.05)
            reporter._stop.set()
            reporter._wake_event.set()

        asyncio.create_task(stop_soon())

        # Should wait for wake event
        await reporter._report_loop()

    async def test_report_loop_continues_on_queued(self, reporter):
        """Loop continues immediately when client has queued messages."""
        reporter.proxy._test_mode = False
        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return 5  # Has queued
            elif call_count == 2:
                reporter._stop.set()  # Stop after second call
                return 0

        with patch.object(reporter, "_process_cycle", side_effect=mock_process):
            with patch.object(reporter, "_wait_for_wakeup"):
                await reporter._report_loop()
                assert call_count == 2


class TestClientReporterSendDeliveryReportsExtended:
    """Extended tests for _send_delivery_reports."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    async def test_logs_delivery_with_ids_preview(self, reporter):
        """Logs preview of message IDs when delivering."""
        callback = AsyncMock()
        reporter.proxy._report_delivery_callable = callback
        reporter.proxy._log_delivery_activity = True

        payloads = [{"id": f"m{i}"} for i in range(10)]
        await reporter._send_delivery_reports(payloads)

        # Should have logged with preview
        reporter.proxy.logger.info.assert_called()

    async def test_uses_basic_auth_for_sync_url(self, reporter):
        """Uses basic auth when configured."""
        reporter.proxy._client_sync_url = "http://sync.com"
        reporter.proxy._client_sync_user = "user"
        reporter.proxy._client_sync_password = "pass"

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(return_value={"ok": True})
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()

            mock_post = MagicMock(return_value=mock_response)
            mock_session_instance = MagicMock()
            mock_session_instance.post = mock_post
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()
            mock_session.return_value = mock_session_instance

            await reporter._send_delivery_reports([{"id": "m1"}])

            call_kwargs = mock_post.call_args[1]
            assert call_kwargs["auth"] is not None

    async def test_parses_error_and_not_found_ids(self, reporter):
        """Parses error and not_found IDs from response."""
        reporter.proxy._client_sync_url = "http://sync.com"
        reporter.proxy._log_delivery_activity = True

        with patch("aiohttp.ClientSession") as mock_session:
            mock_response = AsyncMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json = AsyncMock(
                return_value={"ok": False, "error": ["m2"], "not_found": ["m3"], "queued": 0}
            )
            mock_response.__aenter__ = AsyncMock(return_value=mock_response)
            mock_response.__aexit__ = AsyncMock()

            mock_post = MagicMock(return_value=mock_response)
            mock_session_instance = MagicMock()
            mock_session_instance.post = mock_post
            mock_session_instance.__aenter__ = AsyncMock(return_value=mock_session_instance)
            mock_session_instance.__aexit__ = AsyncMock()
            mock_session.return_value = mock_session_instance

            ids, queued, _ = await reporter._send_delivery_reports([{"id": "m1"}, {"id": "m2"}, {"id": "m3"}])

            # All IDs should still be in processed list
            assert "m1" in ids


class TestClientReporterWaitForWakeupExtended:
    """Extended tests for _wait_for_wakeup."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    async def test_infinite_timeout_waits_for_event(self, reporter):
        """math.inf timeout waits for event."""
        import math

        async def set_event():
            await asyncio.sleep(0.05)
            reporter._wake_event.set()

        task = asyncio.create_task(set_event())
        await reporter._wait_for_wakeup(math.inf)
        await task

    async def test_clears_wake_event_after_wait(self, reporter):
        """Wake event is cleared after waiting."""
        reporter._wake_event.set()
        await reporter._wait_for_wakeup(0.01)
        assert not reporter._wake_event.is_set()


class TestClientReporterPropertiesExtended:
    """Additional tests for property delegation."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    def test_smtp_batch_size_delegates(self, reporter):
        """_smtp_batch_size delegates to proxy."""
        assert reporter._smtp_batch_size == reporter.proxy._smtp_batch_size

    def test_report_retention_seconds_delegates(self, reporter):
        """_report_retention_seconds delegates to proxy."""
        assert reporter._report_retention_seconds == reporter.proxy._report_retention_seconds

    def test_client_sync_url_delegates(self, reporter):
        """_client_sync_url delegates to proxy."""
        assert reporter._client_sync_url == reporter.proxy._client_sync_url

    def test_client_sync_token_delegates(self, reporter):
        """_client_sync_token delegates to proxy."""
        assert reporter._client_sync_token == reporter.proxy._client_sync_token

    def test_client_sync_user_delegates(self, reporter):
        """_client_sync_user delegates to proxy."""
        assert reporter._client_sync_user == reporter.proxy._client_sync_user

    def test_client_sync_password_delegates(self, reporter):
        """_client_sync_password delegates to proxy."""
        assert reporter._client_sync_password == reporter.proxy._client_sync_password

    def test_report_delivery_callable_delegates(self, reporter):
        """_report_delivery_callable delegates to proxy."""
        assert reporter._report_delivery_callable == reporter.proxy._report_delivery_callable

    def test_log_delivery_activity_delegates(self, reporter):
        """_log_delivery_activity delegates to proxy."""
        assert reporter._log_delivery_activity == reporter.proxy._log_delivery_activity


class TestClientReporterUtcNowEpoch:
    """Tests for _utc_now_epoch static method."""

    def test_returns_int(self):
        """_utc_now_epoch returns an integer."""
        result = ClientReporter._utc_now_epoch()
        assert isinstance(result, int)

    def test_returns_reasonable_timestamp(self):
        """_utc_now_epoch returns a reasonable timestamp."""
        import time

        result = ClientReporter._utc_now_epoch()
        # Should be close to current time (within 5 seconds)
        assert abs(result - time.time()) < 5


class TestClientReporterEventsToPayloadsExtended:
    """Extended tests for _events_to_payloads."""

    @pytest.fixture
    def reporter(self):
        proxy = MockProxy()
        return ClientReporter(proxy)

    def test_pec_event_without_description(self, reporter):
        """PEC events without description don't add pec_details."""
        events = [{
            "event_type": "pec_delivery",
            "message_id": "m1",
            "event_ts": 1234567890,
            "description": None,
        }]
        payloads = reporter._events_to_payloads(events)
        assert "pec_details" not in payloads[0]
        assert payloads[0]["pec_event"] == "pec_delivery"


class TestStopWithTask:
    """Tests for stop() with running task."""

    async def test_stop_awaits_task(self):
        """stop() awaits the running task."""
        proxy = MockProxy()
        reporter = ClientReporter(proxy)

        # Create a task that completes when stop is set
        async def mock_loop():
            while not reporter._stop.is_set():
                await asyncio.sleep(0.01)

        reporter._task = asyncio.create_task(mock_loop())
        await asyncio.sleep(0.02)  # Let task start

        await reporter.stop()

        assert reporter._stop.is_set()
        assert reporter._task.done()
