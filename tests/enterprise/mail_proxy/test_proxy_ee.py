# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for MailProxy_EE mixin."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from enterprise.mail_proxy.bounce.receiver import BounceConfig
from enterprise.mail_proxy.proxy_ee import MailProxy_EE


class MockMailProxy(MailProxy_EE):
    """Mock MailProxy that uses EE mixin."""

    def __init__(self):
        # Simulate what MailProxy.__init__ does
        self.__init_proxy_ee__()


class TestMailProxyEEInit:
    """Tests for __init_proxy_ee__."""

    def test_init_proxy_ee(self):
        """__init_proxy_ee__ initializes EE state."""
        proxy = MockMailProxy()

        assert proxy.bounce_receiver is None
        assert proxy._bounce_config is None


class TestMailProxyEEConfigureBounceReceiver:
    """Tests for configure_bounce_receiver."""

    def test_configure_bounce_receiver(self):
        """configure_bounce_receiver stores config."""
        proxy = MockMailProxy()
        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="bounces@test.com",
            password="secret",
        )

        proxy.configure_bounce_receiver(config)

        assert proxy._bounce_config is config


class TestMailProxyEELifecycle:
    """Tests for _start_proxy_ee and _stop_proxy_ee."""

    async def test_start_proxy_ee_without_config(self):
        """_start_proxy_ee does nothing without config."""
        proxy = MockMailProxy()

        await proxy._start_proxy_ee()

        assert proxy.bounce_receiver is None

    async def test_start_proxy_ee_with_config(self):
        """_start_proxy_ee creates and starts BounceReceiver."""
        proxy = MockMailProxy()
        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="bounces@test.com",
            password="secret",
        )
        proxy.configure_bounce_receiver(config)

        mock_receiver = MagicMock()
        mock_receiver.start = AsyncMock()

        # BounceReceiver is imported inside _start_proxy_ee
        mock_bounce_module = MagicMock()
        mock_bounce_module.BounceReceiver = MagicMock(return_value=mock_receiver)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.bounce": mock_bounce_module}):
            await proxy._start_proxy_ee()

        assert proxy.bounce_receiver is mock_receiver
        mock_receiver.start.assert_called_once()

    async def test_stop_proxy_ee_without_receiver(self):
        """_stop_proxy_ee does nothing without receiver."""
        proxy = MockMailProxy()

        await proxy._stop_proxy_ee()

        assert proxy.bounce_receiver is None

    async def test_stop_proxy_ee_with_receiver(self):
        """_stop_proxy_ee stops and clears receiver."""
        proxy = MockMailProxy()

        mock_receiver = MagicMock()
        mock_receiver.stop = AsyncMock()
        proxy.bounce_receiver = mock_receiver

        await proxy._stop_proxy_ee()

        mock_receiver.stop.assert_called_once()
        assert proxy.bounce_receiver is None


class TestMailProxyEEBounceReceiverRunning:
    """Tests for bounce_receiver_running property."""

    def test_bounce_receiver_running_no_receiver(self):
        """bounce_receiver_running is False when no receiver."""
        proxy = MockMailProxy()

        assert proxy.bounce_receiver_running is False

    def test_bounce_receiver_running_not_running(self):
        """bounce_receiver_running is False when receiver not running."""
        proxy = MockMailProxy()
        mock_receiver = MagicMock()
        mock_receiver._running = False
        proxy.bounce_receiver = mock_receiver

        assert proxy.bounce_receiver_running is False

    def test_bounce_receiver_running_is_running(self):
        """bounce_receiver_running is True when receiver is running."""
        proxy = MockMailProxy()
        mock_receiver = MagicMock()
        mock_receiver._running = True
        proxy.bounce_receiver = mock_receiver

        assert proxy.bounce_receiver_running is True


class TestMailProxyEEHandleBounceCommand:
    """Tests for handle_bounce_command."""

    async def test_get_bounce_status_not_configured(self):
        """getBounceStatus when not configured."""
        proxy = MockMailProxy()

        result = await proxy.handle_bounce_command("getBounceStatus")

        assert result == {
            "ok": True,
            "configured": False,
            "running": False,
        }

    async def test_get_bounce_status_configured_not_running(self):
        """getBounceStatus when configured but not running."""
        proxy = MockMailProxy()
        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="bounces@test.com",
            password="secret",
        )
        proxy.configure_bounce_receiver(config)

        result = await proxy.handle_bounce_command("getBounceStatus")

        assert result == {
            "ok": True,
            "configured": True,
            "running": False,
        }

    async def test_get_bounce_status_running(self):
        """getBounceStatus when running."""
        proxy = MockMailProxy()
        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="bounces@test.com",
            password="secret",
        )
        proxy.configure_bounce_receiver(config)

        mock_receiver = MagicMock()
        mock_receiver._running = True
        proxy.bounce_receiver = mock_receiver

        result = await proxy.handle_bounce_command("getBounceStatus")

        assert result == {
            "ok": True,
            "configured": True,
            "running": True,
        }

    async def test_unknown_command(self):
        """Unknown command returns error."""
        proxy = MockMailProxy()

        result = await proxy.handle_bounce_command("unknownCommand")

        assert result == {
            "ok": False,
            "error": "unknown bounce command",
        }

    async def test_get_bounce_status_with_payload(self):
        """getBounceStatus ignores payload."""
        proxy = MockMailProxy()

        result = await proxy.handle_bounce_command("getBounceStatus", {"extra": "data"})

        assert result["ok"] is True
