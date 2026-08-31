# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Unit tests for BounceReceiverMixin."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.mail_proxy.core import MailProxy
from src.mail_proxy.core.bounce_mixin import BounceReceiverMixin  # noqa: F401


@pytest_asyncio.fixture
async def proxy(tmp_path):
    """Create a MailProxy for testing."""
    proxy = MailProxy(
        db_path=str(tmp_path / "test.db"),
        test_mode=True,
    )
    await proxy.start()
    yield proxy
    await proxy.stop()


class TestInitBounceReceiver:
    """Test __init_bounce_receiver__ initialization."""

    @pytest.mark.asyncio
    async def test_initial_state_is_none(self, proxy: MailProxy):
        """Bounce receiver should be None initially."""
        assert proxy._bounce_receiver is None
        assert proxy._bounce_config is None


class TestConfigureBounceReceiver:
    """Test configure_bounce_receiver method."""

    @pytest.mark.asyncio
    async def test_stores_config(self, proxy: MailProxy):
        """Configuration should be stored."""
        from mail_proxy.bounce import BounceConfig

        config = BounceConfig(
            host="imap.example.com",
            port=993,
            user="bounce@example.com",
            password="secret",
        )
        proxy.configure_bounce_receiver(config)

        assert proxy._bounce_config is config
        assert proxy._bounce_config.host == "imap.example.com"
        assert proxy._bounce_config.port == 993

    @pytest.mark.asyncio
    async def test_can_reconfigure(self, proxy: MailProxy):
        """Should allow reconfiguration."""
        from mail_proxy.bounce import BounceConfig

        config1 = BounceConfig(
            host="imap1.example.com",
            port=993,
            user="bounce1@example.com",
            password="secret1",
        )
        config2 = BounceConfig(
            host="imap2.example.com",
            port=143,
            user="bounce2@example.com",
            password="secret2",
        )

        proxy.configure_bounce_receiver(config1)
        assert proxy._bounce_config.host == "imap1.example.com"

        proxy.configure_bounce_receiver(config2)
        assert proxy._bounce_config.host == "imap2.example.com"


class TestStartBounceReceiver:
    """Test _start_bounce_receiver method."""

    @pytest.mark.asyncio
    async def test_does_nothing_without_config(self, proxy: MailProxy):
        """Should return early if no config."""
        proxy._bounce_config = None
        await proxy._start_bounce_receiver()
        assert proxy._bounce_receiver is None

    @pytest.mark.asyncio
    async def test_creates_and_starts_receiver(self, proxy: MailProxy):
        """Should create and start BounceReceiver with config."""
        from mail_proxy.bounce import BounceConfig
        # Import using src. prefix to match how pytest loads modules
        from src.mail_proxy.bounce.receiver import BounceReceiver

        config = BounceConfig(
            host="imap.example.com",
            port=993,
            user="bounce@example.com",
            password="secret",
        )
        proxy.configure_bounce_receiver(config)

        # Mock BounceReceiver.start to avoid actual IMAP connection
        with patch.object(BounceReceiver, "start", new_callable=AsyncMock) as mock_start:
            await proxy._start_bounce_receiver()

            # Verify receiver was created and started
            assert proxy._bounce_receiver is not None
            assert type(proxy._bounce_receiver).__name__ == "BounceReceiver"
            mock_start.assert_called_once()

    @pytest.mark.asyncio
    async def test_raises_if_no_db(self, tmp_path):
        """Should raise if db attribute is missing."""
        from mail_proxy.bounce import BounceConfig

        # Create a minimal object with mixin but no db
        class MinimalMixin(BounceReceiverMixin):
            def __init__(self):
                self.__init_bounce_receiver__()
                self.logger = None

        mixin = MinimalMixin()
        config = BounceConfig(
            host="imap.example.com",
            port=993,
            user="bounce@example.com",
            password="secret",
        )
        mixin.configure_bounce_receiver(config)

        with pytest.raises(RuntimeError) as exc_info:
            await mixin._start_bounce_receiver()

        assert "requires db attribute" in str(exc_info.value)


