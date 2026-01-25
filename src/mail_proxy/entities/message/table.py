# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Messages table manager for email queue."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from ...sql import Integer, String, Table, Timestamp


class MessagesTable(Table):
    """Messages table: email queue with scheduling.

    Fields:
    - id: Message identifier
    - account_id: SMTP account (FK)
    - priority: 1=high, 2=normal, 3=low
    - payload: JSON-encoded message data
    - batch_code: Optional batch/campaign identifier for grouping messages
    - deferred_ts: Timestamp when message can be retried (for retry scheduling)
    - smtp_ts: Timestamp when SMTP send was attempted (NULL = not yet attempted)

    Delivery status and reporting are tracked in the message_events table.
    """

    name = "messages"

    def configure(self) -> None:
        c = self.columns
        c.column("id", String, primary_key=True)
        c.column("account_id", String)
        c.column("priority", Integer, nullable=False, default=2)
        c.column("payload", String, nullable=False)  # JSON but handled specially
        c.column("batch_code", String)  # Optional batch/campaign identifier
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("deferred_ts", Integer)  # For retry scheduling
        c.column("smtp_ts", Integer)  # When SMTP send was attempted (NULL = pending)

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
            batch_code = entry.get("batch_code")

            rowcount = await self.execute(
                """
                INSERT INTO messages (id, account_id, priority, payload, batch_code, deferred_ts)
                VALUES (:id, :account_id, :priority, :payload, :batch_code, :deferred_ts)
                ON CONFLICT(id) DO UPDATE SET
                    account_id = excluded.account_id,
                    priority = excluded.priority,
                    payload = excluded.payload,
                    batch_code = excluded.batch_code,
                    deferred_ts = excluded.deferred_ts,
                    updated_at = CURRENT_TIMESTAMP
                WHERE messages.smtp_ts IS NULL
                """,
                {
                    "id": msg_id,
                    "account_id": account_id,
                    "priority": priority,
                    "payload": payload,
                    "batch_code": batch_code,
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
        """Fetch messages ready for SMTP delivery.

        Excludes messages from suspended tenants/batches:
        - If tenant.suspended_batches = "*", all messages are skipped
        - If tenant.suspended_batches contains message.batch_code, message is skipped
        - Messages without batch_code are only skipped when suspended_batches = "*"
        """
        conditions = [
            "m.smtp_ts IS NULL",
            "(m.deferred_ts IS NULL OR m.deferred_ts <= :now_ts)",
        ]
        params: dict[str, Any] = {"now_ts": now_ts, "limit": limit}

        if priority is not None:
            conditions.append("m.priority = :priority")
            params["priority"] = priority
        elif min_priority is not None:
            conditions.append("m.priority >= :min_priority")
            params["min_priority"] = min_priority

        # Exclude suspended batches:
        # - t.suspended_batches IS NULL → not suspended
        # - t.suspended_batches = '*' → fully suspended (skip all)
        # - m.batch_code is in suspended list → skip
        # - m.batch_code IS NULL and suspended_batches != '*' → not suspended
        # Note: Use named placeholders for LIKE wildcards to avoid psycopg % interpretation
        suspension_filter = """
            (
                t.suspended_batches IS NULL
                OR (
                    t.suspended_batches != '*'
                    AND (
                        m.batch_code IS NULL
                        OR NOT (',' || t.suspended_batches || ',' LIKE :like_prefix || m.batch_code || :like_suffix)
                    )
                )
            )
        """
        params["like_prefix"] = "%,"
        params["like_suffix"] = ",%"
        conditions.append(suspension_filter)

        query = f"""
            SELECT m.id, m.account_id, m.priority, m.payload, m.batch_code, m.deferred_ts
            FROM messages m
            LEFT JOIN accounts a ON m.account_id = a.id
            LEFT JOIN tenants t ON a.tenant_id = t.id
            WHERE {' AND '.join(conditions)}
            ORDER BY m.priority ASC, m.created_at ASC, m.id ASC
            LIMIT :limit
        """

        rows = await self.db.adapter.fetch_all(query, params)
        return [self._decode_payload(row) for row in rows]

    async def set_deferred(self, msg_id: str, deferred_ts: int) -> None:
        """Put message back in queue for retry at deferred_ts.

        Resets smtp_ts to NULL so the message becomes "pending" again.
        """
        await self.execute(
            """
            UPDATE messages
            SET deferred_ts = :deferred_ts, smtp_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
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

    async def mark_sent(self, msg_id: str, smtp_ts: int) -> None:
        """Mark a message as processed (SMTP attempted successfully)."""
        await self.execute(
            """
            UPDATE messages
            SET smtp_ts = :smtp_ts, deferred_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
            """,
            {"smtp_ts": smtp_ts, "msg_id": msg_id},
        )

    async def mark_error(self, msg_id: str, smtp_ts: int) -> None:
        """Mark a message as processed (SMTP attempted with error)."""
        await self.execute(
            """
            UPDATE messages
            SET smtp_ts = :smtp_ts, deferred_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
            """,
            {"smtp_ts": smtp_ts, "msg_id": msg_id},
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

    async def get(self, msg_id: str) -> dict[str, Any] | None:
        """Get a single message by ID. Returns None if not found."""
        row = await self.db.adapter.fetch_one(
            "SELECT * FROM messages WHERE id = :id",
            {"id": msg_id},
        )
        if row is None:
            return None
        return self._decode_payload(row)

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

    async def get_ids_for_tenant(self, ids: list[str], tenant_id: str) -> set[str]:
        """Return the subset of ids that belong to the specified tenant.

        Validates ownership by joining with accounts table.

        Args:
            ids: List of message IDs to check.
            tenant_id: Tenant ID to validate ownership against.

        Returns:
            Set of message IDs that belong to the tenant.
        """
        if not ids:
            return set()

        params: dict[str, Any] = {"tenant_id": tenant_id}
        params.update({f"id_{i}": mid for i, mid in enumerate(ids)})
        placeholders = ", ".join(f":id_{i}" for i in range(len(ids)))

        rows = await self.db.adapter.fetch_all(
            f"""
            SELECT m.id
            FROM messages m
            JOIN accounts a ON m.account_id = a.id
            WHERE m.id IN ({placeholders})
              AND a.tenant_id = :tenant_id
            """,
            params,
        )
        return {row["id"] for row in rows}

    async def remove_fully_reported_before(self, threshold_ts: int) -> int:
        """Delete messages whose all events have been reported before threshold.

        A message can be removed when:
        - It has been processed (smtp_ts IS NOT NULL)
        - All its events have been reported
        - The most recent reported_ts is older than threshold

        Returns:
            Number of deleted messages.
        """
        return await self.execute(
            """
            DELETE FROM messages
            WHERE smtp_ts IS NOT NULL
              AND id IN (
                  SELECT m.id FROM messages m
                  WHERE m.smtp_ts IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM message_events e
                        WHERE e.message_id = m.id AND e.reported_ts IS NULL
                    )
                    AND (
                        SELECT MAX(e.reported_ts) FROM message_events e
                        WHERE e.message_id = m.id
                    ) < :threshold_ts
              )
            """,
            {"threshold_ts": threshold_ts},
        )

    async def remove_fully_reported_before_for_tenant(
        self, threshold_ts: int, tenant_id: str
    ) -> int:
        """Delete fully reported messages older than threshold for a tenant.

        Returns:
            Number of deleted messages.
        """
        return await self.execute(
            """
            DELETE FROM messages
            WHERE id IN (
                SELECT m.id FROM messages m
                JOIN accounts a ON m.account_id = a.id
                WHERE a.tenant_id = :tenant_id
                  AND m.smtp_ts IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM message_events e
                      WHERE e.message_id = m.id AND e.reported_ts IS NULL
                  )
                  AND (
                      SELECT MAX(e.reported_ts) FROM message_events e
                      WHERE e.message_id = m.id
                  ) < :threshold_ts
            )
            """,
            {"threshold_ts": threshold_ts, "tenant_id": tenant_id},
        )

    async def list_all(
        self, *, tenant_id: str | None = None, active_only: bool = False
    ) -> list[dict[str, Any]]:
        """Return messages for inspection purposes, optionally filtered by tenant.

        Args:
            tenant_id: If provided, filter messages to those belonging to this tenant
                (via the account's tenant_id).
            active_only: If True, only return messages pending delivery.
        """
        params: dict[str, Any] = {}
        where_clauses: list[str] = []

        if tenant_id:
            # Join with accounts to filter by tenant_id
            query = """
                SELECT m.id, m.account_id, m.priority, m.payload, m.batch_code,
                       m.deferred_ts, m.smtp_ts, m.created_at, m.updated_at
                FROM messages m
                LEFT JOIN accounts a ON m.account_id = a.id
            """
            where_clauses.append("a.tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id
        else:
            query = """
                SELECT id, account_id, priority, payload, batch_code,
                       deferred_ts, smtp_ts, created_at, updated_at
                FROM messages
            """

        if active_only:
            if tenant_id:
                where_clauses.append("m.smtp_ts IS NULL")
            else:
                where_clauses.append("smtp_ts IS NULL")

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        if tenant_id:
            query += " ORDER BY m.priority ASC, m.created_at ASC, m.id ASC"
        else:
            query += " ORDER BY priority ASC, created_at ASC, id ASC"

        rows = await self.db.adapter.fetch_all(query, params)
        return [self._decode_payload(row) for row in rows]

    async def count_active(self) -> int:
        """Return the number of messages still awaiting delivery."""
        row = await self.db.adapter.fetch_one(
            """
            SELECT COUNT(*) as cnt FROM messages
            WHERE smtp_ts IS NULL
            """
        )
        return int(row["cnt"]) if row else 0

    async def count_pending_for_tenant(
        self, tenant_id: str, batch_code: str | None = None
    ) -> int:
        """Count pending messages for a tenant, optionally filtered by batch_code.

        Args:
            tenant_id: Tenant ID to filter by.
            batch_code: Optional batch code. If provided, only count messages
                with this batch_code. If None, count all pending messages.

        Returns:
            Number of pending messages.
        """
        params: dict[str, Any] = {"tenant_id": tenant_id}

        if batch_code is not None:
            query = """
                SELECT COUNT(*) as cnt
                FROM messages m
                JOIN accounts a ON m.account_id = a.id
                WHERE a.tenant_id = :tenant_id
                  AND m.batch_code = :batch_code
                  AND m.smtp_ts IS NULL
            """
            params["batch_code"] = batch_code
        else:
            query = """
                SELECT COUNT(*) as cnt
                FROM messages m
                JOIN accounts a ON m.account_id = a.id
                WHERE a.tenant_id = :tenant_id
                  AND m.smtp_ts IS NULL
            """

        row = await self.db.adapter.fetch_one(query, params)
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
