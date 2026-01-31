# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for SmtpSender with mocked dependencies."""

import asyncio
from email.message import EmailMessage
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from core.mail_proxy.smtp.sender import (
    SmtpSender,
    AccountConfigurationError,
    AttachmentTooLargeError,
)


class TestSmtpSenderInit:
    """Tests for SmtpSender initialization."""

    def test_init_creates_components(self):
        """SmtpSender creates pool and rate_limiter."""
        mock_proxy = MagicMock()
        sender = SmtpSender(mock_proxy)

        assert sender.proxy == mock_proxy
        assert sender.pool is not None
        assert sender.rate_limiter is not None

    def test_properties_delegate_to_proxy(self):
        """Properties delegate to proxy."""
        mock_proxy = MagicMock()
        mock_proxy.db = "mock_db"
        mock_proxy.config = "mock_config"
        mock_proxy.logger = "mock_logger"

        sender = SmtpSender(mock_proxy)

        assert sender.db == "mock_db"
        assert sender.config == "mock_config"
        assert sender.logger == "mock_logger"


class TestSmtpSenderLifecycle:
    """Tests for SmtpSender start/stop lifecycle."""

    @pytest.fixture
    def mock_proxy(self):
        """Create a mock proxy with all required attributes."""
        proxy = MagicMock()
        proxy.logger = MagicMock()
        proxy._test_mode = True
        proxy._send_loop_interval = 1.0
        return proxy

    @pytest.fixture
    def sender(self, mock_proxy):
        """Create SmtpSender with mock proxy."""
        return SmtpSender(mock_proxy)

    async def test_start_creates_dispatch_task(self, sender):
        """start() creates dispatch loop task."""
        await sender.start()

        assert sender._task_dispatch is not None
        assert not sender._stop.is_set()

        await sender.stop()

    async def test_stop_sets_stop_event(self, sender):
        """stop() sets stop event and cancels tasks."""
        await sender.start()
        await sender.stop()

        assert sender._stop.is_set()

    async def test_wake_sets_event(self, sender):
        """wake() sets the wake event."""
        sender.wake()
        assert sender._wake_event.is_set()


class TestSmtpSenderAccountResolution:
    """Tests for SMTP account resolution."""

    @pytest.fixture
    def mock_proxy(self):
        """Create mock proxy with db."""
        proxy = MagicMock()
        proxy.db = MagicMock()
        proxy.logger = MagicMock()
        proxy.default_host = None
        proxy.default_port = None
        proxy.default_user = None
        proxy.default_password = None
        proxy.default_use_tls = None
        return proxy

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_resolve_account_from_db(self, sender):
        """Resolves account from database."""
        mock_account = {
            "id": "smtp-1",
            "host": "smtp.example.com",
            "port": 465,
            "user": "sender@example.com",
            "password": "secret",
            "use_tls": True,
        }
        sender.db.table = MagicMock(return_value=MagicMock(
            get=AsyncMock(return_value=mock_account)
        ))

        host, port, user, password, acc = await sender._resolve_account(
            "tenant-1", "smtp-1"
        )

        assert host == "smtp.example.com"
        assert port == 465
        assert user == "sender@example.com"
        assert password == "secret"
        assert acc == mock_account

    async def test_resolve_account_uses_defaults(self, sender, mock_proxy):
        """Uses default account when no account_id provided."""
        mock_proxy.default_host = "default.smtp.com"
        mock_proxy.default_port = 587
        mock_proxy.default_user = "default@example.com"
        mock_proxy.default_password = "default-pass"
        mock_proxy.default_use_tls = True

        host, port, user, password, acc = await sender._resolve_account(
            "tenant-1", None
        )

        assert host == "default.smtp.com"
        assert port == 587
        assert user == "default@example.com"
        assert password == "default-pass"

    async def test_resolve_account_no_defaults_raises(self, sender):
        """Raises when no account and no defaults."""
        with pytest.raises(AccountConfigurationError):
            await sender._resolve_account("tenant-1", None)


