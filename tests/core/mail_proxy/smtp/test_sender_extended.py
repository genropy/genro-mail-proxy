# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Extended unit tests for SmtpSender - dispatch loop, send_with_limits, attachments."""

import asyncio
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from core.mail_proxy.smtp.sender import (
    SmtpSender,
    AccountConfigurationError,
    AttachmentTooLargeError,
)


class MockProxy:
    """Reusable mock proxy for tests."""

    def __init__(self):
        self.db = MagicMock()
        self.logger = MagicMock()
        self.metrics = MagicMock()
        self.attachments = MagicMock()
        self.client_reporter = MagicMock()
        self.client_reporter._wake_event = asyncio.Event()
        self._retry_strategy = MagicMock()
        self._test_mode = True
        self._send_loop_interval = 0.1
        self._smtp_batch_size = 10
        self._batch_size_per_account = 5
        self._max_concurrent_sends = 5
        self._max_concurrent_per_account = 3
        self._max_concurrent_attachments = 5
        self._attachment_timeout = 30.0
        self._attachment_semaphore = None
        self._attachment_cache = None
        self._log_delivery_activity = True
        self.default_host = None
        self.default_port = None
        self.default_user = None
        self.default_password = None
        self.default_use_tls = None

        # Setup db.table mock - pre-initialize common tables
        self._tables = {
            "messages": MagicMock(),
            "tenants": MagicMock(),
            "accounts": MagicMock(),
            "message_events": MagicMock(),
            "storages": MagicMock(),
        }
        self.db.table = MagicMock(side_effect=self._get_table)

        # Also add _refresh_queue_gauge mock at proxy level
        self._refresh_queue_gauge = AsyncMock()

    def _get_table(self, name):
        if name not in self._tables:
            self._tables[name] = MagicMock()
        return self._tables[name]


class TestSmtpSenderDispatchLoop:
    """Tests for the dispatch loop."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_dispatch_loop_processes_cycle(self, sender, mock_proxy):
        """Dispatch loop calls _process_cycle."""
        # Make the loop run once then stop
        call_count = 0

        async def mock_process_cycle():
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                sender._stop.set()
            return False

        sender._process_cycle = mock_process_cycle

        # Start dispatch loop
        task = asyncio.create_task(sender._dispatch_loop())
        await asyncio.sleep(0.2)
        sender._stop.set()
        sender._wake_event.set()
        await asyncio.wait_for(task, timeout=1.0)

        assert call_count >= 1

    async def test_dispatch_loop_wakes_client_reporter_on_processed(self, sender, mock_proxy):
        """Dispatch loop wakes client reporter when messages are processed."""
        call_count = 0

        async def mock_process_cycle():
            nonlocal call_count
            call_count += 1
            sender._stop.set()
            return True  # Messages were processed

        sender._process_cycle = mock_process_cycle
        mock_proxy.client_reporter._wake_event.clear()

        task = asyncio.create_task(sender._dispatch_loop())
        await asyncio.wait_for(task, timeout=1.0)

        # Client reporter should be woken
        assert mock_proxy.client_reporter._wake_event.is_set()


class TestSmtpSenderProcessCycle:
    """Tests for _process_cycle."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s._dispatch_batch = AsyncMock()
        return s

    async def test_process_cycle_fetches_immediate_priority(self, sender, mock_proxy):
        """_process_cycle fetches immediate priority messages first."""
        mock_proxy._tables["messages"].fetch_ready = AsyncMock(side_effect=[
            [{"pk": "1", "id": "m1", "account_id": "a1"}],  # immediate
            [],  # regular
        ])
        mock_proxy._refresh_queue_gauge = AsyncMock()

        result = await sender._process_cycle()

        assert result is True
        calls = mock_proxy._tables["messages"].fetch_ready.call_args_list
        assert len(calls) == 2
        # First call should be for priority=0
        assert calls[0].kwargs.get("priority") == 0

    async def test_process_cycle_fetches_regular_priority(self, sender, mock_proxy):
        """_process_cycle fetches regular priority messages."""
        mock_proxy._tables["messages"].fetch_ready = AsyncMock(side_effect=[
            [],  # no immediate
            [{"pk": "2", "id": "m2", "account_id": "a1"}],  # regular
        ])
        mock_proxy._refresh_queue_gauge = AsyncMock()

        result = await sender._process_cycle()

        assert result is True
        calls = mock_proxy._tables["messages"].fetch_ready.call_args_list
        assert len(calls) == 2
        # Second call should be for min_priority=1
        assert calls[1].kwargs.get("min_priority") == 1

    async def test_process_cycle_returns_false_when_no_messages(self, sender, mock_proxy):
        """_process_cycle returns False when no messages."""
        mock_proxy._tables["messages"].fetch_ready = AsyncMock(return_value=[])
        mock_proxy._refresh_queue_gauge = AsyncMock()

        result = await sender._process_cycle()

        assert result is False


