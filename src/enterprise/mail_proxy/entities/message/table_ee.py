# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition extensions for MessagesTable.

This module adds PEC (Posta Elettronica Certificata) tracking functionality
to the base MessagesTable. PEC messages require tracking of acceptance
and delivery receipts.

PEC workflow:
1. Message sent via PEC account gets is_pec=1
2. IMAP poller checks for acceptance receipt
3. If no acceptance within timeout, message flagged for alert
4. Delivery receipt completes the PEC lifecycle

Usage:
    class MessagesTable(MessagesTable_EE, MessagesTableBase):
        pass
"""

from __future__ import annotations

from typing import Any


class MessagesTable_EE:
    """Enterprise Edition: PEC message tracking.

    Adds methods for:
    - Clearing PEC flag for non-PEC recipients
    - Finding PEC messages missing acceptance receipts
    """

    async def clear_pec_flag(self, pk: str) -> None:
        """Clear the is_pec flag when recipient is not a PEC address.

        Called when sending to a non-PEC recipient via a PEC account.
        The message doesn't need receipt tracking in this case.

        Args:
            pk: Internal primary key of the message (UUID string).
        """
        await self.execute(  # type: ignore[attr-defined]
            """
            UPDATE messages
            SET is_pec = 0, updated_at = CURRENT_TIMESTAMP
            WHERE pk = :pk
            """,
            {"pk": pk},
        )

    async def get_pec_without_acceptance(self, cutoff_ts: int) -> list[dict[str, Any]]:
        """Get PEC messages sent before cutoff_ts without acceptance receipt.

        Used to detect PEC delivery failures. If a PEC message doesn't
        receive an acceptance receipt within the expected timeframe,
        it may indicate a delivery problem.

        Returns messages where:
        - is_pec = 1 (marked as PEC)
        - smtp_ts < cutoff_ts (sent before cutoff)
        - No pec_acceptance event exists in message_events

        Args:
            cutoff_ts: Unix timestamp. Messages sent before this time
                without acceptance receipt are returned.

        Returns:
            List of message dicts with pk, id, account_id, smtp_ts.
        """
        rows = await self.db.adapter.fetch_all(  # type: ignore[attr-defined]
            """
            SELECT m.pk, m.id, m.account_id, m.smtp_ts
            FROM messages m
            WHERE m.is_pec = 1
              AND m.smtp_ts IS NOT NULL
              AND m.smtp_ts < :cutoff_ts
              AND NOT EXISTS (
                  SELECT 1 FROM message_events e
                  WHERE e.message_pk = m.pk
                    AND e.event_type = 'pec_acceptance'
              )
            """,
            {"cutoff_ts": cutoff_ts},
        )
        return [dict(row) for row in rows]


__all__ = ["MessagesTable_EE"]