class TestSmtpSenderEmailBuilding:
    """Tests for email message construction."""

    @pytest.fixture
    def mock_proxy(self):
        """Create mock proxy."""
        proxy = MagicMock()
        proxy.db = MagicMock()
        proxy.logger = MagicMock()
        proxy.attachments = MagicMock()
        proxy._attachment_cache = None
        proxy._attachment_semaphore = None
        proxy._max_concurrent_attachments = 5
        proxy._attachment_timeout = 30.0
        return proxy

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    async def test_build_email_basic(self, sender):
        """Builds basic email with required fields."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test Subject",
            "body": "Test body content",
        }

        msg, envelope_from = await sender._build_email(data)

        assert msg["From"] == "sender@example.com"
        assert msg["To"] == "recipient@example.com"
        assert msg["Subject"] == "Test Subject"
        assert envelope_from == "sender@example.com"

    async def test_build_email_with_cc_bcc(self, sender):
        """Builds email with CC and BCC."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
            "cc": "cc1@example.com, cc2@example.com",
            "bcc": "bcc@example.com",
        }

        msg, _ = await sender._build_email(data)

        assert "cc1@example.com" in msg["Cc"]
        assert "cc2@example.com" in msg["Cc"]
        assert msg["Bcc"] == "bcc@example.com"

    async def test_build_email_to_list(self, sender):
        """Handles To as list."""
        data = {
            "from": "sender@example.com",
            "to": ["a@example.com", "b@example.com"],
            "subject": "Test",
        }

        msg, _ = await sender._build_email(data)

        assert "a@example.com" in msg["To"]
        assert "b@example.com" in msg["To"]

    async def test_build_email_html_content(self, sender):
        """Builds email with HTML content."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
            "body": "<h1>Hello</h1>",
            "content_type": "html",
        }

        msg, _ = await sender._build_email(data)
        # HTML content should be set with subtype=html

    async def test_build_email_reply_to(self, sender):
        """Builds email with Reply-To header."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
            "reply_to": "replies@example.com",
        }

        msg, _ = await sender._build_email(data)

        assert msg["Reply-To"] == "replies@example.com"

    async def test_build_email_custom_headers(self, sender):
        """Builds email with custom headers."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
            "headers": {
                "X-Custom-Header": "custom-value",
                "X-Priority": "1",
            },
        }

        msg, _ = await sender._build_email(data)

        assert msg["X-Custom-Header"] == "custom-value"
        assert msg["X-Priority"] == "1"

    async def test_build_email_return_path(self, sender):
        """Uses return_path for envelope sender."""
        data = {
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
            "return_path": "bounces@example.com",
        }

        _, envelope_from = await sender._build_email(data)

        assert envelope_from == "bounces@example.com"

    async def test_build_email_adds_tracking_header(self, sender):
        """Adds X-Genro-Mail-ID header for bounce tracking."""
        data = {
            "id": "msg-12345",
            "from": "sender@example.com",
            "to": "recipient@example.com",
            "subject": "Test",
        }

        msg, _ = await sender._build_email(data)

        assert msg["X-Genro-Mail-ID"] == "msg-12345"

    async def test_build_email_missing_from_raises(self, sender):
        """Missing 'from' raises KeyError."""
        data = {
            "to": "recipient@example.com",
            "subject": "Test",
        }

        with pytest.raises(KeyError):
            await sender._build_email(data)

    async def test_build_email_missing_to_raises(self, sender):
        """Missing 'to' raises KeyError."""
        data = {
            "from": "sender@example.com",
            "subject": "Test",
        }

        with pytest.raises(KeyError):
            await sender._build_email(data)


class TestSmtpSenderUtilities:
    """Tests for utility methods."""

    @pytest.fixture
    def mock_proxy(self):
        proxy = MagicMock()
        proxy.logger = MagicMock()
        return proxy

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    def test_utc_now_iso_format(self, sender):
        """_utc_now_iso returns ISO format."""
        result = sender._utc_now_iso()
        assert "T" in result
        assert result.endswith("Z")

    def test_utc_now_epoch_is_integer(self, sender):
        """_utc_now_epoch returns integer timestamp."""
        result = sender._utc_now_epoch()
        assert isinstance(result, int)
        assert result > 0

    def test_summarise_addresses_string(self, sender):
        """Summarizes string addresses."""
        result = sender._summarise_addresses("a@example.com, b@example.com")
        assert "a@example.com" in result
        assert "b@example.com" in result

    def test_summarise_addresses_list(self, sender):
        """Summarizes list addresses."""
        result = sender._summarise_addresses(["a@example.com", "b@example.com"])
        assert "a@example.com" in result
        assert "b@example.com" in result

    def test_summarise_addresses_empty(self, sender):
        """Empty addresses returns dash."""
        assert sender._summarise_addresses(None) == "-"
        assert sender._summarise_addresses("") == "-"
        assert sender._summarise_addresses([]) == "-"

    def test_summarise_addresses_truncates_long(self, sender):
        """Long address lists are truncated."""
        long_list = [f"user{i}@example.com" for i in range(50)]
        result = sender._summarise_addresses(long_list)
        assert result.endswith("...")
        assert len(result) <= 200 + 3  # 200 + "..."


class TestSmtpSenderConcurrency:
    """Tests for concurrency control."""

    @pytest.fixture
    def mock_proxy(self):
        proxy = MagicMock()
        proxy.logger = MagicMock()
        proxy._max_concurrent_per_account = 3
        return proxy

    @pytest.fixture
    def sender(self, mock_proxy):
        return SmtpSender(mock_proxy)

    def test_get_account_semaphore_creates_new(self, sender):
        """Creates new semaphore for new account."""
        sem = sender._get_account_semaphore("account-1")
        assert isinstance(sem, asyncio.Semaphore)
        assert "account-1" in sender._account_semaphores

    def test_get_account_semaphore_reuses_existing(self, sender):
        """Reuses existing semaphore."""
        sem1 = sender._get_account_semaphore("account-1")
        sem2 = sender._get_account_semaphore("account-1")
        assert sem1 is sem2


class TestAccountConfigurationError:
    """Tests for AccountConfigurationError."""

    def test_default_message(self):
        """Default message is set."""
        error = AccountConfigurationError()
        assert "Missing SMTP account configuration" in str(error)
        assert error.code == "missing_account_configuration"

    def test_custom_message(self):
        """Custom message is preserved."""
        error = AccountConfigurationError("Custom error message")
        assert "Custom error message" in str(error)


class TestAttachmentTooLargeError:
    """Tests for AttachmentTooLargeError."""

    def test_error_contains_details(self):
        """Error message contains file details."""
        error = AttachmentTooLargeError("large.pdf", 15.5, 10.0)

        assert error.filename == "large.pdf"
        assert error.size_mb == 15.5
        assert error.max_size_mb == 10.0
        assert "large.pdf" in str(error)
        assert "15.5" in str(error)
        assert "10" in str(error)
