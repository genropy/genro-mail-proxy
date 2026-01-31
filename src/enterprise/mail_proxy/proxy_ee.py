# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""MailProxy_EE: Enterprise Edition mixin for bounce detection.

This mixin adds bounce detection to MailProxy by overriding the CE stub
methods (__init_proxy_ee__, _start_proxy_ee, _stop_proxy_ee).

Class Hierarchy:
    MailProxyBase (CE): config, db, tables, endpoints, api/cli
        └── MailProxy (CE): +SmtpSender, +ClientReporter, +metrics
            └── MailProxy with MailProxy_EE (EE): +BounceReceiver

Bounce Detection:
    Monitors a dedicated IMAP mailbox for bounce notifications. Correlates
    bounces with sent messages using the X-Genro-Mail-ID header.

Configuration:
    proxy.configure_bounce_receiver(BounceConfig(
        imap_host="imap.example.com",
        imap_user="bounce@example.com",
        imap_password="secret",
    ))
    await proxy.start()

Commands (via handle_bounce_command):
    - getBounceStatus: Returns configured and running status
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bounce import BounceConfig, BounceReceiver


class MailProxy_EE:
    """EE mixin: adds BounceReceiver to MailProxy.

    Instance Attributes (added by this mixin):
        bounce_receiver: BounceReceiver instance (or None if not started)
        _bounce_config: BounceConfig instance (or None if not configured)

    Methods:
        configure_bounce_receiver(): Set BounceConfig before start()
        bounce_receiver_running: Property to check if poller is active
        handle_bounce_command(): Handle getBounceStatus command

    Overridden Methods (from MailProxy CE stubs):
        __init_proxy_ee__(): Initialize bounce_receiver, _bounce_config
        _start_proxy_ee(): Start BounceReceiver if configured
        _stop_proxy_ee(): Stop BounceReceiver
    """

    bounce_receiver: BounceReceiver | None
    _bounce_config: BounceConfig | None

    # -------------------------------------------------------------------------
    # Initialization (override CE stub)
    # -------------------------------------------------------------------------

    def __init_proxy_ee__(self) -> None:
        """Initialize EE state. Called from MailProxy.__init__."""
        self.bounce_receiver = None
        self._bounce_config = None

    # -------------------------------------------------------------------------
    # Configuration
    # -------------------------------------------------------------------------

    def configure_bounce_receiver(self, config: BounceConfig) -> None:
        """Configure bounce detection.

        Call this before start() to enable bounce detection. The bounce receiver
        will poll the configured IMAP mailbox for bounce messages and correlate
        them with sent messages using the X-Genro-Mail-ID header.

        Args:
            config: BounceConfig with IMAP credentials and polling settings.
        """
        self._bounce_config = config

    # -------------------------------------------------------------------------
    # Lifecycle (override CE stubs)
    # -------------------------------------------------------------------------

    async def _start_proxy_ee(self) -> None:
        """Start BounceReceiver if configured. Called from MailProxy.start()."""
        if self._bounce_config is None:
            return

        from .bounce import BounceReceiver

        self.bounce_receiver = BounceReceiver(self, self._bounce_config)  # type: ignore[arg-type]
        await self.bounce_receiver.start()

    async def _stop_proxy_ee(self) -> None:
        """Stop BounceReceiver. Called from MailProxy.stop()."""
        if self.bounce_receiver is not None:
            await self.bounce_receiver.stop()
            self.bounce_receiver = None

    # -------------------------------------------------------------------------
    # Status and commands
    # -------------------------------------------------------------------------

    @property
    def bounce_receiver_running(self) -> bool:
        """True if BounceReceiver is active and polling."""
        return self.bounce_receiver is not None and self.bounce_receiver._running

    async def handle_bounce_command(
        self, cmd: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Handle bounce-related commands.

        Supported commands:
        - ``getBounceStatus``: Get bounce receiver status
        - ``configureBounce``: Configure bounce receiver (requires restart)

        Args:
            cmd: Command name.
            payload: Command parameters.

        Returns:
            Command result dict.
        """
        payload = payload or {}

        match cmd:
            case "getBounceStatus":
                return {
                    "ok": True,
                    "configured": self._bounce_config is not None,
                    "running": self.bounce_receiver_running,
                }
            case _:
                return {"ok": False, "error": "unknown bounce command"}


__all__ = ["MailProxy_EE"]