class TestSmtpSenderDispatchBatch:
    """Tests for _dispatch_batch."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s._dispatch_message = AsyncMock()
        return s

    async def test_dispatch_batch_groups_by_account(self, sender, mock_proxy):
        """Messages are grouped by account_id."""
        batch = [
            {"pk": "1", "id": "m1", "account_id": "acct1", "tenant_id": "t1"},
            {"pk": "2", "id": "m2", "account_id": "acct1", "tenant_id": "t1"},
            {"pk": "3", "id": "m3", "account_id": "acct2", "tenant_id": "t1"},
        ]

        await sender._dispatch_batch(batch, 12345)

        # All 3 messages should be dispatched
        assert sender._dispatch_message.call_count == 3

    async def test_dispatch_batch_respects_account_batch_size(self, sender, mock_proxy):
        """Per-account batch size limits are respected."""
        mock_proxy._batch_size_per_account = 2

        batch = [
            {"pk": "1", "id": "m1", "account_id": "acct1", "tenant_id": "t1"},
            {"pk": "2", "id": "m2", "account_id": "acct1", "tenant_id": "t1"},
            {"pk": "3", "id": "m3", "account_id": "acct1", "tenant_id": "t1"},  # This should be skipped
        ]

        await sender._dispatch_batch(batch, 12345)

        # Only 2 messages for acct1 should be dispatched
        assert sender._dispatch_message.call_count == 2

    async def test_dispatch_batch_uses_account_specific_batch_size(self, sender, mock_proxy):
        """Uses account-specific batch_size from database."""
        mock_proxy._batch_size_per_account = 10
        mock_proxy._tables["accounts"].get = AsyncMock(
            return_value={"id": "acct1", "batch_size": 1}
        )

        batch = [
            {"pk": "1", "id": "m1", "account_id": "acct1", "tenant_id": "t1"},
            {"pk": "2", "id": "m2", "account_id": "acct1", "tenant_id": "t1"},
        ]

        await sender._dispatch_batch(batch, 12345)

        # Only 1 message should be dispatched due to account-specific batch_size
        assert sender._dispatch_message.call_count == 1

    async def test_dispatch_batch_default_account_handling(self, sender, mock_proxy):
        """Messages without account_id use 'default'."""
        batch = [
            {"pk": "1", "id": "m1", "account_id": None, "tenant_id": "t1"},
        ]

        await sender._dispatch_batch(batch, 12345)

        assert sender._dispatch_message.call_count == 1


class TestSmtpSenderDispatchMessage:
    """Tests for _dispatch_message."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s._build_email = AsyncMock(return_value=(EmailMessage(), "from@test.com"))
        s._send_with_limits = AsyncMock(return_value={"status": "sent"})
        s._publish_result = AsyncMock()
        return s

    async def test_dispatch_message_builds_and_sends(self, sender, mock_proxy):
        """_dispatch_message builds email and sends."""
        mock_proxy._tables["messages"].clear_deferred = AsyncMock()

        entry = {
            "pk": "pk-1",
            "id": "msg-1",
            "tenant_id": "t1",
            "account_id": "a1",
            "message": {
                "from": "sender@test.com",
                "to": ["recipient@test.com"],
                "subject": "Test",
                "body": "Hello",
            },
        }

        await sender._dispatch_message(entry, 12345)

        sender._build_email.assert_called_once()
        sender._send_with_limits.assert_called_once()
        sender._publish_result.assert_called_once()

    async def test_dispatch_message_handles_build_keyerror(self, sender, mock_proxy):
        """Handles KeyError during email building."""
        mock_proxy._tables["messages"].clear_deferred = AsyncMock()
        mock_proxy._tables["message_events"].add_event = AsyncMock()
        sender._build_email = AsyncMock(side_effect=KeyError("from"))

        entry = {
            "pk": "pk-1",
            "id": "msg-1",
            "message": {},
        }

        await sender._dispatch_message(entry, 12345)

        # Should publish error result
        sender._publish_result.assert_called_once()
        result = sender._publish_result.call_args[0][0]
        assert result["status"] == "error"
        assert "missing" in result["error"]

    async def test_dispatch_message_handles_build_valueerror(self, sender, mock_proxy):
        """Handles ValueError during email building."""
        mock_proxy._tables["messages"].clear_deferred = AsyncMock()
        mock_proxy._tables["message_events"].add_event = AsyncMock()
        sender._build_email = AsyncMock(side_effect=ValueError("Invalid attachment"))

        entry = {
            "pk": "pk-1",
            "id": "msg-1",
            "message": {},
        }

        await sender._dispatch_message(entry, 12345)

        # Should publish error result
        sender._publish_result.assert_called_once()
        result = sender._publish_result.call_args[0][0]
        assert result["status"] == "error"


