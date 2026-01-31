# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""PEC receipt receiver loop for polling IMAP and processing receipts.

This module monitors PEC accounts for incoming receipts (ricevute) and:
1. Parses receipt type (accettazione, consegna, mancata_consegna, etc.)
2. Creates corresponding events in the message_events table
3. Handles timeout for PEC messages that don't receive acceptance within 30 minutes

The receiver runs as a background task and polls all PEC accounts periodically.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from .parser import PecReceiptParser

if TYPE_CHECKING:
    from logging import Logger

    from core.mail_proxy.mailproxy_db import MailProxyDb


# Timeout in seconds for PEC acceptance (30 minutes)
PEC_ACCEPTANCE_TIMEOUT = 30 * 60


class PecReceiver:
    """Background task that polls PEC accounts for receipts."""

    def __init__(
        self,
        db: MailProxyDb,
        logger: Logger | None = None,
        poll_interval: int = 60,
        acceptance_timeout: int = PEC_ACCEPTANCE_TIMEOUT,
    ):
        self._db = db
        self._logger = logger
        self._parser = PecReceiptParser()
        self._poll_interval = poll_interval
        self._acceptance_timeout = acceptance_timeout
        self._running = False
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Start the PEC receiver background task."""
        if self._running:
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())

        if self._logger:
            self._logger.info("PecReceiver started")

    async def stop(self) -> None:
        """Stop the PEC receiver."""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._logger:
            self._logger.info("PecReceiver stopped")

    async def _poll_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            try:
                await self._process_all_pec_accounts()
                await self._check_pec_timeouts()
            except Exception as e:
                if self._logger:
                    self._logger.error("PEC processing error: %s", e)

            await asyncio.sleep(self._poll_interval)

    async def _process_all_pec_accounts(self) -> None:
        """Process receipts for all PEC accounts."""
        pec_accounts = await self._db.table("accounts").list_pec_accounts()

        if not pec_accounts:
            return

        for account in pec_accounts:
            try:
                await self._process_account(account)
            except Exception as e:
                if self._logger:
                    self._logger.error(
                        "Error processing PEC account %s: %s",
                        account.get("id"),
                        e,
                    )

    async def _process_account(self, account: dict) -> None:
        """Process receipts for a single PEC account."""
        from ..imap import IMAPClient

        tenant_id = account["tenant_id"]
        account_id = account["id"]
        imap_host = account.get("imap_host")
        imap_port = account.get("imap_port", 993)
        imap_user = account.get("imap_user")
        imap_password = account.get("imap_password")
        imap_folder = account.get("imap_folder", "INBOX")
        last_uid = account.get("imap_last_uid") or 0
        stored_uidvalidity = account.get("imap_uidvalidity")

        if not all([imap_host, imap_user, imap_password]):
            if self._logger:
                self._logger.warning(
                    "PEC account %s missing IMAP configuration",
                    account_id,
                )
            return

        client = IMAPClient(logger=self._logger)

        try:
            await client.connect(
                host=imap_host,
                port=imap_port,
                user=imap_user,
                password=imap_password,
                use_ssl=True,
            )

            uidvalidity = await client.select_folder(imap_folder)

            # Reset last_uid if UIDVALIDITY changed (mailbox was recreated)
            if stored_uidvalidity is not None and uidvalidity != stored_uidvalidity:
                if self._logger:
                    self._logger.warning(
                        "PEC account %s: UIDVALIDITY changed from %d to %d, resetting sync state",
                        account_id,
                        stored_uidvalidity,
                        uidvalidity,
                    )
                last_uid = 0

            # Fetch new messages
            messages = await client.fetch_since_uid(last_uid)

            if not messages:
                # Update uidvalidity even if no messages
                if uidvalidity != stored_uidvalidity:
                    await self._db.table("accounts").update_imap_sync_state(
                        tenant_id,
                        account_id,
                        last_uid=last_uid,
                        uidvalidity=uidvalidity,
                    )
                return

            if self._logger:
                self._logger.debug(
                    "PEC account %s: processing %d messages",
                    account_id,
                    len(messages),
                )

            processed = 0
            max_uid = last_uid

            for msg in messages:
                receipt_info = self._parser.parse(msg.raw)

                if receipt_info.receipt_type and receipt_info.original_message_id:
                    # Found a valid PEC receipt
                    await self._handle_receipt(receipt_info)
                    processed += 1

                    if self._logger:
                        self._logger.info(
                            "PEC receipt: msg_id=%s type=%s",
                            receipt_info.original_message_id,
                            receipt_info.receipt_type,
                        )

                # Track max UID
                if msg.uid > max_uid:
                    max_uid = msg.uid

            # Update sync state
            await self._db.table("accounts").update_imap_sync_state(
                tenant_id,
                account_id,
                last_uid=max_uid,
                uidvalidity=uidvalidity,
            )

            if self._logger and processed > 0:
                self._logger.info(
                    "PEC account %s: processed %d receipts",
                    account_id,
                    processed,
                )

        finally:
            await client.close()

    async def _handle_receipt(self, receipt_info) -> None:
        """Handle a parsed PEC receipt by creating appropriate event."""
        # original_message_id contains the pk (UUID) from the Message-ID header
        message_pk = receipt_info.original_message_id
        receipt_type = receipt_info.receipt_type

        # Map receipt type to event type
        event_type_map = {
            "accettazione": "pec_acceptance",
            "consegna": "pec_delivery",
            "mancata_consegna": "pec_error",
            "non_accettazione": "pec_error",
            "presa_in_carico": "pec_acceptance",  # Treat as acceptance
        }

        event_type = event_type_map.get(receipt_type, "pec_acceptance")

        # Build event metadata
        metadata = {}
        if receipt_info.timestamp:
            metadata["pec_timestamp"] = receipt_info.timestamp
        if receipt_info.recipient:
            metadata["recipient"] = receipt_info.recipient
        if receipt_info.error_reason:
            metadata["error_reason"] = receipt_info.error_reason

        # Create event
        now_ts = int(time.time())
        await self._db.table("message_events").add_event(
            message_pk=message_pk,
            event_type=event_type,
            event_ts=now_ts,
            description=f"PEC {receipt_type}",
            metadata=metadata if metadata else None,
        )

        # For delivery confirmations, the message can be considered complete
        # For errors, mark the message accordingly
        if receipt_type in ("mancata_consegna", "non_accettazione"):
            # Clear PEC flag on error - it won't get delivered via PEC
            await self._db.table("messages").clear_pec_flag(message_pk)

    async def _check_pec_timeouts(self) -> None:
        """Check for PEC messages that timed out waiting for acceptance."""
        # Find messages with is_pec=1 and smtp_ts > 30 minutes ago
        # that haven't received an acceptance receipt
        now_ts = int(time.time())
        cutoff_ts = now_ts - self._acceptance_timeout

        timed_out_messages = await self._db.table("messages").get_pec_without_acceptance(cutoff_ts)

        for msg in timed_out_messages:
            message_pk = msg["pk"]

            if self._logger:
                self._logger.info(
                    "PEC timeout: message pk=%s sent over 30 min ago without acceptance, declassifying",
                    message_pk,
                )

            # Clear the PEC flag
            await self._db.table("messages").clear_pec_flag(message_pk)

            # Create timeout event
            await self._db.table("message_events").add_event(
                message_pk=message_pk,
                event_type="pec_timeout",
                event_ts=now_ts,
                description="PEC acceptance timeout - message treated as normal email",
            )


__all__ = ["PecReceiver", "PEC_ACCEPTANCE_TIMEOUT"]
