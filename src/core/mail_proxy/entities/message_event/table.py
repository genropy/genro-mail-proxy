# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Message event table for delivery tracking.

This module implements event-based tracking for message lifecycle changes.
Each significant state change (sent, error, deferred, bounce, PEC receipts)
is recorded as a separate event, enabling complete delivery history.

Components:
    MessageEventTable: Table manager for message events.

Event Types:
    - deferred: Message was deferred (rate limit, temporary failure)
    - sent: Message successfully delivered via SMTP
    - error: Permanent delivery failure
    - bounce: Bounce notification received (EE)
    - pec_acceptance: PEC acceptance receipt (EE)
    - pec_delivery: PEC delivery receipt (EE)
    - pec_error: PEC error notification (EE)

Example:
    Record and query events::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        events = proxy.db.table("message_events")

        # Record a sent event
        await events.add_event(
            message_pk="550e8400-e29b-41d4-a716-446655440000",
            event_type="sent",
            event_ts=1704067200,
            description="250 OK id=1abc-2def",
        )

        # Fetch unreported events for sync
        unreported = await events.fetch_unreported(limit=100)

        # Mark as reported
        event_ids = [e["event_id"] for e in unreported]
        await events.mark_reported(event_ids, reported_ts=1704067260)

Note:
    Events are linked to messages via message_pk (UUID primary key).
    The trigger_on_inserted method automatically updates message status
    when events are recorded.
"""

from __future__ import annotations

import json
from typing import Any

from sql import Integer, String, Table


class MessageEventTable(Table):
    """Message events storage table.

    Records delivery events for complete message history and reporting.
    Each event links to a message via message_pk and includes timestamp,
    type, description, and optional metadata.

    Attributes:
        name: Table name ("message_events").
        pkey: Primary key column ("id", auto-increment INTEGER).

    Table Schema:
        - id: Auto-increment primary key
        - message_pk: FK to messages.pk (UUID)
        - event_type: Event category (sent, error, deferred, etc.)
        - event_ts: Unix timestamp when event occurred
        - description: Error message, bounce reason, etc.
        - metadata: JSON for extra data (bounce_type, deferred_ts)
        - reported_ts: When event was synced to client

    Example:
        Query message history::

            events = proxy.db.table("message_events")

            # Get all events for a message
            history = await events.get_events_for_message(
                "550e8400-e29b-41d4-a716-446655440000"
            )
            for event in history:
                print(f"{event['event_type']} at {event['event_ts']}")

            # Check pending reports
            count = await events.count_unreported_for_message(message_pk)
    """

    name = "message_events"
    pkey = "id"

    def new_pkey_value(self) -> None:
        """Return None for INTEGER PRIMARY KEY autoincrement."""
        return None

    def configure(self) -> None:
        """Define table columns.

        Columns:
            id: Auto-increment primary key (INTEGER).
            message_pk: Reference to messages.pk (UUID string).
            event_type: Event category for filtering.
            event_ts: Unix timestamp of event occurrence.
            description: Human-readable event details.
            metadata: JSON-encoded extra data.
            reported_ts: Unix timestamp when synced to client.
        """
        c = self.columns
        c.column("id", Integer)  # autoincrement
        c.column("message_pk", String, nullable=False)
        c.column("event_type", String, nullable=False)
        c.column("event_ts", Integer, nullable=False)
        c.column("description", String)
        c.column("metadata", String)
        c.column("reported_ts", Integer)

    async def trigger_on_inserted(self, record: dict[str, Any]) -> None:
        """Update message status based on event type.

        Called automatically after event insert. Updates the message's
        state in the messages table based on event_type.

        Args:
            record: Inserted event record with event_type and message_pk.

        Note:
            - "sent" → marks message as sent
            - "error" → marks message as error
            - "deferred" → sets deferred_ts from metadata or event_ts
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
        """Record a message event.

        Args:
            message_pk: Message's internal pk (UUID).
            event_type: Event category (sent, error, deferred, bounce, pec_*).
            event_ts: Unix timestamp when event occurred.
            description: Optional error message or reason.
            metadata: Optional dict serialized as JSON.

        Returns:
            Number of rows inserted (typically 1).

        Note:
            Triggers are called automatically after insert,
            updating message status based on event_type.
        """
        return await self.insert(
            {
                "message_pk": message_pk,
                "event_type": event_type,
                "event_ts": event_ts,
                "description": description,
                "metadata": json.dumps(metadata) if metadata else None,
            }
        )

    async def fetch_unreported(self, limit: int) -> list[dict[str, Any]]:
        """Fetch events not yet reported to clients.

        Args:
            limit: Maximum number of events to return.

        Returns:
            List of event dicts with message_id for external reporting,
            ordered chronologically by event_ts.

        Example:
            ::

                unreported = await events.fetch_unreported(limit=100)
                for event in unreported:
                    # event contains: event_id, message_id, event_type,
                    # event_ts, description, metadata, account_id, tenant_id
                    await report_to_client(event)
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
            if event.get("metadata"):
                try:
                    event["metadata"] = json.loads(event["metadata"])
                except (json.JSONDecodeError, TypeError):
                    event["metadata"] = None
            result.append(event)
        return result

    async def mark_reported(self, event_ids: list[int], reported_ts: int) -> None:
        """Mark events as reported to client.

        Args:
            event_ids: List of event IDs to mark.
            reported_ts: Unix timestamp of report.
        """
        if not event_ids:
            return
        await self.update_batch_raw(
            pkeys=event_ids,
            updater={"reported_ts": reported_ts},
        )

    async def get_events_for_message(self, message_pk: str) -> list[dict[str, Any]]:
        """Get all events for a message, ordered chronologically.

        Args:
            message_pk: Internal message pk (UUID).

        Returns:
            List of event dicts with parsed metadata.
        """
        rows = await self.db.adapter.fetch_all(
            """
            SELECT id as event_id, message_pk, event_type, event_ts,
                   description, metadata, reported_ts
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
        """Delete all events for a message.

        Args:
            message_pk: Internal message pk (UUID).

        Returns:
            Number of deleted records.
        """
        return await self.delete(where={"message_pk": message_pk})

    async def count_unreported_for_message(self, message_pk: str) -> int:
        """Count unreported events for a message.

        Args:
            message_pk: Internal message pk (UUID).

        Returns:
            Number of events with reported_ts IS NULL.
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