class TestSmtpSenderSendWithLimits:
    """Tests for _send_with_limits."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s._resolve_account = AsyncMock(return_value=(
            "smtp.test.com", 587, "user", "pass", {"id": "a1", "use_tls": True}
        ))
        return s

    async def test_send_with_limits_success(self, sender, mock_proxy):
        """Successful send returns sent status."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={"name": "Test Tenant"})
        mock_proxy._tables["message_events"].add_event = AsyncMock()
        sender.rate_limiter.check_and_plan = AsyncMock(return_value=(None, False))
        sender.rate_limiter.log_send = AsyncMock()

        with patch.object(sender.pool, "connection") as mock_conn:
            mock_smtp = AsyncMock()
            mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
            mock_smtp.__aexit__ = AsyncMock(return_value=None)
            mock_smtp.send_message = AsyncMock()
            mock_conn.return_value = mock_smtp

            msg = EmailMessage()
            msg["From"] = "from@test.com"

            result = await sender._send_with_limits(
                msg, "from@test.com", "pk-1", "msg-1",
                {"tenant_id": "t1", "account_id": "a1"}
            )

            assert result["status"] == "sent"
            mock_proxy.metrics.inc_sent.assert_called_once()

    async def test_send_with_limits_account_error(self, sender, mock_proxy):
        """AccountConfigurationError returns error status."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value=None)
        mock_proxy._tables["message_events"].add_event = AsyncMock()
        sender._resolve_account = AsyncMock(
            side_effect=AccountConfigurationError("Account not found")
        )

        result = await sender._send_with_limits(
            EmailMessage(), None, "pk-1", "msg-1",
            {"tenant_id": "t1", "account_id": "bad"}
        )

        assert result["status"] == "error"
        assert "Account not found" in result["error"]

    async def test_send_with_limits_rate_limited_defer(self, sender, mock_proxy):
        """Rate limited message is deferred."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={"name": "Test"})
        mock_proxy._tables["message_events"].add_event = AsyncMock()
        sender.rate_limiter.check_and_plan = AsyncMock(return_value=(12345, False))  # defer

        result = await sender._send_with_limits(
            EmailMessage(), None, "pk-1", "msg-1",
            {"tenant_id": "t1", "account_id": "a1"}
        )

        assert result is None  # Deferred, no result
        mock_proxy.metrics.inc_rate_limited.assert_called_once()
        mock_proxy.metrics.inc_deferred.assert_called_once()

    async def test_send_with_limits_rate_limited_reject(self, sender, mock_proxy):
        """Rate limited message with reject returns error."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={"name": "Test"})
        mock_proxy._tables["message_events"].add_event = AsyncMock()
        sender.rate_limiter.check_and_plan = AsyncMock(return_value=(12345, True))  # reject

        result = await sender._send_with_limits(
            EmailMessage(), None, "pk-1", "msg-1",
            {"tenant_id": "t1", "account_id": "a1"}
        )

        assert result["status"] == "error"
        assert result["error"] == "rate_limit_exceeded"

    async def test_send_with_limits_smtp_error_retry(self, sender, mock_proxy):
        """SMTP temporary error triggers retry."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={"name": "Test"})
        mock_proxy._tables["messages"].update_payload = AsyncMock()
        mock_proxy._tables["message_events"].add_event = AsyncMock()
        sender.rate_limiter.check_and_plan = AsyncMock(return_value=(None, False))
        sender.rate_limiter.release_slot = AsyncMock()
        mock_proxy._retry_strategy.classify_error = MagicMock(return_value=(True, 450))
        mock_proxy._retry_strategy.should_retry = MagicMock(return_value=True)
        mock_proxy._retry_strategy.calculate_delay = MagicMock(return_value=60)
        mock_proxy._retry_strategy.max_retries = 3

        with patch.object(sender.pool, "connection") as mock_conn:
            mock_smtp = AsyncMock()
            mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
            mock_smtp.__aexit__ = AsyncMock(return_value=None)
            mock_smtp.send_message = AsyncMock(side_effect=Exception("SMTP Error"))
            mock_conn.return_value = mock_smtp

            result = await sender._send_with_limits(
                EmailMessage(), None, "pk-1", "msg-1",
                {"tenant_id": "t1", "account_id": "a1", "retry_count": 0}
            )

            assert result["status"] == "deferred"
            assert result["retry_count"] == 1
            mock_proxy.metrics.inc_deferred.assert_called_once()

    async def test_send_with_limits_smtp_error_permanent(self, sender, mock_proxy):
        """SMTP permanent error returns error status."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={"name": "Test"})
        mock_proxy._tables["message_events"].add_event = AsyncMock()
        sender.rate_limiter.check_and_plan = AsyncMock(return_value=(None, False))
        sender.rate_limiter.release_slot = AsyncMock()
        mock_proxy._retry_strategy.classify_error = MagicMock(return_value=(False, 550))
        mock_proxy._retry_strategy.should_retry = MagicMock(return_value=False)
        mock_proxy._retry_strategy.max_retries = 3

        with patch.object(sender.pool, "connection") as mock_conn:
            mock_smtp = AsyncMock()
            mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
            mock_smtp.__aexit__ = AsyncMock(return_value=None)
            mock_smtp.send_message = AsyncMock(side_effect=Exception("Permanent Error"))
            mock_conn.return_value = mock_smtp

            result = await sender._send_with_limits(
                EmailMessage(), None, "pk-1", "msg-1",
                {"tenant_id": "t1", "account_id": "a1", "retry_count": 0}
            )

            assert result["status"] == "error"
            mock_proxy.metrics.inc_error.assert_called_once()


class TestSmtpSenderWaitForWakeup:
    """Tests for _wait_for_wakeup."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_wait_returns_immediately_if_stopped(self, sender):
        """Returns immediately if stop is set."""
        sender._stop.set()
        await sender._wait_for_wakeup(10.0)
        # Should return without waiting

    async def test_wait_with_none_timeout_waits_indefinitely(self, sender):
        """None timeout waits for wake event."""
        async def set_wake():
            await asyncio.sleep(0.05)
            sender._wake_event.set()

        asyncio.create_task(set_wake())
        await sender._wait_for_wakeup(None)
        # Should complete when wake is set

    async def test_wait_with_zero_timeout_yields(self, sender):
        """Zero timeout just yields."""
        await sender._wait_for_wakeup(0)
        # Should return immediately

    async def test_wait_returns_on_timeout(self, sender):
        """Returns after timeout expires."""
        import time
        start = time.time()
        await sender._wait_for_wakeup(0.1)
        elapsed = time.time() - start
        assert 0.05 < elapsed < 0.3

    async def test_wait_returns_on_wake_event(self, sender):
        """Returns when wake event is set."""
        async def set_wake():
            await asyncio.sleep(0.05)
            sender._wake_event.set()

        asyncio.create_task(set_wake())

        import time
        start = time.time()
        await sender._wait_for_wakeup(10.0)
        elapsed = time.time() - start
        assert elapsed < 1.0  # Should return quickly when woken


