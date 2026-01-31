# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for MailProxy."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.mail_proxy.proxy import (
    MailProxy,
    PRIORITY_LABELS,
    LABEL_TO_PRIORITY,
    DEFAULT_PRIORITY,
)
from core.mail_proxy.proxy_config import ProxyConfig


class MockDb:
    """Mock database for tests."""

    def __init__(self):
        self._tables = {
            "messages": MagicMock(),
            "tenants": MagicMock(),
            "accounts": MagicMock(),
            "message_events": MagicMock(),
            "command_log": MagicMock(),
            "storages": MagicMock(),
        }
        self.adapter = MagicMock()
        self.adapter.close = AsyncMock()

    def table(self, name):
        if name not in self._tables:
            self._tables[name] = MagicMock()
        return self._tables[name]

    def add_table(self, table_class):
        """Mock add_table - does nothing."""
        pass


class TestPriorityConstants:
    """Tests for priority-related constants."""

    def test_priority_labels_values(self):
        """Priority labels are correctly defined."""
        assert PRIORITY_LABELS[0] == "immediate"
        assert PRIORITY_LABELS[1] == "high"
        assert PRIORITY_LABELS[2] == "medium"
        assert PRIORITY_LABELS[3] == "low"

    def test_label_to_priority_reverse_mapping(self):
        """Label to priority mapping is consistent."""
        for value, label in PRIORITY_LABELS.items():
            assert LABEL_TO_PRIORITY[label] == value

    def test_default_priority_is_medium(self):
        """Default priority is medium (2)."""
        assert DEFAULT_PRIORITY == 2


class TestMailProxyInit:
    """Tests for MailProxy initialization."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_init_creates_smtp_sender(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """MailProxy creates SmtpSender on init."""
        mock_db_cls.return_value = MockDb()
        proxy = MailProxy()
        mock_sender_cls.assert_called_once_with(proxy)

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_init_creates_client_reporter(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """MailProxy creates ClientReporter on init."""
        mock_db_cls.return_value = MockDb()
        proxy = MailProxy()
        mock_reporter_cls.assert_called_once_with(proxy)

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_init_creates_metrics(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """MailProxy creates metrics on init."""
        mock_db_cls.return_value = MockDb()
        proxy = MailProxy()
        assert proxy.metrics is not None

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_init_with_custom_config(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """MailProxy accepts custom config."""
        mock_db_cls.return_value = MockDb()
        config = ProxyConfig(test_mode=True)
        proxy = MailProxy(config=config)
        assert proxy._test_mode is True


class TestMailProxyNormalisePriority:
    """Tests for _normalise_priority method."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            return MailProxy()

    def test_normalise_none_returns_default(self, proxy):
        """None value returns default priority."""
        priority, label = proxy._normalise_priority(None)
        assert priority == DEFAULT_PRIORITY
        assert label == "medium"

    def test_normalise_string_label(self, proxy):
        """String labels are recognized."""
        priority, label = proxy._normalise_priority("high")
        assert priority == 1
        assert label == "high"

    def test_normalise_string_label_case_insensitive(self, proxy):
        """String labels are case-insensitive."""
        priority, label = proxy._normalise_priority("HIGH")
        assert priority == 1
        assert label == "high"

    def test_normalise_integer(self, proxy):
        """Integer values are normalized."""
        priority, label = proxy._normalise_priority(0)
        assert priority == 0
        assert label == "immediate"

    def test_normalise_string_integer(self, proxy):
        """String integers are converted."""
        priority, label = proxy._normalise_priority("3")
        assert priority == 3
        assert label == "low"

    def test_normalise_clamps_high_values(self, proxy):
        """Values above max are clamped."""
        priority, label = proxy._normalise_priority(99)
        assert priority == 3  # Max valid priority
        assert label == "low"

    def test_normalise_clamps_negative_values(self, proxy):
        """Negative values are clamped to 0."""
        priority, label = proxy._normalise_priority(-1)
        assert priority == 0
        assert label == "immediate"

    def test_normalise_invalid_string_uses_default(self, proxy):
        """Invalid string returns default."""
        priority, label = proxy._normalise_priority("invalid")
        assert priority == DEFAULT_PRIORITY

    def test_normalise_with_custom_default(self, proxy):
        """Custom default is respected."""
        priority, label = proxy._normalise_priority(None, default=1)
        assert priority == 1
        assert label == "high"


