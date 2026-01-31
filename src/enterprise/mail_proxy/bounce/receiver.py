# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Bounce receiver - IMAP polling for bounce detection.

This module provides BounceReceiver, the component responsible for:
- Polling an IMAP mailbox for bounce notifications
- Parsing bounce messages to extract delivery failure information
- Correlating bounces with sent messages via X-Genro-Mail-ID header
- Recording bounce events in the message_events table

BounceReceiver is instantiated by MailProxy_EE and accessed via proxy.bounce_receiver.

Example:
    # BounceReceiver is created by MailProxy_EE when configured
    from enterprise.mail_proxy.bounce import BounceConfig

    proxy.configure_bounce_receiver(BounceConfig(
        host="imap.example.com",
        port=993,
        user="bounces@example.com",
        password="secret",
    ))
    await proxy.start()  # Starts bounce_receiver automatically
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .parser import BounceParser

if TYPE_CHECKING:
    from ..proxy_ee import MailProxy_EE


@dataclass
class BounceConfig:
    """Configuration for bounce mailbox polling."""

    host: str
    port: int
    user: str
    password: str
    use_ssl: bool = True
    folder: str = "INBOX"
    poll_interval: int = 60  # seconds


class BounceReceiver:
    """Background task that polls IMAP for bounce messages.

    Monitors a dedicated IMAP mailbox for bounce notifications and correlates
    them with sent messages using the X-Genro-Mail-ID header.

    Attributes:
        proxy: Parent MailProxy instance for accessing db, logger, etc.
        config: BounceConfig with IMAP credentials and polling settings.
    """

    def __init__(self, proxy: MailProxy_EE, config: BounceConfig) -> None:
        """Initialize BounceReceiver.

        Args:
            proxy: Parent MailProxy instance.
            config: BounceConfig with IMAP credentials and polling settings.
        """
        self.proxy = proxy
        self.config = config
        self._parser = BounceParser()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_uid: int = 0
        self._uidvalidity: int | None = None

    # ----------------------------------------------------------------- properties
    @property
    def db(self):
        """Database access via proxy."""
        return self.proxy.db

    @property
    def logger(self):
        """Logger via proxy."""
        return self.proxy.logger

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Start the bounce receiver background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="bounce-receiver-loop")

        self.logger.info(
            "BounceReceiver started: host=%s port=%d user=%s",
            self.config.host,
            self.config.port,
            self.config.user,
        )

    async def stop(self) -> None:
        """Stop the bounce receiver."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        self.logger.info("BounceReceiver stopped")

    # ----------------------------------------------------------------- polling
    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._process_bounces()
            except Exception as e:
                self.logger.error("Bounce processing error: %s", e)

            await asyncio.sleep(self.config.poll_interval)

    async def _process_bounces(self) -> None:
        """Poll IMAP and process any bounce messages."""
        from ..imap import IMAPClient

        client = IMAPClient(logger=self.logger)

        try:
            await client.connect(
                host=self.config.host,
                port=self.config.port,
                user=self.config.user,
                password=self.config.password,
                use_ssl=self.config.use_ssl,
            )

            uidvalidity = await client.select_folder(self.config.folder)

            # Reset last_uid if UIDVALIDITY changed (mailbox was recreated)
            if self._uidvalidity is not None and uidvalidity != self._uidvalidity:
                self.logger.warning(
                    "UIDVALIDITY changed from %d to %d, resetting sync state",
                    self._uidvalidity,
                    uidvalidity,
                )
                self._last_uid = 0

            self._uidvalidity = uidvalidity

            # Fetch new messages
            messages = await client.fetch_since_uid(self._last_uid)

            if not messages:
                return

            self.logger.debug("Processing %d potential bounce messages", len(messages))

            processed = 0
            for msg in messages:
                bounce_info = self._parser.parse(msg.raw)

                if bounce_info.original_message_id:
                    # Found a bounce with our tracking header
                    # original_message_id contains the pk (UUID) from the Message-ID header
                    await self.db.table("message_events").add_event(
                        bounce_info.original_message_id,
                        "bounce",
                        int(time.time()),
                        description=bounce_info.bounce_reason,
                        metadata={
                            "bounce_type": bounce_info.bounce_type or "hard",
                            "bounce_code": bounce_info.bounce_code,
                        },
                    )
                    processed += 1

                    self.logger.info(
                        "Bounce detected: pk=%s type=%s code=%s",
                        bounce_info.original_message_id,
                        bounce_info.bounce_type,
                        bounce_info.bounce_code,
                    )

                # Update last_uid regardless of whether it was a valid bounce
                if msg.uid > self._last_uid:
                    self._last_uid = msg.uid

            if processed > 0:
                self.logger.info("Processed %d bounces", processed)

        finally:
            await client.close()


__all__ = ["BounceConfig", "BounceReceiver"]