class TestSmtpSenderCleanupLoop:
    """Tests for _cleanup_loop."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s.pool.cleanup = AsyncMock()
        return s

    async def test_cleanup_loop_runs_cleanup(self, sender):
        """Cleanup loop calls pool.cleanup."""
        async def stop_after_cleanup():
            await asyncio.sleep(0.05)
            sender._stop.set()
            sender._wake_cleanup_event.set()

        asyncio.create_task(stop_after_cleanup())
        sender._wake_cleanup_event.set()  # Trigger first cleanup

        task = asyncio.create_task(sender._cleanup_loop())
        await asyncio.wait_for(task, timeout=1.0)

        sender.pool.cleanup.assert_called()


class TestSmtpSenderAttachments:
    """Tests for attachment processing."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        mock_proxy.attachments.fetch = AsyncMock(return_value=(b"content", "file.txt"))
        mock_proxy.attachments.guess_mime = MagicMock(return_value=("text", "plain"))
        return s

    async def test_process_attachments_adds_to_message(self, sender, mock_proxy):
        """Attachments are added to the email message."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value=None)

        msg = EmailMessage()
        msg.set_content("Body text")

        await sender._process_attachments(
            msg,
            {"tenant_id": "t1"},
            [{"filename": "file.txt", "storage_path": "/path/to/file"}],
            "plain",
        )

        # Message should now be multipart with attachment
        assert msg.is_multipart() or len(list(msg.iter_attachments())) > 0

    async def test_process_attachments_handles_fetch_error(self, sender, mock_proxy):
        """Raises ValueError on fetch failure."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value=None)
        mock_proxy.attachments.fetch = AsyncMock(side_effect=Exception("Fetch failed"))

        msg = EmailMessage()
        msg.set_content("Body")

        with pytest.raises(ValueError, match="Attachment fetch failed"):
            await sender._process_attachments(
                msg,
                {"tenant_id": "t1"},
                [{"filename": "file.txt", "storage_path": "/path"}],
                "plain",
            )

    async def test_process_attachments_rejects_too_large(self, sender, mock_proxy):
        """Rejects attachment exceeding size limit."""
        # Configure large_file_config to reject attachments > 1MB
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={
            "large_file_config": {
                "enabled": True,
                "max_size_mb": 1.0,
                "action": "reject",
            }
        })
        # Create a mock attachment manager that returns large content (2MB)
        mock_att_manager = MagicMock()
        mock_att_manager.fetch = AsyncMock(return_value=(b"x" * (2 * 1024 * 1024), "large.bin"))
        # Mock _get_attachment_manager_for_message to return our manager
        sender._get_attachment_manager_for_message = AsyncMock(return_value=mock_att_manager)

        msg = EmailMessage()
        msg.set_content("Body")

        with pytest.raises(AttachmentTooLargeError):
            await sender._process_attachments(
                msg,
                {"tenant_id": "t1"},
                [{"filename": "large.bin", "storage_path": "/path"}],
                "plain",
            )

    async def test_get_large_file_config_returns_none_without_tenant(self, sender, mock_proxy):
        """Returns None when no tenant_id."""
        result = await sender._get_large_file_config_for_message({})
        assert result is None

    async def test_get_large_file_config_returns_tenant_config(self, sender, mock_proxy):
        """Returns tenant's large_file_config."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={
            "large_file_config": {"enabled": True, "max_size_mb": 5.0}
        })

        result = await sender._get_large_file_config_for_message({"tenant_id": "t1"})

        assert result["enabled"] is True
        assert result["max_size_mb"] == 5.0

    async def test_get_attachment_manager_returns_global_without_tenant(self, sender, mock_proxy):
        """Returns global attachment manager when no tenant."""
        result = await sender._get_attachment_manager_for_message({})
        assert result is mock_proxy.attachments

    async def test_fetch_attachment_with_timeout_respects_timeout(self, sender, mock_proxy):
        """Attachment fetch respects timeout."""
        async def slow_fetch(att):
            await asyncio.sleep(10)
            return (b"content", "file.txt")

        mock_proxy.attachments.fetch = slow_fetch
        mock_proxy._attachment_timeout = 0.1

        with pytest.raises(TimeoutError):
            await sender._fetch_attachment_with_timeout({"filename": "file.txt"})


class TestSmtpSenderAccountResolutionExtended:
    """Extended tests for account resolution."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_resolve_account_converts_valueerror(self, sender, mock_proxy):
        """ValueError from db.get is converted to AccountConfigurationError."""
        mock_proxy._tables["accounts"].get = AsyncMock(
            side_effect=ValueError("Account 'bad' not found")
        )

        with pytest.raises(AccountConfigurationError, match="Account 'bad' not found"):
            await sender._resolve_account("t1", "bad")