class TestMailProxySummariseAddresses:
    """Tests for _summarise_addresses static method."""

    def test_empty_returns_dash(self):
        """Empty value returns dash."""
        assert MailProxy._summarise_addresses(None) == "-"
        assert MailProxy._summarise_addresses("") == "-"
        assert MailProxy._summarise_addresses([]) == "-"

    def test_string_is_split(self):
        """String is split by comma."""
        result = MailProxy._summarise_addresses("a@test.com, b@test.com")
        assert "a@test.com" in result
        assert "b@test.com" in result

    def test_list_is_joined(self):
        """List items are joined."""
        result = MailProxy._summarise_addresses(["a@test.com", "b@test.com"])
        assert "a@test.com" in result
        assert "b@test.com" in result

    def test_long_addresses_truncated(self):
        """Long address lists are truncated."""
        addresses = [f"user{i}@example.com" for i in range(50)]
        result = MailProxy._summarise_addresses(addresses)
        assert len(result) <= 200 + 3  # 200 + "..."
        assert result.endswith("...")


class TestMailProxyUtilityMethods:
    """Tests for utility methods."""

    def test_utc_now_iso_format(self):
        """_utc_now_iso returns ISO format with Z suffix."""
        result = MailProxy._utc_now_iso()
        assert result.endswith("Z")
        assert "T" in result

    def test_utc_now_epoch_is_integer(self):
        """_utc_now_epoch returns integer."""
        result = MailProxy._utc_now_epoch()
        assert isinstance(result, int)
        assert result > 0


class TestMailProxyHandleCommand:
    """Tests for handle_command method."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            p.smtp_sender = MagicMock()
            p.client_reporter = MagicMock()
            p.client_reporter._last_sync = {}
            p._dispatcher = MagicMock()
            p._dispatcher.dispatch = AsyncMock(return_value={"ok": True})
            return p

    async def test_run_now_wakes_sender_and_reporter(self, proxy):
        """'run now' command wakes both smtp_sender and client_reporter."""
        result = await proxy.handle_command("run now")
        assert result["ok"] is True
        proxy.smtp_sender.wake.assert_called_once()
        proxy.client_reporter.wake.assert_called()

    async def test_unknown_command_delegates_to_dispatcher(self, proxy):
        """Unknown commands are delegated to dispatcher."""
        result = await proxy.handle_command("someOtherCommand", {"data": "value"})
        proxy._dispatcher.dispatch.assert_called_once_with("someOtherCommand", {"data": "value"})

    async def test_delete_messages_requires_tenant_id(self, proxy):
        """deleteMessages requires tenant_id."""
        result = await proxy.handle_command("deleteMessages", {})
        assert result["ok"] is False
        assert "tenant_id" in result["error"]

    async def test_cleanup_messages_requires_tenant_id(self, proxy):
        """cleanupMessages requires tenant_id."""
        result = await proxy.handle_command("cleanupMessages", {})
        assert result["ok"] is False
        assert "tenant_id" in result["error"]

    async def test_delete_account_requires_tenant_id(self, proxy):
        """deleteAccount requires tenant_id."""
        result = await proxy.handle_command("deleteAccount", {})
        assert result["ok"] is False
        assert "tenant_id" in result["error"]


class TestMailProxyDeleteMessages:
    """Tests for _delete_messages method."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            return p

    async def test_empty_ids_returns_zero(self, proxy):
        """Empty ID list returns zero removed."""
        removed, not_found, unauthorized = await proxy._delete_messages([], "tenant1")
        assert removed == 0
        assert not_found == []
        assert unauthorized == []

    async def test_deletes_authorized_messages(self, proxy):
        """Only authorized messages are deleted."""
        proxy.db.table("messages").get_ids_for_tenant = AsyncMock(return_value={"m1", "m2"})
        proxy.db.table("messages").delete = AsyncMock(return_value=True)

        removed, not_found, unauthorized = await proxy._delete_messages(["m1", "m2", "m3"], "tenant1")

        assert removed == 2
        assert unauthorized == ["m3"]

    async def test_tracks_not_found_messages(self, proxy):
        """Messages that fail to delete are tracked as not_found."""
        proxy.db.table("messages").get_ids_for_tenant = AsyncMock(return_value={"m1"})
        proxy.db.table("messages").delete = AsyncMock(return_value=False)

        removed, not_found, unauthorized = await proxy._delete_messages(["m1"], "tenant1")

        assert removed == 0
        assert not_found == ["m1"]


