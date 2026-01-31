# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Message events table manager for event-based delivery tracking.

This module implements event-based tracking for message lifecycle events.
Each significant state change (sent, error, deferred, bounce, PEC receipts)
is recorded as a separate event, enabling complete delivery history and
flexible reporting.

Event types:
- deferred: Message was deferred (rate limit, temporary failure, etc.)
- sent: Message was successfully sent via SMTP
- error: Message delivery failed permanently
- bounce: Bounce notification received
- pec_acceptance: PEC acceptance receipt (ricevuta di accettazione)
- pec_delivery: PEC delivery receipt (ricevuta di consegna)
- pec_error: PEC error notification
"""

from __future__ import annotations

import json
from typing import Any

from sql import Integer, String, Table


class MessageEventTable(Table):
    """Message events table: delivery events for reporting.

    Each event represents a state change in a message's lifecycle.
    Events are reported to clients and tracked via reported_ts.
    Events are linked to messages via message_pk (the internal UUID primary key).
    """

    name = "message_events"
    pkey = "id"

    def new_pkey_value(self) -> None:
        """INTEGER PRIMARY KEY uses SQLite autoincrement - no value needed."""
        return None

    def configure(self) -> None:
        c = self.columns
        c.column("id", Integer)  # autoincrement
        c.column("message_pk", String, nullable=False)  # FK to messages.pk (UUID)
        c.column("event_type", String, nullable=False)
        c.column("event_ts", Integer, nullable=False)
        c.column("description", String)  # error message, bounce reason, etc.
        c.column("metadata", String)  # JSON for extra data (bounce_type, bounce_code)
        c.column("reported_ts", Integer)  # when event was reported to client

    async def trigger_on_inserted(self, record: dict[str, Any]) -> None:
        """Update message status based on event type.

        This trigger is called after each event insert and updates the
        corresponding message's state in the messages table.
        """
        event_type = record.get("event_type")
        message_pk = record.get("message_pk")
        event_ts = record.get("event_ts")

        if not message_pk or not event_ts:
            return

        messages = self.db.table("messages")

        if event_type == "sent":
            await messages.mark_sent(message_pk, event_ts)
        elif event_type == "error":
            await messages.mark_error(message_pk, event_ts)
        elif event_type == "deferred":
            # For deferred, event_ts is when the deferral happened,
            # but we need deferred_ts for when to retry.
            # The metadata should contain the actual deferred_ts.
            metadata = record.get("metadata")
            if metadata:
                try:
                    meta_dict = json.loads(metadata) if isinstance(metadata, str) else metadata
                    deferred_ts = meta_dict.get("deferred_ts", event_ts)
                except (json.JSONDecodeError, TypeError):
                    deferred_ts = event_ts
            else:
                deferred_ts = event_ts
            await messages.set_deferred(message_pk, deferred_ts)

    async def add_event(
        self,
        message_pk: str,
        event_type: str,
        event_ts: int,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record a message event. Triggers are called automatically.

        Args:
            message_pk: The message's internal pk (UUID) this event belongs to.
            event_type: Type of event (sent, error, deferred, bounce, pec_*).
            event_ts: Unix timestamp when the event occurred.
            description: Optional description (error message, bounce reason).
            metadata: Optional dict of extra data (serialized as JSON).

        Returns:
            Number of rows inserted (typically 1).
        """
        return await self.insert({
            "message_pk": message_pk,
            "event_type": event_type,
            "event_ts": event_ts,
            "description": description,
            "metadata": json.dumps(metadata) if metadata else None,
        })

    async def fetch_unreported(self, limit: int) -> list[dict[str, Any]]:
        """Fetch events that haven't been reported to clients yet.

        Returns events ordered by event_ts to maintain chronological order.
        Includes message_id (client-facing ID) for external reporting.
        """
        rows = await self.db.adapter.fetch_all(
            """
            SELECT
                e.id as event_id,
                e.message_pk,
                m.id as message_id,
                e.event_type,
                e.event_ts,
                e.description,
                e.metadata,
                m.account_id,
                m.tenant_id
            FROM message_events e
            JOIN messages m ON e.message_pk = m.pk
            LEFT JOIN accounts a ON m.account_pk = a.pk
            WHERE e.reported_ts IS NULL
            ORDER BY e.event_ts ASC, e.id ASC
            LIMIT :limit
            """,
            {"limit": limit},
        )
        result = []
        for row in rows:
            event = dict(row)
            # Parse metadata JSON if present
            if event.get("metadata"):
                try:
                    event["metadata"] = json.loads(event["metadata"])
                except (json.JSONDecodeError, TypeError):
                    event["metadata"] = None
            result.append(event)
        return result

    async def mark_reported(self, event_ids: list[int], reported_ts: int) -> None:
        """Mark events as reported to client. No triggers needed."""
        if not event_ids:
            return
        await self.update_batch_raw(
            pkeys=event_ids,
            updater={"reported_ts": reported_ts},
        )

    async def get_events_for_message(self, message_pk: str) -> list[dict[str, Any]]:
        """Get all events for a specific message, ordered chronologically.

        Args:
            message_pk: Internal message pk (UUID).
        """
        rows = await self.db.adapter.fetch_all(
            """
            SELECT id as event_id, message_pk, event_type, event_ts, description, metadata, reported_ts
            FROM message_events
            WHERE message_pk = :message_pk
            ORDER BY event_ts ASC, event_id ASC
            """,
            {"message_pk": message_pk},
        )
        result = []
        for row in rows:
            event = dict(row)
            if event.get("metadata"):
                try:
                    event["metadata"] = json.loads(event["metadata"])
                except (json.JSONDecodeError, TypeError):
                    event["metadata"] = None
            result.append(event)
        return result

    async def delete_for_message(self, message_pk: str) -> int:
        """Delete all events for a message. Returns deleted count.

        Args:
            message_pk: Internal message pk (UUID).
        """
        return await self.delete(where={"message_pk": message_pk})

    async def count_unreported_for_message(self, message_pk: str) -> int:
        """Count unreported events for a message.

        Args:
            message_pk: Internal message pk (UUID).
        """
        row = await self.db.adapter.fetch_one(
            """
            SELECT COUNT(*) as cnt
            FROM message_events
            WHERE message_pk = :message_pk AND reported_ts IS NULL
            """,
            {"message_pk": message_pk},
        )
        return int(row["cnt"]) if row else 0



__all__ = ["MessageEventTable"]