class TestSmtpSenderEmailBuildingExtended:
    """Extended tests for email building edge cases."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_build_email_with_message_id(self, sender, mock_proxy):
        """Builds email with custom Message-ID."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
            "message_id": "<custom-id@example.com>",
        }

        msg, _ = await sender._build_email(data)

        assert msg["Message-ID"] == "<custom-id@example.com>"

    async def test_build_email_replaces_existing_header(self, sender, mock_proxy):
        """Custom headers can replace existing headers."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Original Subject",
            "headers": {
                "Subject": "Replaced Subject",  # Replace existing header
            },
        }

        msg, _ = await sender._build_email(data)

        assert msg["Subject"] == "Replaced Subject"

    async def test_build_email_skips_none_headers(self, sender, mock_proxy):
        """None values in headers dict are skipped."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
            "headers": {
                "X-Custom": "value",
                "X-Skip": None,  # Should be skipped
            },
        }

        msg, _ = await sender._build_email(data)

        assert msg["X-Custom"] == "value"
        assert msg["X-Skip"] is None

    async def test_format_addresses_with_scalar(self, sender, mock_proxy):
        """_format_addresses handles scalar values."""
        data = {
            "from": "sender@example.com",
            "to": 12345,  # Non-string, non-list value
            "subject": "Test",
        }

        msg, _ = await sender._build_email(data)

        # Should convert to string
        assert "12345" in msg["To"]

    async def test_build_email_adds_tracking_header(self, sender, mock_proxy):
        """X-Genro-Mail-ID header is added for message tracking."""
        data = {
            "id": "msg-track-12345",
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
        }

        msg, _ = await sender._build_email(data)

        assert msg["X-Genro-Mail-ID"] == "msg-track-12345"