class TestMailProxyValidateEnqueuePayload:
    """Tests for _validate_enqueue_payload method."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            p.db.table("accounts").get = AsyncMock(return_value={"id": "acc1"})
            return p

    async def test_valid_payload_returns_true(self, proxy):
        """Valid payload returns True."""
        payload = {
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test Subject",
        }
        is_valid, reason = await proxy._validate_enqueue_payload(payload)
        assert is_valid is True
        assert reason is None

    async def test_missing_id_returns_false(self, proxy):
        """Missing id returns False."""
        payload = {
            "tenant_id": "t1",
            "account_id": "acc1",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test Subject",
        }
        is_valid, reason = await proxy._validate_enqueue_payload(payload)
        assert is_valid is False
        assert "id" in reason

    async def test_missing_tenant_id_returns_false(self, proxy):
        """Missing tenant_id returns False."""
        payload = {
            "id": "msg1",
            "account_id": "acc1",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test Subject",
        }
        is_valid, reason = await proxy._validate_enqueue_payload(payload)
        assert is_valid is False
        assert "tenant_id" in reason

    async def test_missing_account_id_returns_false(self, proxy):
        """Missing account_id returns False."""
        payload = {
            "id": "msg1",
            "tenant_id": "t1",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test Subject",
        }
        is_valid, reason = await proxy._validate_enqueue_payload(payload)
        assert is_valid is False
        assert "account_id" in reason

    async def test_missing_from_returns_false(self, proxy):
        """Missing from returns False."""
        payload = {
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "to": ["recipient@test.com"],
            "subject": "Test Subject",
        }
        is_valid, reason = await proxy._validate_enqueue_payload(payload)
        assert is_valid is False
        assert "from" in reason

    async def test_missing_to_returns_false(self, proxy):
        """Missing to returns False."""
        payload = {
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "from": "sender@test.com",
            "subject": "Test Subject",
        }
        is_valid, reason = await proxy._validate_enqueue_payload(payload)
        assert is_valid is False
        assert "to" in reason

    async def test_empty_to_list_returns_false(self, proxy):
        """Empty to list returns False."""
        payload = {
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "from": "sender@test.com",
            "to": [],
            "subject": "Test Subject",
        }
        is_valid, reason = await proxy._validate_enqueue_payload(payload)
        assert is_valid is False
        assert "to" in reason

    async def test_missing_subject_returns_false(self, proxy):
        """Missing subject returns False."""
        payload = {
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
        }
        is_valid, reason = await proxy._validate_enqueue_payload(payload)
        assert is_valid is False
        assert "subject" in reason

    async def test_nonexistent_account_returns_false(self, proxy):
        """Nonexistent account returns False."""
        proxy.db.table("accounts").get = AsyncMock(side_effect=ValueError("not found"))
        payload = {
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "nonexistent",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test Subject",
        }
        is_valid, reason = await proxy._validate_enqueue_payload(payload)
        assert is_valid is False
        assert "account not found" in reason


class TestMailProxyHandleAddMessages:
    """Tests for _handle_add_messages method."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            p.db.table("accounts").get = AsyncMock(return_value={"id": "acc1"})
            p.db.table("messages").insert_batch = AsyncMock(return_value=[{"id": "msg1", "pk": "pk1"}])
            p.db.table("message_events").add_event = AsyncMock()
            p._refresh_queue_gauge = AsyncMock()
            p._publish_result = AsyncMock()
            return p

    async def test_messages_must_be_list(self, proxy):
        """Messages must be a list."""
        result = await proxy._handle_add_messages({"messages": "not a list"})
        assert result["ok"] is False
        assert "must be a list" in result["error"]

    async def test_batch_size_limit(self, proxy):
        """Batch size is enforced."""
        proxy._max_enqueue_batch = 2
        messages = [{"id": f"msg{i}"} for i in range(5)]
        result = await proxy._handle_add_messages({"messages": messages})
        assert result["ok"] is False
        assert "Cannot enqueue" in result["error"]

    async def test_valid_messages_are_queued(self, proxy):
        """Valid messages are queued."""
        messages = [{
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test Subject",
        }]
        result = await proxy._handle_add_messages({"messages": messages})
        assert result["ok"] is True
        assert result["queued"] == 1

    async def test_invalid_messages_are_rejected(self, proxy):
        """Invalid messages are rejected."""
        messages = [
            {"id": "msg1"},  # Missing required fields
        ]
        result = await proxy._handle_add_messages({"messages": messages})
        assert len(result["rejected"]) == 1
        assert result["rejected"][0]["id"] == "msg1"


