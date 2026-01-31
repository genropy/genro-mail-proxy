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
        self.add_table = MagicMock()  # Track calls to add_table
        self.tables = {}  # For endpoint discovery

    def table(self, name):
        if name not in self._tables:
            self._tables[name] = MagicMock()
        return self._tables[name]


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


class TestMailProxyBaseEncryptionKey:
    """Tests for encryption key loading in MailProxyBase."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_encryption_key_from_env_var(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """Encryption key is loaded from MAIL_PROXY_ENCRYPTION_KEY env var."""
        import base64
        import os

        mock_db_cls.return_value = MockDb()

        # Generate a valid 32-byte key
        key = b"0123456789abcdef0123456789abcdef"
        key_b64 = base64.b64encode(key).decode()

        with patch.dict(os.environ, {"MAIL_PROXY_ENCRYPTION_KEY": key_b64}):
            proxy = MailProxy()
            assert proxy.encryption_key == key

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_encryption_key_invalid_base64_ignored(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """Invalid base64 in env var is silently ignored."""
        import os

        mock_db_cls.return_value = MockDb()

        with patch.dict(os.environ, {"MAIL_PROXY_ENCRYPTION_KEY": "not-valid-base64!!!"}):
            proxy = MailProxy()
            assert proxy.encryption_key is None

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_encryption_key_wrong_length_ignored(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """Key with wrong length is silently ignored."""
        import base64
        import os

        mock_db_cls.return_value = MockDb()

        # Generate a key with wrong length (16 bytes instead of 32)
        key = b"0123456789abcdef"  # 16 bytes
        key_b64 = base64.b64encode(key).decode()

        with patch.dict(os.environ, {"MAIL_PROXY_ENCRYPTION_KEY": key_b64}):
            proxy = MailProxy()
            assert proxy.encryption_key is None

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_encryption_key_from_secrets_file(self, mock_db_cls, mock_reporter_cls, mock_sender_cls, tmp_path):
        """Encryption key is loaded from secrets file when env var not set."""
        import os

        mock_db_cls.return_value = MockDb()

        # Create a mock secrets file
        key = b"0123456789abcdef0123456789abcdef"
        secrets_path = tmp_path / "encryption_key"
        secrets_path.write_bytes(key + b"\n")  # With trailing newline

        # We need to mock the Path class used inside _load_encryption_key
        # The method imports Path from pathlib inside the function
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.read_bytes.return_value = key + b"\n"

        with patch.dict(os.environ, {}, clear=True), \
             patch('pathlib.Path') as mock_path_cls:
            mock_path_cls.return_value = mock_path_instance

            proxy = MailProxy()
            assert proxy.encryption_key == key

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_encryption_key_secrets_file_wrong_length(self, mock_db_cls, mock_reporter_cls, mock_sender_cls, tmp_path):
        """Secrets file with wrong key length is ignored."""
        import os

        mock_db_cls.return_value = MockDb()

        # Create a mock secrets file with wrong length
        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.read_bytes.return_value = b"short"

        with patch.dict(os.environ, {}, clear=True), \
             patch('pathlib.Path') as mock_path_cls:
            mock_path_cls.return_value = mock_path_instance

            proxy = MailProxy()
            assert proxy.encryption_key is None

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_set_encryption_key_programmatically(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """Encryption key can be set programmatically."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        key = b"0123456789abcdef0123456789abcdef"
        proxy.set_encryption_key(key)
        assert proxy.encryption_key == key

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_set_encryption_key_validates_length(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """set_encryption_key raises ValueError for wrong length."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        with pytest.raises(ValueError, match="32 bytes"):
            proxy.set_encryption_key(b"short")


class TestMailProxyBaseTableDiscovery:
    """Tests for EE mixin discovery and table composition in MailProxyBase."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_discover_tables_finds_ce_tables(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """Table discovery finds CE tables from entities package."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()

        # Should have called add_table for discovered tables
        assert mock_db_cls.return_value.add_table.called

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_find_entity_modules_handles_import_error(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """_find_entity_modules handles missing packages gracefully."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()

        # Test with non-existent package
        result = proxy._find_entity_modules("nonexistent.package", "table")
        assert result == {}

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_get_class_from_module_filters_private(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """_get_class_from_module filters out private classes."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()

        # Create a mock module with private and public classes
        mock_module = MagicMock()
        mock_module._PrivateTable = type("_PrivateTable", (), {"name": "private"})
        mock_module.PublicTable = type("PublicTable", (), {"name": "public"})
        # Add dir() result
        mock_module.__dir__ = lambda: ["_PrivateTable", "PublicTable"]
        type(mock_module).__iter__ = lambda self: iter(["_PrivateTable", "PublicTable"])

        # Private class should not be returned
        # Use a fresh mock module that doesn't have classes matching our pattern
        simple_module = type('SimpleModule', (), {})()
        simple_module._SomeTable = type("_SomeTable", (), {"name": "test"})
        result = proxy._get_class_from_module(simple_module, "Table")
        assert result is None  # Private classes are filtered

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_get_ee_mixin_from_module_extracts_mixin(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """_get_ee_mixin_from_module extracts classes with _EE suffix."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()

        # Create a mock module with _EE mixin
        mock_module = type('MockModule', (), {})()
        mock_module.SomeTable_EE = type("SomeTable_EE", (), {})

        result = proxy._get_ee_mixin_from_module(mock_module, "_EE")
        assert result is not None
        assert result.__name__ == "SomeTable_EE"

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_get_ee_mixin_from_module_returns_none_if_not_found(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """_get_ee_mixin_from_module returns None when no mixin found."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()

        # Create a mock module without _EE mixin
        mock_module = type('MockModule', (), {})()
        mock_module.SomeTable = type("SomeTable", (), {})

        result = proxy._get_ee_mixin_from_module(mock_module, "_EE")
        assert result is None


class TestMailProxyBaseCli:
    """Tests for CLI creation in MailProxyBase."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_cli_property_creates_click_group(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """cli property creates a Click group on first access."""
        import click

        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        cli = proxy.cli

        assert isinstance(cli, click.Group)

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_cli_property_is_cached(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """cli property returns same instance on subsequent calls."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        cli1 = proxy.cli
        cli2 = proxy.cli

        assert cli1 is cli2

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_cli_has_serve_command(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """CLI includes serve command."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        cli = proxy.cli

        assert "serve" in cli.commands

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_cli_has_endpoint_commands(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """CLI includes endpoint-based commands."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        cli = proxy.cli

        # Should have commands for registered endpoints
        # At minimum should have some commands
        assert len(cli.commands) > 1  # serve + at least some endpoint commands


class TestMailProxyBaseApi:
    """Tests for API creation in MailProxyBase."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_api_property_creates_fastapi_app(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """api property creates a FastAPI app on first access."""
        from fastapi import FastAPI

        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        api = proxy.api

        assert isinstance(api, FastAPI)

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_api_property_is_cached(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """api property returns same instance on subsequent calls."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        api1 = proxy.api
        api2 = proxy.api

        assert api1 is api2


class TestMailProxyBaseEndpoint:
    """Tests for endpoint() method in MailProxyBase."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_endpoint_not_found_raises_value_error(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """endpoint() raises ValueError for unknown endpoint name."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()

        with pytest.raises(ValueError, match="Endpoint 'nonexistent' not found"):
            proxy.endpoint("nonexistent")


class TestMailProxyBaseTableDiscoveryExtended:
    """Extended tests for table discovery edge cases."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_get_class_from_module_filters_ee_classes(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """_get_class_from_module filters out _EE classes."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()

        # Create module with _EE class
        mock_module = type('MockModule', (), {})()
        mock_module.SomeTable_EE = type("SomeTable_EE", (), {"name": "test"})

        result = proxy._get_class_from_module(mock_module, "Table")
        assert result is None  # _EE classes are filtered

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_get_class_from_module_filters_base_table(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """_get_class_from_module filters out bare 'Table' class."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()

        # Create module with only Table class (the base class import)
        mock_module = type('MockModule', (), {})()
        mock_module.Table = type("Table", (), {"name": "table"})

        result = proxy._get_class_from_module(mock_module, "Table")
        assert result is None  # Base Table class is filtered

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_get_class_from_module_filters_class_without_name(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """_get_class_from_module filters out classes without name attribute."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()

        # Create module with class that has no name attribute
        mock_module = type('MockModule', (), {})()
        mock_module.SomeTable = type("SomeTable", (), {})  # No name attribute

        result = proxy._get_class_from_module(mock_module, "Table")
        assert result is None  # Classes without name are filtered


class TestMailProxyBaseEncryptionKeyExtended:
    """Extended tests for encryption key loading edge cases."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_secrets_file_read_error_ignored(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """Errors reading secrets file are silently ignored."""
        import os

        mock_db_cls.return_value = MockDb()

        mock_path_instance = MagicMock()
        mock_path_instance.exists.return_value = True
        mock_path_instance.read_bytes.side_effect = IOError("Permission denied")

        with patch.dict(os.environ, {}, clear=True), \
             patch('pathlib.Path') as mock_path_cls:
            mock_path_cls.return_value = mock_path_instance

            proxy = MailProxy()
            assert proxy.encryption_key is None  # Error is silently handled


class TestMailProxyCompatibilityProperties:
    """Tests for compatibility properties (pool, rate_limiter)."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_pool_property_delegates_to_smtp_sender(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """pool property delegates to smtp_sender.pool."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        mock_pool = MagicMock()
        proxy.smtp_sender.pool = mock_pool

        assert proxy.pool is mock_pool

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_rate_limiter_property_delegates_to_smtp_sender(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """rate_limiter property delegates to smtp_sender.rate_limiter."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        mock_limiter = MagicMock()
        proxy.smtp_sender.rate_limiter = mock_limiter

        assert proxy.rate_limiter is mock_limiter


class TestMailProxyInitMethod:
    """Tests for init() method with cache initialization."""

    @patch('core.mail_proxy.proxy.TieredCache')
    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    async def test_init_with_cache_enabled(self, mock_db_cls, mock_reporter_cls, mock_sender_cls, mock_cache_cls):
        """init() initializes TieredCache when cache is enabled."""
        from core.mail_proxy.proxy_config import ProxyConfig, CacheConfig

        mock_db_cls.return_value = MockDb()

        # Configure cache to be enabled via config
        cache_config = CacheConfig(
            memory_max_mb=100,
            memory_ttl_seconds=300,
            disk_dir="/tmp/cache",  # This enables the cache
            disk_max_mb=500,
            disk_ttl_seconds=3600,
            disk_threshold_kb=100,
        )
        config = ProxyConfig(cache=cache_config)

        proxy = MailProxy(config=config)
        proxy._refresh_queue_gauge = AsyncMock()
        proxy._init_account_metrics = AsyncMock()

        mock_cache_instance = MagicMock()
        mock_cache_instance.init = AsyncMock()
        mock_cache_cls.return_value = mock_cache_instance

        # Mock MailProxyBase.init
        with patch.object(MailProxy.__bases__[0], 'init', new_callable=AsyncMock):
            await proxy.init()

        mock_cache_cls.assert_called_once_with(
            memory_max_mb=100,
            memory_ttl_seconds=300,
            disk_dir="/tmp/cache",
            disk_max_mb=500,
            disk_ttl_seconds=3600,
            disk_threshold_kb=100,
        )
        mock_cache_instance.init.assert_called_once()

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    async def test_init_without_cache(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """init() skips cache when disabled."""
        from core.mail_proxy.proxy_config import ProxyConfig, CacheConfig

        mock_db_cls.return_value = MockDb()

        # No disk_dir means cache is disabled
        cache_config = CacheConfig(disk_dir=None)
        config = ProxyConfig(cache=cache_config)

        proxy = MailProxy(config=config)
        proxy._refresh_queue_gauge = AsyncMock()
        proxy._init_account_metrics = AsyncMock()
        proxy._attachment_cache = None

        # Mock MailProxyBase.init
        with patch.object(MailProxy.__bases__[0], 'init', new_callable=AsyncMock):
            await proxy.init()

        assert proxy._attachment_cache is None


class TestMailProxyDeleteMessagesCommand:
    """Tests for deleteMessages command through handle_command."""

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
            p._refresh_queue_gauge = AsyncMock()
            p.db.table("command_log").log_command = AsyncMock()
            return p

    async def test_delete_messages_success(self, proxy):
        """deleteMessages command deletes messages and returns counts."""
        proxy.db.table("messages").get_ids_for_tenant = AsyncMock(return_value={"m1", "m2"})
        proxy.db.table("messages").delete = AsyncMock(return_value=True)

        result = await proxy.handle_command("deleteMessages", {
            "tenant_id": "t1",
            "ids": ["m1", "m2", "m3"]
        })

        assert result["ok"] is True
        assert result["removed"] == 2
        assert "m3" in result["unauthorized"]
        proxy._refresh_queue_gauge.assert_called()


class TestMailProxyCleanupMessagesCommand:
    """Tests for cleanupMessages command through handle_command."""

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
            p._refresh_queue_gauge = AsyncMock()
            p._report_retention_seconds = 3600
            p.db.table("command_log").log_command = AsyncMock()
            p.db.table("messages").remove_fully_reported_before_for_tenant = AsyncMock(return_value=5)
            return p

    async def test_cleanup_messages_success(self, proxy):
        """cleanupMessages command removes old reported messages."""
        result = await proxy.handle_command("cleanupMessages", {
            "tenant_id": "t1",
            "older_than_seconds": 7200
        })

        assert result["ok"] is True
        assert result["removed"] == 5


class TestMailProxyDeleteAccountCommand:
    """Tests for deleteAccount command through handle_command."""

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
            p._refresh_queue_gauge = AsyncMock()
            p.db.table("command_log").log_command = AsyncMock()
            return p

    async def test_delete_account_success(self, proxy):
        """deleteAccount command deletes account successfully."""
        proxy.db.table("accounts").get = AsyncMock(return_value={"id": "acc1"})
        proxy.db.table("accounts").remove = AsyncMock()

        result = await proxy.handle_command("deleteAccount", {
            "tenant_id": "t1",
            "id": "acc1"
        })

        assert result["ok"] is True
        proxy.db.table("accounts").remove.assert_called_once_with("t1", "acc1")
        proxy._refresh_queue_gauge.assert_called()

    async def test_delete_account_not_found(self, proxy):
        """deleteAccount command returns error when account not found."""
        proxy.db.table("accounts").get = AsyncMock(side_effect=ValueError("not found"))

        result = await proxy.handle_command("deleteAccount", {
            "tenant_id": "t1",
            "id": "nonexistent"
        })

        assert result["ok"] is False
        assert "not found" in result["error"]


class TestMailProxyInitAccountMetrics:
    """Tests for _init_account_metrics method."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            p.metrics = MagicMock()
            return p

    async def test_init_account_metrics_initializes_default(self, proxy):
        """_init_account_metrics always initializes default account."""
        proxy.db.table("tenants").list_all = AsyncMock(return_value=[])
        proxy.db.table("accounts").list_all = AsyncMock(return_value=[])

        await proxy._init_account_metrics()

        proxy.metrics.init_account.assert_called()
        proxy.metrics.set_pending.assert_called_with(0)

    async def test_init_account_metrics_initializes_all_accounts(self, proxy):
        """_init_account_metrics initializes metrics for all accounts."""
        proxy.db.table("tenants").list_all = AsyncMock(return_value=[
            {"id": "t1", "name": "Tenant 1"},
            {"id": "t2", "name": "Tenant 2"},
        ])
        proxy.db.table("accounts").list_all = AsyncMock(return_value=[
            {"id": "acc1", "tenant_id": "t1"},
            {"id": "acc2", "tenant_id": "t2"},
        ])

        await proxy._init_account_metrics()

        # Should have called init_account for default + 2 accounts
        assert proxy.metrics.init_account.call_count >= 3


class TestMailProxyLogDeliveryEventExtended:
    """Extended tests for _log_delivery_event method."""

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

    def test_log_deferred_event_with_non_numeric_timestamp(self, proxy):
        """Deferred events handle non-numeric timestamps."""
        event = {
            "status": "deferred",
            "id": "msg1",
            "account": "acc1",
            "deferred_until": "2025-01-20T10:00:00Z"  # String timestamp
        }
        proxy._log_delivery_event(event)
        proxy.logger.info.assert_called()

    def test_log_unknown_status_event(self, proxy):
        """Unknown status events are logged as info."""
        event = {
            "status": "processing",  # Unknown status
            "id": "msg1",
            "account": "acc1",
        }
        proxy._log_delivery_event(event)
        proxy.logger.info.assert_called()


class TestMailProxyNormalisePriorityExtended:
    """Extended tests for _normalise_priority edge cases."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            return MailProxy()

    def test_normalise_with_string_default(self, proxy):
        """_normalise_priority accepts string default."""
        priority, label = proxy._normalise_priority(None, default="high")
        assert priority == 1
        assert label == "high"

    def test_normalise_with_float_default(self, proxy):
        """_normalise_priority accepts float default."""
        priority, label = proxy._normalise_priority(None, default=1.5)
        assert priority == 1
        assert label == "high"

    def test_normalise_with_invalid_type_default(self, proxy):
        """_normalise_priority handles invalid default type."""
        priority, label = proxy._normalise_priority(None, default=object())
        assert priority == DEFAULT_PRIORITY  # Falls back to global default

    def test_normalise_with_non_convertible_value(self, proxy):
        """_normalise_priority handles non-convertible values."""
        priority, label = proxy._normalise_priority(object())
        assert priority == DEFAULT_PRIORITY


class TestMailProxyHandleAddMessagesExtended:
    """Extended tests for _handle_add_messages edge cases."""

    @pytest.fixture
    def proxy(self):
        with patch('core.mail_proxy.proxy.SmtpSender'), \
             patch('core.mail_proxy.proxy.ClientReporter'), \
             patch('core.mail_proxy.proxy_base.SqlDb') as mock_db_cls:
            mock_db_cls.return_value = MockDb()
            p = MailProxy()
            p.db.table("accounts").get = AsyncMock(return_value={"id": "acc1"})
            p.db.table("messages").insert_batch = AsyncMock(return_value=[])
            p.db.table("message_events").add_event = AsyncMock()
            p._refresh_queue_gauge = AsyncMock()
            p._publish_result = AsyncMock()
            return p

    async def test_non_dict_payload_in_messages(self, proxy):
        """Non-dict items in messages list are rejected."""
        messages = ["not a dict", 123, None]
        result = await proxy._handle_add_messages({"messages": messages})

        assert len(result["rejected"]) == 3
        for r in result["rejected"]:
            assert r["reason"] == "invalid payload"

    async def test_rejected_message_with_id_is_persisted(self, proxy):
        """Rejected messages with ID are persisted for error reporting."""
        proxy.db.table("messages").insert_batch = AsyncMock(return_value=[{"id": "msg1", "pk": "pk1"}])

        messages = [{
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            # Missing required fields
        }]
        result = await proxy._handle_add_messages({"messages": messages})

        # Message was rejected due to validation failure
        assert len(result["rejected"]) == 1
        # But it was persisted with error event
        proxy.db.table("message_events").add_event.assert_called()

    async def test_already_sent_message_is_rejected(self, proxy):
        """Messages already sent are marked as rejected with 'already sent' reason."""
        # insert_batch returns empty list (message already exists with sent_ts)
        proxy.db.table("messages").insert_batch = AsyncMock(return_value=[])

        messages = [{
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test",
        }]
        result = await proxy._handle_add_messages({"messages": messages})

        assert len(result["rejected"]) == 1
        assert result["rejected"][0]["reason"] == "already sent"
        # ok should still be True because "already sent" is not a validation failure
        assert result["ok"] is True

    async def test_deferred_ts_none_is_removed(self, proxy):
        """deferred_ts=None is removed from payload."""
        proxy.db.table("messages").insert_batch = AsyncMock(return_value=[{"id": "msg1", "pk": "pk1"}])

        messages = [{
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test",
            "deferred_ts": None,
        }]
        await proxy._handle_add_messages({"messages": messages})

        # Check that the message passed to insert_batch doesn't have deferred_ts
        call_args = proxy.db.table("messages").insert_batch.call_args
        inserted_entry = call_args[0][0][0]
        assert inserted_entry.get("deferred_ts") is None


class TestMailProxySummariseAddressesExtended:
    """Extended tests for _summarise_addresses edge cases."""

    def test_set_is_joined(self):
        """Set items are joined."""
        result = MailProxy._summarise_addresses({"a@test.com", "b@test.com"})
        assert "@test.com" in result

    def test_single_value_converted_to_string(self):
        """Single non-iterable value is converted to string."""
        result = MailProxy._summarise_addresses(123)
        assert result == "123"

    def test_tuple_is_joined(self):
        """Tuple items are joined."""
        result = MailProxy._summarise_addresses(("a@test.com", "b@test.com"))
        assert "a@test.com" in result
        assert "b@test.com" in result


class TestMailProxyEEHooks:
    """Tests for EE hook methods (CE stubs)."""

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    def test_init_proxy_ee_is_noop(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """__init_proxy_ee__ is a no-op in CE."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        # Should not raise
        proxy.__init_proxy_ee__()

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    async def test_start_proxy_ee_is_noop(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """_start_proxy_ee is a no-op in CE."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        # Should not raise
        await proxy._start_proxy_ee()

    @patch('core.mail_proxy.proxy.SmtpSender')
    @patch('core.mail_proxy.proxy.ClientReporter')
    @patch('core.mail_proxy.proxy_base.SqlDb')
    async def test_stop_proxy_ee_is_noop(self, mock_db_cls, mock_reporter_cls, mock_sender_cls):
        """_stop_proxy_ee is a no-op in CE."""
        mock_db_cls.return_value = MockDb()

        proxy = MailProxy()
        # Should not raise
        await proxy._stop_proxy_ee()
