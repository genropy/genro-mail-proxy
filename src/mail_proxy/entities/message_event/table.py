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

from ...sql import Integer, String, Table


class MessageEventTable(Table):
    """Message events table: delivery events for reporting.

    Each event represents a state change in a message's lifecycle.
    Events are reported to clients and tracked via reported_ts.
    """

    name = "message_events"

    def configure(self) -> None:
        c = self.columns
        c.column("id", Integer, primary_key=True)  # autoincrement
        c.column("message_id", String, nullable=False)
        c.column("event_type", String, nullable=False)
        c.column("event_ts", Integer, nullable=False)
        c.column("description", String)  # error message, bounce reason, etc.
        c.column("metadata", String)  # JSON for extra data (bounce_type, bounce_code)
        c.column("reported_ts", Integer)  # when event was reported to client

    async def add_event(
        self,
        message_id: str,
        event_type: str,
        event_ts: int,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record a message event.

        Args:
            message_id: The message this event belongs to.
            event_type: Type of event (sent, error, deferred, bounce, pec_*).
            event_ts: Unix timestamp when the event occurred.
            description: Optional description (error message, bounce reason).
            metadata: Optional dict of extra data (serialized as JSON).

        Returns:
            The ID of the inserted event.
        """
        metadata_json = json.dumps(metadata) if metadata else None
        params = {
            "message_id": message_id,
            "event_type": event_type,
            "event_ts": event_ts,
            "description": description,
            "metadata": metadata_json,
        }

        # Check if using PostgreSQL (has psycopg pool)
        is_postgres = hasattr(self.db.adapter, "_pool") and self.db.adapter._pool is not None

        if is_postgres:
            # PostgreSQL: use RETURNING to get the auto-generated id
            row = await self.db.adapter.fetch_one(
                """
                INSERT INTO message_events (message_id, event_type, event_ts, description, metadata)
                VALUES (:message_id, :event_type, :event_ts, :description, :metadata)
                RETURNING id
                """,
                params,
            )
            return int(row["id"]) if row else 0
        else:
            # SQLite: use last_insert_rowid()
            await self.execute(
                """
                INSERT INTO message_events (message_id, event_type, event_ts, description, metadata)
                VALUES (:message_id, :event_type, :event_ts, :description, :metadata)
                """,
                params,
            )
            row = await self.db.adapter.fetch_one("SELECT last_insert_rowid() as id", {})
            return int(row["id"]) if row else 0

    async def fetch_unreported(self, limit: int) -> list[dict[str, Any]]:
        """Fetch events that haven't been reported to clients yet.

        Returns events ordered by event_ts to maintain chronological order.
        """
        rows = await self.db.adapter.fetch_all(
            """
            SELECT
                e.id as event_id,
                e.message_id,
                e.event_type,
                e.event_ts,
                e.description,
                e.metadata,
                m.account_id,
                a.tenant_id
            FROM message_events e
            JOIN messages m ON e.message_id = m.id
            LEFT JOIN accounts a ON m.account_id = a.id
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
        """Mark events as reported to client."""
        if not event_ids:
            return
        params: dict[str, Any] = {"reported_ts": reported_ts}
        params.update({f"id_{i}": eid for i, eid in enumerate(event_ids)})
        placeholders = ", ".join(f":id_{i}" for i in range(len(event_ids)))
        await self.execute(
            f"""
            UPDATE message_events
            SET reported_ts = :reported_ts
            WHERE id IN ({placeholders})
            """,
            params,
        )

    async def get_events_for_message(self, message_id: str) -> list[dict[str, Any]]:
        """Get all events for a specific message, ordered chronologically."""
        rows = await self.db.adapter.fetch_all(
            """
            SELECT id as event_id, message_id, event_type, event_ts, description, metadata, reported_ts
            FROM message_events
            WHERE message_id = :message_id
            ORDER BY event_ts ASC, event_id ASC
            """,
            {"message_id": message_id},
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

    async def delete_for_message(self, message_id: str) -> int:
        """Delete all events for a message. Returns deleted count."""
        return await self.delete(where={"message_id": message_id})

    async def count_unreported_for_message(self, message_id: str) -> int:
        """Count unreported events for a message."""
        row = await self.db.adapter.fetch_one(
            """
            SELECT COUNT(*) as cnt
            FROM message_events
            WHERE message_id = :message_id AND reported_ts IS NULL
            """,
            {"message_id": message_id},
        )
        return int(row["cnt"]) if row else 0



__all__ = ["MessageEventTable"]