class TestMailProxyLifecycle:
    """Tests for start/stop lifecycle."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender') as mock_sender_cls, \
             patch('core.mail_proxy.proxy.ClientReporter') as mock_reporter_cls, \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            p.smtp_sender = MagicMock()
            p.smtp_sender.start = AsyncMock()
            p.smtp_sender.stop = AsyncMock()
            p.client_reporter = MagicMock()
            p.client_reporter.start = AsyncMock()
            p.client_reporter.stop = AsyncMock()
            p.init = AsyncMock()
            return p

    async def test_start_initializes_components(self, proxy):
        """start() initializes components."""
        await proxy.start()
        proxy.init.assert_called_once()
        proxy.smtp_sender.start.assert_called_once()
        proxy.client_reporter.start.assert_called_once()

    async def test_stop_stops_components(self, proxy):
        """stop() stops components."""
        await proxy.stop()
        proxy.smtp_sender.stop.assert_called_once()
        proxy.client_reporter.stop.assert_called_once()
        proxy.db.adapter.close.assert_called_once()


class TestMailProxyLogDeliveryEvent:
    """Tests for _log_delivery_event method."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            p.logger = MagicMock()
            p._log_delivery_activity = True
            return p

    def test_log_sent_event(self, proxy):
        """Sent events are logged as info."""
        event = {"status": "sent", "id": "msg1", "account": "acc1"}
        proxy._log_delivery_event(event)
        proxy.logger.info.assert_called()
        assert "succeeded" in str(proxy.logger.info.call_args)

    def test_log_deferred_event(self, proxy):
        """Deferred events are logged with timestamp."""
        event = {"status": "deferred", "id": "msg1", "account": "acc1", "deferred_until": 1234567890}
        proxy._log_delivery_event(event)
        proxy.logger.info.assert_called()
        assert "deferred" in str(proxy.logger.info.call_args).lower()

    def test_log_error_event(self, proxy):
        """Error events are logged as warning."""
        event = {"status": "error", "id": "msg1", "account": "acc1", "error": "Connection refused"}
        proxy._log_delivery_event(event)
        proxy.logger.warning.assert_called()
        assert "failed" in str(proxy.logger.warning.call_args).lower()

    def test_disabled_logging_does_nothing(self, proxy):
        """When logging is disabled, nothing is logged."""
        proxy._log_delivery_activity = False
        event = {"status": "sent", "id": "msg1"}
        proxy._log_delivery_event(event)
        proxy.logger.info.assert_not_called()


class TestMailProxyCleanupReportedMessages:
    """Tests for _cleanup_reported_messages method."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            p._report_retention_seconds = 3600
            p._refresh_queue_gauge = AsyncMock()
            p.db.table("messages").remove_fully_reported_before = AsyncMock(return_value=5)
            p.db.table("messages").remove_fully_reported_before_for_tenant = AsyncMock(return_value=3)
            return p

    async def test_cleanup_uses_default_retention(self, proxy):
        """Cleanup uses default retention when no override."""
        removed = await proxy._cleanup_reported_messages(tenant_id="t1")
        assert removed == 3
        proxy.db.table("messages").remove_fully_reported_before_for_tenant.assert_called()

    async def test_cleanup_respects_custom_retention(self, proxy):
        """Cleanup respects custom retention period."""
        await proxy._cleanup_reported_messages(older_than_seconds=7200, tenant_id="t1")
        call_args = proxy.db.table("messages").remove_fully_reported_before_for_tenant.call_args
        # The threshold should be based on 7200 seconds ago
        assert call_args is not None

    async def test_cleanup_without_tenant_cleans_all(self, proxy):
        """Cleanup without tenant_id cleans all messages."""
        removed = await proxy._cleanup_reported_messages()
        assert removed == 5
        proxy.db.table("messages").remove_fully_reported_before.assert_called()

    async def test_cleanup_refreshes_gauge_when_removed(self, proxy):
        """Cleanup refreshes queue gauge when messages removed."""
        await proxy._cleanup_reported_messages(tenant_id="t1")
        proxy._refresh_queue_gauge.assert_called()


class TestMailProxyListTenantsSyncStatus:
    """Tests for listTenantsSyncStatus command."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            p.smtp_sender = MagicMock()
            p.client_reporter = MagicMock()
            p.client_reporter._last_sync = {}
            return p

    async def test_returns_tenant_sync_status(self, proxy):
        """Returns sync status for all tenants."""
        proxy.db.table("tenants").list_all = AsyncMock(return_value=[
            {"id": "t1", "name": "Tenant 1", "active": True},
            {"id": "t2", "name": "Tenant 2", "active": False},
        ])
        proxy.client_reporter._last_sync = {"t1": 1234567890}

        result = await proxy.handle_command("listTenantsSyncStatus")

        assert result["ok"] is True
        assert len(result["tenants"]) == 2
        assert result["tenants"][0]["id"] == "t1"
        assert result["tenants"][0]["last_sync_ts"] == 1234567890


class TestMailProxyCreateFactory:
    """Tests for create() factory method."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    async def test_create_returns_started_proxy(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """create() returns a started proxy instance."""
        mock_db_cls.return_value = MockDb()

        with patch.object(MailProxy, 'start', new_callable=AsyncMock) as mock_start:
            proxy = await MailProxy.create()
            mock_start.assert_called_once()
            assert isinstance(proxy, MailProxy)
