# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Messages table manager for email queue."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from ..sql import Integer, String, Table, Timestamp


class MessagesTable(Table):
    """Messages table: email queue with status tracking.

    Fields:
    - id: Message identifier
    - account_id: SMTP account (FK)
    - priority: 1=high, 2=normal, 3=low
    - payload: JSON-encoded message data
    - deferred_ts: Timestamp when message can be retried
    - sent_ts: Timestamp when message was sent
    - error_ts: Timestamp when error occurred
    - error: Error message
    - reported_ts: Timestamp when delivery status was reported to client
    """

    name = "messages"

    def configure(self) -> None:
        c = self.columns
        c.column("id", String, primary_key=True)
        c.column("account_id", String)
        c.column("priority", Integer, nullable=False, default=2)
        c.column("payload", String, nullable=False)  # JSON but handled specially
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("deferred_ts", Integer)
        c.column("sent_ts", Integer)
        c.column("error_ts", Integer)
        c.column("error", String)
        c.column("reported_ts", Integer)

    async def insert_batch(self, entries: Sequence[dict[str, Any]]) -> list[str]:
        """Persist a batch of messages for delivery. Returns list of inserted IDs."""
        if not entries:
            return []

        inserted: list[str] = []
        for entry in entries:
            msg_id = entry["id"]
            payload = json.dumps(entry["payload"])
            account_id = entry.get("account_id")
            priority = int(entry.get("priority", 2))
            deferred_ts = entry.get("deferred_ts")

            rowcount = await self.execute(
                """
                INSERT INTO messages (id, account_id, priority, payload, deferred_ts)
                VALUES (:id, :account_id, :priority, :payload, :deferred_ts)
                ON CONFLICT(id) DO UPDATE SET
                    account_id = excluded.account_id,
                    priority = excluded.priority,
                    payload = excluded.payload,
                    deferred_ts = excluded.deferred_ts,
                    error_ts = NULL,
                    error = NULL,
                    reported_ts = NULL,
                    updated_at = CURRENT_TIMESTAMP
                WHERE sent_ts IS NULL
                """,
                {
                    "id": msg_id,
                    "account_id": account_id,
                    "priority": priority,
                    "payload": payload,
                    "deferred_ts": deferred_ts,
                },
            )

            if rowcount:
                inserted.append(msg_id)

        return inserted

    async def fetch_ready(
        self,
        *,
        limit: int,
        now_ts: int,
        priority: int | None = None,
        min_priority: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch messages ready for SMTP delivery."""
        conditions = [
            "sent_ts IS NULL",
            "error_ts IS NULL",
            "(deferred_ts IS NULL OR deferred_ts <= :now_ts)",
        ]
        params: dict[str, Any] = {"now_ts": now_ts, "limit": limit}

        if priority is not None:
            conditions.append("priority = :priority")
            params["priority"] = priority
        elif min_priority is not None:
            conditions.append("priority >= :min_priority")
            params["min_priority"] = min_priority

        query = f"""
            SELECT id, account_id, priority, payload, deferred_ts
            FROM messages
            WHERE {' AND '.join(conditions)}
            ORDER BY priority ASC, created_at ASC, id ASC
            LIMIT :limit
        """

        rows = await self.db.adapter.fetch_all(query, params)
        return [self._decode_payload(row) for row in rows]

    async def set_deferred(self, msg_id: str, deferred_ts: int) -> None:
        """Update the deferred timestamp for a message."""
        await self.execute(
            """
            UPDATE messages
            SET deferred_ts = :deferred_ts, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id AND sent_ts IS NULL AND error_ts IS NULL
            """,
            {"deferred_ts": deferred_ts, "msg_id": msg_id},
        )

    async def clear_deferred(self, msg_id: str) -> None:
        """Clear the deferred timestamp for a message."""
        await self.execute(
            """
            UPDATE messages
            SET deferred_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
            """,
            {"msg_id": msg_id},
        )

    async def mark_sent(self, msg_id: str, sent_ts: int) -> None:
        """Mark a message as sent."""
        await self.execute(
            """
            UPDATE messages
            SET sent_ts = :sent_ts, error_ts = NULL, error = NULL, deferred_ts = NULL,
                reported_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
            """,
            {"sent_ts": sent_ts, "msg_id": msg_id},
        )

    async def mark_error(self, msg_id: str, error_ts: int, error: str) -> None:
        """Mark a message as failed."""
        await self.execute(
            """
            UPDATE messages
            SET error_ts = :error_ts, error = :error, sent_ts = NULL, deferred_ts = NULL,
                reported_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
            """,
            {"error_ts": error_ts, "error": error, "msg_id": msg_id},
        )

    async def update_payload(self, msg_id: str, payload: dict[str, Any]) -> None:
        """Update the payload field of a message."""
        await self.execute(
            """
            UPDATE messages
            SET payload = :payload, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
            """,
            {"payload": json.dumps(payload), "msg_id": msg_id},
        )

    async def remove(self, msg_id: str) -> bool:
        """Remove a message regardless of its state. Returns True if deleted."""
        rowcount = await self.delete(where={"id": msg_id})
        return rowcount > 0

    async def purge_for_account(self, account_id: str) -> None:
        """Delete every message linked to the given account."""
        await self.delete(where={"account_id": account_id})

    async def existing_ids(self, ids: Iterable[str]) -> set[str]:
        """Return the subset of ids that already exist in storage."""
        id_list = [mid for mid in ids if mid]
        if not id_list:
            return set()

        params = {f"id_{i}": mid for i, mid in enumerate(id_list)}
        placeholders = ", ".join(f":id_{i}" for i in range(len(id_list)))
        rows = await self.db.adapter.fetch_all(
            f"SELECT id FROM messages WHERE id IN ({placeholders})",
            params,
        )
        return {row["id"] for row in rows}

    async def fetch_reports(self, limit: int) -> list[dict[str, Any]]:
        """Return messages that need to be reported back to the client."""
        rows = await self.db.adapter.fetch_all(
            """
            SELECT m.id, m.account_id, m.priority, m.payload, m.sent_ts, m.error_ts,
                   m.error, m.deferred_ts, a.tenant_id
            FROM messages m
            LEFT JOIN accounts a ON m.account_id = a.id
            WHERE m.reported_ts IS NULL
              AND (m.sent_ts IS NOT NULL OR m.error_ts IS NOT NULL)
            ORDER BY m.updated_at ASC, m.id ASC
            LIMIT :limit
            """,
            {"limit": limit},
        )
        return [self._decode_payload(row) for row in rows]

    async def mark_reported(self, message_ids: Iterable[str], reported_ts: int) -> None:
        """Set the reported timestamp for the provided messages."""
        ids = [mid for mid in message_ids if mid]
        if not ids:
            return

        params: dict[str, Any] = {"reported_ts": reported_ts}
        params.update({f"id_{i}": mid for i, mid in enumerate(ids)})
        placeholders = ", ".join(f":id_{i}" for i in range(len(ids)))
        await self.execute(
            f"""
            UPDATE messages
            SET reported_ts = :reported_ts, updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            params,
        )

    async def remove_reported_before(self, threshold_ts: int) -> int:
        """Delete reported messages older than threshold_ts. Returns deleted count."""
        return await self.execute(
            """
            DELETE FROM messages
            WHERE reported_ts IS NOT NULL
              AND reported_ts < :threshold_ts
              AND (sent_ts IS NOT NULL OR error_ts IS NOT NULL)
            """,
            {"threshold_ts": threshold_ts},
        )

    async def list_all(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Return messages for inspection purposes."""
        query = """
            SELECT id, account_id, priority, payload, deferred_ts, sent_ts, error_ts,
                   error, reported_ts, created_at, updated_at
            FROM messages
        """
        if active_only:
            query += " WHERE sent_ts IS NULL AND error_ts IS NULL"
        query += " ORDER BY priority ASC, created_at ASC, id ASC"

        rows = await self.db.adapter.fetch_all(query)
        return [self._decode_payload(row) for row in rows]

    async def count_active(self) -> int:
        """Return the number of messages still awaiting delivery."""
        row = await self.db.adapter.fetch_one(
            """
            SELECT COUNT(*) as cnt FROM messages
            WHERE sent_ts IS NULL AND error_ts IS NULL
            """
        )
        return int(row["cnt"]) if row else 0

    def _decode_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        """Decode payload JSON in message dict."""
        payload = data.pop("payload", None)
        if payload is not None:
            try:
                data["message"] = json.loads(payload)
            except json.JSONDecodeError:
                data["message"] = {"raw_payload": payload}
        else:
            data["message"] = None
        return data


__all__ = ["MessagesTable"]