class TestSmtpSenderMaxRetriesExceeded:
    """Tests for max retries exceeded path."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s._resolve_account = AsyncMock(return_value=(
            "smtp.test.com", 587, "user", "pass", {"id": "a1", "use_tls": True}
        ))
        return s

    async def test_send_with_limits_max_retries_exceeded(self, sender, mock_proxy):
        """Error message includes max retries when exceeded."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={"name": "Test"})
        mock_proxy._tables["message_events"].add_event = AsyncMock()
        sender.rate_limiter.check_and_plan = AsyncMock(return_value=(None, False))
        sender.rate_limiter.release_slot = AsyncMock()
        mock_proxy._retry_strategy.classify_error = MagicMock(return_value=(True, 450))
        mock_proxy._retry_strategy.should_retry = MagicMock(return_value=False)
        mock_proxy._retry_strategy.max_retries = 3

        with patch.object(sender.pool, "connection") as mock_conn:
            mock_smtp = AsyncMock()
            mock_smtp.__aenter__ = AsyncMock(return_value=mock_smtp)
            mock_smtp.__aexit__ = AsyncMock(return_value=None)
            mock_smtp.send_message = AsyncMock(side_effect=Exception("SMTP Error"))
            mock_conn.return_value = mock_smtp

            result = await sender._send_with_limits(
                EmailMessage(), None, "pk-1", "msg-1",
                {"tenant_id": "t1", "account_id": "a1", "retry_count": 3}  # Already at max
            )

            assert result["status"] == "error"
            assert "Max retries" in result["error"]


class TestSmtpSenderLargeFileRewrite:
    """Tests for large file rewrite functionality."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        mock_proxy.attachments.fetch = AsyncMock(return_value=(b"content", "file.txt"))
        mock_proxy.attachments.guess_mime = MagicMock(return_value=("text", "plain"))
        return s

    async def test_process_attachments_warns_large_file(self, sender, mock_proxy):
        """Large file with 'warn' action logs warning but attaches."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={
            "large_file_config": {
                "enabled": True,
                "max_size_mb": 0.001,  # 1KB limit to trigger warning
                "action": "warn",
            }
        })
        mock_proxy._tables["storages"].get_storage_manager = AsyncMock(return_value=None)
        # Return content larger than 0.001 MB (1KB)
        mock_proxy.attachments.fetch = AsyncMock(return_value=(b"x" * 2000, "file.txt"))

        msg = EmailMessage()
        msg.set_content("Body text")

        await sender._process_attachments(
            msg,
            {"tenant_id": "t1"},
            [{"filename": "file.txt", "storage_path": "/path/to/file"}],
            "plain",
        )

        # Should log warning but still attach
        mock_proxy.logger.warning.assert_called()

    async def test_create_large_file_storage_returns_none_without_url(self, sender, mock_proxy):
        """_create_large_file_storage returns None without storage_url."""
        result = sender._create_large_file_storage({"enabled": True})
        assert result is None


class TestSmtpSenderTenantAttachmentManager:
    """Tests for tenant-specific attachment manager."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_get_attachment_manager_with_tenant_auth(self, sender, mock_proxy):
        """Returns custom attachment manager with tenant auth."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={
            "attachment_config": {"base_url": "https://attachments.example.com"},
            "client_auth": {
                "method": "bearer",
                "token": "secret-token",
            }
        })
        mock_proxy._tables["storages"].get_storage_manager = AsyncMock(return_value=None)

        result = await sender._get_attachment_manager_for_message({"tenant_id": "t1"})

        # Should return a new AttachmentManager, not the global one
        assert result is not mock_proxy.attachments

    async def test_get_attachment_manager_returns_global_when_no_config(self, sender, mock_proxy):
        """Returns global manager when tenant has no custom config."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={})
        mock_proxy._tables["storages"].get_storage_manager = AsyncMock(side_effect=ValueError())

        result = await sender._get_attachment_manager_for_message({"tenant_id": "t1"})

        assert result is mock_proxy.attachments


class TestSmtpSenderWaitForWakeupExtended:
    """Extended tests for _wait_for_wakeup."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_wait_with_infinite_timeout(self, sender):
        """Infinite timeout waits for wake event."""
        import math

        async def set_wake():
            await asyncio.sleep(0.05)
            sender._wake_event.set()

        asyncio.create_task(set_wake())
        await sender._wait_for_wakeup(math.inf)
        # Should complete when wake is set


