# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition extensions for MailProxy.

This module adds bounce detection capabilities to the base MailProxy class.
Bounce detection monitors a dedicated IMAP mailbox for bounce notifications
and correlates them with sent messages.

Usage:
    class MailProxy(MailProxy_EE, MailProxyBase):
        pass
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bounce import BounceConfig, BounceReceiver


class MailProxy_EE:
    """Enterprise Edition: Bounce detection capabilities.

    Adds methods for:
    - Configuring bounce detection via IMAP
    - Starting/stopping bounce receiver
    - Handling bounce-related commands
    """

    bounce_receiver: "BounceReceiver | None"
    _bounce_config: "BounceConfig | None"

    def __init_proxy_ee__(self) -> None:
        """Initialize EE proxy state. Called from MailProxy.__init__."""
        self.bounce_receiver = None
        self._bounce_config = None

    def configure_bounce_receiver(self, config: "BounceConfig") -> None:
        """Configure bounce detection.

        Call this before start() to enable bounce detection. The bounce receiver
        will poll the configured IMAP mailbox for bounce messages and correlate
        them with sent messages using the X-Genro-Mail-ID header.

        Args:
            config: BounceConfig with IMAP credentials and polling settings.
        """
        self._bounce_config = config

    async def _start_proxy_ee(self) -> None:
        """Start EE components. Called from MailProxy.start()."""
        if self._bounce_config is None:
            return

        from .bounce import BounceReceiver

        self.bounce_receiver = BounceReceiver(self, self._bounce_config)  # type: ignore[arg-type]
        await self.bounce_receiver.start()

    async def _stop_proxy_ee(self) -> None:
        """Stop EE components. Called from MailProxy.stop()."""
        if self.bounce_receiver is not None:
            await self.bounce_receiver.stop()
            self.bounce_receiver = None

    @property
    def bounce_receiver_running(self) -> bool:
        """Return True if bounce receiver is currently running."""
        return self.bounce_receiver is not None and self.bounce_receiver._running

    async def handle_bounce_command(self, cmd: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
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
