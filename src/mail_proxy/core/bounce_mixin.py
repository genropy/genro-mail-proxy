# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Bounce receiver mixin for MailProxy."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..bounce import BounceConfig, BounceReceiver


class BounceReceiverMixin:
    """Mixin that adds bounce detection capabilities to MailProxy."""

    _bounce_receiver: "BounceReceiver | None"
    _bounce_config: "BounceConfig | None"

    def __init_bounce_receiver__(self) -> None:
        """Initialize bounce receiver state. Called from MailProxy.__init__."""
        self._bounce_receiver = None
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

    async def _start_bounce_receiver(self) -> None:
        """Start the bounce receiver if configured. Called from MailProxy.start()."""
        if self._bounce_config is None:
            return

        from ..bounce import BounceReceiver

        # Access db and logger from the main class (they are mixed together)
        db = getattr(self, "db", None)
        logger = getattr(self, "logger", None)

        if db is None:
            raise RuntimeError("BounceReceiverMixin requires db attribute")

        self._bounce_receiver = BounceReceiver(
            db=db,
            config=self._bounce_config,
            logger=logger,
        )
        await self._bounce_receiver.start()

    async def _stop_bounce_receiver(self) -> None:
        """Stop the bounce receiver if running. Called from MailProxy.stop()."""
        if self._bounce_receiver is not None:
            await self._bounce_receiver.stop()
            self._bounce_receiver = None

    @property
    def bounce_receiver_running(self) -> bool:
        """Return True if bounce receiver is currently running."""
        return self._bounce_receiver is not None and self._bounce_receiver._running

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


__all__ = ["BounceReceiverMixin"]