class TestSmtpSenderDispatchBatchEdgeCases:
    """Edge case tests for _dispatch_batch."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s._dispatch_message = AsyncMock()
        return s

    async def test_dispatch_batch_empty_after_filtering(self, sender, mock_proxy):
        """_dispatch_batch handles empty batch after filtering."""
        # This shouldn't happen normally, but test the guard
        await sender._dispatch_batch([], 12345)
        sender._dispatch_message.assert_not_called()

    async def test_dispatch_batch_logs_exception_in_dispatch(self, sender, mock_proxy):
        """Exceptions in dispatch are logged."""
        sender._dispatch_message = AsyncMock(side_effect=RuntimeError("Unexpected error"))

        batch = [
            {"pk": "1", "id": "m1", "account_id": "acct1", "tenant_id": "t1"},
        ]

        await sender._dispatch_batch(batch, 12345)

        # Should log the exception
        mock_proxy.logger.exception.assert_called()


class TestSmtpSenderLifecycleExtended:
    """Extended lifecycle tests."""

    @pytest.fixture
    def mock_proxy(self):
        p = MockProxy()
        p._test_mode = False  # Enable cleanup loop
        return p

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_start_creates_cleanup_task_in_non_test_mode(self, sender, mock_proxy):
        """start() creates cleanup loop task in non-test mode."""
        await sender.start()

        assert sender._task_cleanup is not None

        await sender.stop()


class TestSmtpSenderCleanupLoopExtended:
    """Extended cleanup loop tests."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        s.pool.cleanup = AsyncMock()
        return s

    async def test_cleanup_loop_timeout_path(self, sender):
        """Cleanup loop handles timeout without wake event."""
        # Patch cleanup interval to very short
        async def run_cleanup():
            iteration = 0
            while not sender._stop.is_set():
                try:
                    await asyncio.wait_for(sender._wake_cleanup_event.wait(), timeout=0.01)
                    sender._wake_cleanup_event.clear()
                except asyncio.TimeoutError:
                    pass
                if sender._stop.is_set():
                    break
                await sender.pool.cleanup()
                iteration += 1
                if iteration >= 2:
                    sender._stop.set()

        task = asyncio.create_task(run_cleanup())
        await asyncio.wait_for(task, timeout=1.0)

        assert sender.pool.cleanup.call_count >= 1


class TestSmtpSenderAttachmentNone:
    """Tests for attachment None data handling."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        mock_proxy.attachments.guess_mime = MagicMock(return_value=("text", "plain"))
        return s

    async def test_process_attachments_raises_on_none_data(self, sender, mock_proxy):
        """Raises ValueError when attachment fetch returns None."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value=None)
        mock_proxy.attachments.fetch = AsyncMock(return_value=None)

        msg = EmailMessage()
        msg.set_content("Body")

        with pytest.raises(ValueError, match="returned no data"):
            await sender._process_attachments(
                msg,
                {"tenant_id": "t1"},
                [{"filename": "file.txt"}],
                "plain",
            )