class TestStopBounceReceiver:
    """Test _stop_bounce_receiver method."""

    @pytest.mark.asyncio
    async def test_does_nothing_if_not_running(self, proxy: MailProxy):
        """Should do nothing if receiver is None."""
        proxy._bounce_receiver = None
        await proxy._stop_bounce_receiver()
        assert proxy._bounce_receiver is None

    @pytest.mark.asyncio
    async def test_stops_and_clears_receiver(self, proxy: MailProxy):
        """Should stop receiver and set to None."""
        mock_receiver = MagicMock()
        mock_receiver.stop = AsyncMock()
        proxy._bounce_receiver = mock_receiver

        await proxy._stop_bounce_receiver()

        mock_receiver.stop.assert_called_once()
        assert proxy._bounce_receiver is None


class TestBounceReceiverRunningProperty:
    """Test bounce_receiver_running property."""

    @pytest.mark.asyncio
    async def test_false_when_receiver_is_none(self, proxy: MailProxy):
        """Should return False if receiver is None."""
        proxy._bounce_receiver = None
        assert proxy.bounce_receiver_running is False

    @pytest.mark.asyncio
    async def test_false_when_receiver_not_running(self, proxy: MailProxy):
        """Should return False if receiver exists but not running."""
        mock_receiver = MagicMock()
        mock_receiver._running = False
        proxy._bounce_receiver = mock_receiver
        assert proxy.bounce_receiver_running is False
        # Cleanup to prevent teardown error
        proxy._bounce_receiver = None

    @pytest.mark.asyncio
    async def test_true_when_receiver_running(self, proxy: MailProxy):
        """Should return True if receiver is running."""
        mock_receiver = MagicMock()
        mock_receiver._running = True
        proxy._bounce_receiver = mock_receiver
        assert proxy.bounce_receiver_running is True
        # Cleanup to prevent teardown error
        proxy._bounce_receiver = None


class TestHandleBounceCommand:
    """Test handle_bounce_command method."""

    @pytest.mark.asyncio
    async def test_get_bounce_status_not_configured(self, proxy: MailProxy):
        """getBounceStatus returns not configured state."""
        proxy._bounce_config = None
        proxy._bounce_receiver = None

        result = await proxy.handle_bounce_command("getBounceStatus")

        assert result["ok"] is True
        assert result["configured"] is False
        assert result["running"] is False

    @pytest.mark.asyncio
    async def test_get_bounce_status_configured_not_running(self, proxy: MailProxy):
        """getBounceStatus returns configured but not running."""
        from mail_proxy.bounce import BounceConfig

        config = BounceConfig(
            host="imap.example.com",
            port=993,
            user="bounce@example.com",
            password="secret",
        )
        proxy.configure_bounce_receiver(config)
        proxy._bounce_receiver = None

        result = await proxy.handle_bounce_command("getBounceStatus")

        assert result["ok"] is True
        assert result["configured"] is True
        assert result["running"] is False

    @pytest.mark.asyncio
    async def test_get_bounce_status_running(self, proxy: MailProxy):
        """getBounceStatus returns running state."""
        from mail_proxy.bounce import BounceConfig

        config = BounceConfig(
            host="imap.example.com",
            port=993,
            user="bounce@example.com",
            password="secret",
        )
        proxy.configure_bounce_receiver(config)

        mock_receiver = MagicMock()
        mock_receiver._running = True
        proxy._bounce_receiver = mock_receiver

        result = await proxy.handle_bounce_command("getBounceStatus")

        assert result["ok"] is True
        assert result["configured"] is True
        assert result["running"] is True
        # Cleanup to prevent teardown error
        proxy._bounce_receiver = None

    @pytest.mark.asyncio
    async def test_unknown_command_returns_error(self, proxy: MailProxy):
        """Unknown command should return error."""
        result = await proxy.handle_bounce_command("unknownCommand")

        assert result["ok"] is False
        assert "unknown bounce command" in result["error"]

    @pytest.mark.asyncio
    async def test_handles_none_payload(self, proxy: MailProxy):
        """Should handle None payload."""
        result = await proxy.handle_bounce_command("getBounceStatus", None)
        assert result["ok"] is True

    @pytest.mark.asyncio
    async def test_handles_empty_payload(self, proxy: MailProxy):
        """Should handle empty payload dict."""
        result = await proxy.handle_bounce_command("getBounceStatus", {})
        assert result["ok"] is True
