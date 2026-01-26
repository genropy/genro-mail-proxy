# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Bounce receiver loop for polling IMAP and processing bounces."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .parser import BounceParser

if TYPE_CHECKING:
    from logging import Logger

    from ..mailproxy_db import MailProxyDb


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
    """Background task that polls IMAP for bounce messages."""

    def __init__(self, db: MailProxyDb, config: BounceConfig, logger: Logger | None = None):
        self._db = db
        self._config = config
        self._logger = logger
        self._parser = BounceParser()
        self._running = False
        self._task: asyncio.Task[None] | None = None
        self._last_uid: int = 0
        self._uidvalidity: int | None = None

    async def start(self) -> None:
        """Start the bounce receiver background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

        if self._logger:
            self._logger.info("BounceReceiver started")

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

        if self._logger:
            self._logger.info("BounceReceiver stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._process_bounces()
            except Exception as e:
                if self._logger:
                    self._logger.error("Bounce processing error: %s", e)

            await asyncio.sleep(self._config.poll_interval)

    async def _process_bounces(self) -> None:
        """Poll IMAP and process any bounce messages."""
        from ..imap import IMAPClient

        client = IMAPClient(logger=self._logger)

        try:
            await client.connect(
                host=self._config.host,
                port=self._config.port,
                user=self._config.user,
                password=self._config.password,
                use_ssl=self._config.use_ssl,
            )

            uidvalidity = await client.select_folder(self._config.folder)

            # Reset last_uid if UIDVALIDITY changed (mailbox was recreated)
            if self._uidvalidity is not None and uidvalidity != self._uidvalidity:
                if self._logger:
                    self._logger.warning(
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

            if self._logger:
                self._logger.debug("Processing %d potential bounce messages", len(messages))

            processed = 0
            for msg in messages:
                bounce_info = self._parser.parse(msg.raw)

                if bounce_info.original_message_id:
                    # Found a bounce with our tracking header
                    await self._db.mark_bounced(
                        msg_id=bounce_info.original_message_id,
                        bounce_type=bounce_info.bounce_type or "hard",
                        bounce_code=bounce_info.bounce_code,
                        bounce_reason=bounce_info.bounce_reason,
                    )
                    processed += 1

                    if self._logger:
                        self._logger.info(
                            "Bounce detected: msg_id=%s type=%s code=%s",
                            bounce_info.original_message_id,
                            bounce_info.bounce_type,
                            bounce_info.bounce_code,
                        )

                # Update last_uid regardless of whether it was a valid bounce
                if msg.uid > self._last_uid:
                    self._last_uid = msg.uid

            if self._logger and processed > 0:
                self._logger.info("Processed %d bounces", processed)

        finally:
            await client.close()


__all__ = ["BounceConfig", "BounceReceiver"]