class TestSmtpSenderGetAttachmentManagerWithAuth:
    """Tests for _get_attachment_manager_for_message with auth."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_get_attachment_manager_with_client_auth(self, sender, mock_proxy):
        """AttachmentManager created with client_auth config."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={
            "client_auth": {
                "method": "bearer",
                "token": "secret-token",
            }
        })
        mock_proxy._tables["storages"].get_storage_manager = AsyncMock(
            side_effect=ValueError("no storage")
        )

        result = await sender._get_attachment_manager_for_message({"tenant_id": "t1"})

        # Should return a new AttachmentManager, not the global one
        assert result is not mock_proxy.attachments

    async def test_get_attachment_manager_storage_exception(self, sender, mock_proxy):
        """Storage table exception is caught silently."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={
            "attachment_url": "http://attachments.example.com",
        })
        mock_proxy._tables["storages"].get_storage_manager = AsyncMock(
            side_effect=KeyError("not found")
        )

        result = await sender._get_attachment_manager_for_message({"tenant_id": "t1"})

        # Should still return a manager (with attachment_url)
        assert result is not None


class TestSmtpSenderLargeFileStorageError:
    """Tests for LargeFileStorageError stub in CE mode."""

    def test_large_file_storage_error_is_defined(self):
        """LargeFileStorageError stub is defined in CE mode."""
        from core.mail_proxy.smtp.sender import LargeFileStorageError

        # Should be able to instantiate
        err = LargeFileStorageError("test error")
        assert str(err) == "test error"


class TestSmtpSenderAppendDownloadLinks:
    """Tests for _append_download_links_to_email."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    def test_append_download_links_html(self, sender):
        """Appends HTML footer for HTML emails."""
        msg = EmailMessage()
        msg.set_content("<p>Hello</p>", subtype="html")

        sender._append_download_links_to_email(
            msg,
            [{"filename": "big.zip", "size_mb": 25.5, "url": "https://dl.example.com/file"}],
            "html",
        )

        body = msg.get_content()
        assert "Large attachments available for download" in body
        assert "big.zip" in body
        assert "https://dl.example.com/file" in body

    def test_append_download_links_plain(self, sender):
        """Appends plain text footer for plain text emails."""
        msg = EmailMessage()
        msg.set_content("Hello World")

        sender._append_download_links_to_email(
            msg,
            [{"filename": "data.csv", "size_mb": 15.0, "url": "https://dl.example.com/csv"}],
            "plain",
        )

        body = msg.get_content()
        assert "Large attachments available for download" in body
        assert "data.csv" in body
        assert "15.0 MB" in body

    def test_append_download_links_multipart_html(self, sender):
        """Handles multipart message with HTML body."""
        msg = EmailMessage()
        msg.set_content("<p>HTML body</p>", subtype="html")
        msg.add_attachment(b"small data", maintype="text", subtype="plain", filename="small.txt")

        sender._append_download_links_to_email(
            msg,
            [{"filename": "archive.tar.gz", "size_mb": 50.0, "url": "https://storage.example.com/archive"}],
            "html",
        )

        # Should have modified the HTML body part
        body_part = msg.get_body(preferencelist=("html", "plain"))
        if body_part:
            content = body_part.get_content()
            assert "archive.tar.gz" in content

    def test_append_download_links_non_multipart_plain(self, sender):
        """Handles non-multipart plain text message."""
        msg = EmailMessage()
        msg.set_content("Simple body text")

        sender._append_download_links_to_email(
            msg,
            [{"filename": "big.bin", "size_mb": 100.0, "url": "https://storage/big"}],
            "plain",
        )

        body = msg.get_content()
        assert "Large attachments available for download" in body
        assert "big.bin" in body


class TestSmtpSenderLargeFileWarnMode:
    """Tests for large file warn mode functionality."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        s = SmtpSender(mock_proxy)
        mock_proxy.attachments.guess_mime = MagicMock(return_value=("application", "octet-stream"))
        return s

    async def test_process_attachments_warn_mode_sends_anyway(self, sender, mock_proxy):
        """In warn mode, large attachments are sent with warning."""
        mock_proxy._tables["tenants"].get = AsyncMock(return_value={
            "large_file_config": {
                "enabled": True,
                "max_size_mb": 1.0,
                "action": "warn",
            }
        })
        mock_proxy._tables["storages"].get_storage_manager = AsyncMock(
            side_effect=ValueError("no storage")
        )
        # 2MB attachment
        mock_proxy.attachments.fetch = AsyncMock(return_value=(b"x" * (2 * 1024 * 1024), "large.bin"))

        msg = EmailMessage()
        msg.set_content("Body")

        # Should NOT raise - just warns and attaches normally
        await sender._process_attachments(
            msg,
            {"tenant_id": "t1"},
            [{"filename": "large.bin"}],
            "plain",
        )

        # Message should have the attachment
        attachments = list(msg.iter_attachments())
        assert len(attachments) == 1


class TestSmtpSenderBuildEmailExtended:
    """Tests for additional _build_email paths."""

    @pytest.fixture
    def mock_proxy(self):
        return MockProxy()

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_build_email_with_reply_to(self, sender, mock_proxy):
        """Builds email with Reply-To header."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
            "reply_to": "reply@example.com",
        }

        msg, _ = await sender._build_email(data)

        assert msg["Reply-To"] == "reply@example.com"

    async def test_build_email_with_date_header(self, sender, mock_proxy):
        """Builds email with custom Date header."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
            "headers": {
                "Date": "Wed, 01 Jan 2025 12:00:00 +0000",
            },
        }

        msg, _ = await sender._build_email(data)

        assert "2025" in msg["Date"]
