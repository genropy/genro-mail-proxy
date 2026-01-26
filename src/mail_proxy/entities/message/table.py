# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Messages table manager for email queue."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from ...sql import Integer, String, Table, Timestamp
from ...uid import get_uuid


class MessagesTable(Table):
    """Messages table: email queue with scheduling.

    Fields:
    - pk: Internal primary key (autoincrement)
    - tenant_id, id: Composite unique key for multi-tenant isolation
    - account_id: SMTP account (FK)
    - priority: 1=high, 2=normal, 3=low
    - payload: JSON-encoded message data
    - batch_code: Optional batch/campaign identifier for grouping messages
    - deferred_ts: Timestamp when message can be retried (for retry scheduling)
    - smtp_ts: Timestamp when SMTP send was attempted (NULL = not yet attempted)
    - is_pec: PEC flag (1=awaiting PEC receipts, 0=normal email)

    Multi-tenant isolation is enforced via UNIQUE (tenant_id, id).
    Delivery status and reporting are tracked in the message_events table.
    """

    name = "messages"

    def create_table_sql(self) -> str:
        """Generate CREATE TABLE with UNIQUE (tenant_id, id) for multi-tenant isolation."""
        sql = super().create_table_sql()
        # Add UNIQUE constraint before final closing parenthesis
        last_paren = sql.rfind(")")
        return sql[:last_paren] + ',\n    UNIQUE ("tenant_id", "id")\n)'

    def configure(self) -> None:
        c = self.columns
        c.column("pk", String, primary_key=True)  # get_uuid() generated
        c.column("id", String, nullable=False)  # message_id from client
        c.column("tenant_id", String, nullable=False)  # denormalized for isolation
        c.column("account_id", String)
        c.column("priority", Integer, nullable=False, default=2)
        c.column("payload", String, nullable=False)  # JSON but handled specially
        c.column("batch_code", String)  # Optional batch/campaign identifier
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("deferred_ts", Integer)  # For retry scheduling
        c.column("smtp_ts", Integer)  # When SMTP send was attempted (NULL = pending)
        c.column("is_pec", Integer, default=0)  # PEC flag (1=awaiting receipts)

    async def insert_batch(
        self,
        entries: Sequence[dict[str, Any]],
        pec_account_ids: set[str] | None = None,
        tenant_id: str | None = None,
    ) -> list[dict[str, str]]:
        """Persist a batch of messages for delivery.

        Returns list of dicts with 'id' (message_id) and 'pk' (internal key)
        for each successfully inserted message.

        Args:
            entries: List of message entries to insert.
            pec_account_ids: Set of account IDs that are PEC accounts.
                Messages sent via these accounts will have is_pec=1.
            tenant_id: Tenant ID for multi-tenant isolation. Required unless
                each entry has its own tenant_id.

        Returns:
            List of {"id": msg_id, "pk": pk} for inserted messages.
        """
        if not entries:
            return []

        pec_accounts = pec_account_ids or set()
        inserted: list[dict[str, str]] = []
        for entry in entries:
            msg_id = entry["id"]
            # Use entry's tenant_id if present, otherwise use the parameter
            entry_tenant_id = entry.get("tenant_id") or tenant_id
            if not entry_tenant_id:
                # Skip entries without tenant_id
                continue

            # Generate internal pk
            pk = get_uuid()

            payload = json.dumps(entry["payload"])
            account_id = entry.get("account_id")
            priority = int(entry.get("priority", 2))
            deferred_ts = entry.get("deferred_ts")
            batch_code = entry.get("batch_code")
            # Auto-set is_pec=1 if account is a PEC account
            is_pec = 1 if account_id in pec_accounts else 0

            rowcount = await self.execute(
                """
                INSERT INTO messages (pk, id, tenant_id, account_id, priority, payload, batch_code, deferred_ts, is_pec)
                VALUES (:pk, :id, :tenant_id, :account_id, :priority, :payload, :batch_code, :deferred_ts, :is_pec)
                ON CONFLICT(tenant_id, id) DO UPDATE SET
                    account_id = excluded.account_id,
                    priority = excluded.priority,
                    payload = excluded.payload,
                    batch_code = excluded.batch_code,
                    deferred_ts = excluded.deferred_ts,
                    is_pec = excluded.is_pec,
                    updated_at = CURRENT_TIMESTAMP
                WHERE messages.smtp_ts IS NULL
                """,
                {
                    "pk": pk,
                    "id": msg_id,
                    "tenant_id": entry_tenant_id,
                    "account_id": account_id,
                    "priority": priority,
                    "payload": payload,
                    "batch_code": batch_code,
                    "deferred_ts": deferred_ts,
                    "is_pec": is_pec,
                },
            )

            if rowcount:
                inserted.append({"id": msg_id, "pk": pk})

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
            SELECT m.pk, m.id, m.tenant_id, m.account_id, m.priority, m.payload, m.batch_code, m.deferred_ts, m.is_pec
            FROM messages m
            LEFT JOIN accounts a ON m.account_id = a.id
            LEFT JOIN tenants t ON a.tenant_id = t.id
            WHERE {' AND '.join(conditions)}
            ORDER BY m.priority ASC, m.created_at ASC, m.pk ASC
            LIMIT :limit
        """

        rows = await self.db.adapter.fetch_all(query, params)
        return [self._decode_payload(row) for row in rows]

    async def set_deferred(self, pk: str, deferred_ts: int) -> None:
        """Put message back in queue for retry at deferred_ts.

        Resets smtp_ts to NULL so the message becomes "pending" again.

        Args:
            pk: Internal primary key of the message (UUID string).
            deferred_ts: Timestamp when message can be retried.
        """
        await self.execute(
            """
            UPDATE messages
            SET deferred_ts = :deferred_ts, smtp_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE pk = :pk
            """,
            {"deferred_ts": deferred_ts, "pk": pk},
        )

    async def clear_deferred(self, pk: str) -> None:
        """Clear the deferred timestamp for a message.

        Args:
            pk: Internal primary key of the message (UUID string).
        """
        await self.execute(
            """
            UPDATE messages
            SET deferred_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE pk = :pk
            """,
            {"pk": pk},
        )

    async def mark_sent(self, pk: str, smtp_ts: int) -> None:
        """Mark a message as processed (SMTP attempted successfully).

        Args:
            pk: Internal primary key of the message (UUID string).
            smtp_ts: Timestamp when SMTP send was attempted.
        """
        await self.execute(
            """
            UPDATE messages
            SET smtp_ts = :smtp_ts, deferred_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE pk = :pk
            """,
            {"smtp_ts": smtp_ts, "pk": pk},
        )

    async def mark_error(self, pk: str, smtp_ts: int) -> None:
        """Mark a message as processed (SMTP attempted with error).

        Args:
            pk: Internal primary key of the message (UUID string).
            smtp_ts: Timestamp when SMTP send was attempted.
        """
        await self.execute(
            """
            UPDATE messages
            SET smtp_ts = :smtp_ts, deferred_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE pk = :pk
            """,
            {"smtp_ts": smtp_ts, "pk": pk},
        )

    async def clear_pec_flag(self, pk: str) -> None:
        """Clear the is_pec flag when recipient is not a PEC address.

        Args:
            pk: Internal primary key of the message (UUID string).
        """
        await self.execute(
            """
            UPDATE messages
            SET is_pec = 0, updated_at = CURRENT_TIMESTAMP
            WHERE pk = :pk
            """,
            {"pk": pk},
        )

    async def get_pec_without_acceptance(self, cutoff_ts: int) -> list[dict[str, Any]]:
        """Get PEC messages sent before cutoff_ts without acceptance receipt.

        Returns messages where:
        - is_pec = 1 (marked as PEC)
        - smtp_ts < cutoff_ts (sent before cutoff)
        - No pec_acceptance event exists
        """
        rows = await self.db.adapter.fetch_all(
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

    async def update_payload(self, pk: str, payload: dict[str, Any]) -> None:
        """Update the payload field of a message.

        Args:
            pk: Internal primary key of the message (UUID string).
            payload: New payload data.
        """
        await self.execute(
            """
            UPDATE messages
            SET payload = :payload, updated_at = CURRENT_TIMESTAMP
            WHERE pk = :pk
            """,
            {"payload": json.dumps(payload), "pk": pk},
        )

    async def get(self, msg_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
        """Get a single message by ID. Returns None if not found.

        Args:
            msg_id: Client-provided message ID.
            tenant_id: Optional tenant ID for multi-tenant lookup.
                If not provided, returns first match (use with caution).
        """
        if tenant_id:
            row = await self.db.adapter.fetch_one(
                "SELECT * FROM messages WHERE tenant_id = :tenant_id AND id = :id",
                {"tenant_id": tenant_id, "id": msg_id},
            )
        else:
            row = await self.db.adapter.fetch_one(
                "SELECT * FROM messages WHERE id = :id",
                {"id": msg_id},
            )
        if row is None:
            return None
        return self._decode_payload(row)

    async def get_by_pk(self, pk: str) -> dict[str, Any] | None:
        """Get a single message by internal primary key.

        Args:
            pk: Internal primary key (UUID string).
        """
        row = await self.db.adapter.fetch_one(
            "SELECT * FROM messages WHERE pk = :pk",
            {"pk": pk},
        )
        if row is None:
            return None
        return self._decode_payload(row)

    async def remove(self, msg_id: str, tenant_id: str | None = None) -> bool:
        """Remove a message regardless of its state. Returns True if deleted.

        Args:
            msg_id: Client-provided message ID.
            tenant_id: Optional tenant ID for multi-tenant deletion.
                If not provided, deletes first match (use with caution).
        """
        if tenant_id:
            rowcount = await self.delete(where={"tenant_id": tenant_id, "id": msg_id})
        else:
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
              AND pk IN (
                  SELECT m.pk FROM messages m
                  WHERE m.smtp_ts IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM message_events e
                        WHERE e.message_pk = m.pk AND e.reported_ts IS NULL
                    )
                    AND (
                        SELECT MAX(e.reported_ts) FROM message_events e
                        WHERE e.message_pk = m.pk
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
            WHERE pk IN (
                SELECT m.pk FROM messages m
                JOIN accounts a ON m.account_id = a.id
                WHERE a.tenant_id = :tenant_id
                  AND m.smtp_ts IS NOT NULL
                  AND NOT EXISTS (
                      SELECT 1 FROM message_events e
                      WHERE e.message_pk = m.pk AND e.reported_ts IS NULL
                  )
                  AND (
                      SELECT MAX(e.reported_ts) FROM message_events e
                      WHERE e.message_pk = m.pk
                  ) < :threshold_ts
            )
            """,
            {"threshold_ts": threshold_ts, "tenant_id": tenant_id},
        )

    async def list_all(
        self,
        *,
        tenant_id: str | None = None,
        active_only: bool = False,
        include_history: bool = False,
    ) -> list[dict[str, Any]]:
        """Return messages for inspection purposes, optionally filtered by tenant.

        Args:
            tenant_id: If provided, filter messages to those belonging to this tenant
                (via the account's tenant_id).
            active_only: If True, only return messages pending delivery.
            include_history: If True, include event history for each message.

        Returns:
            List of message dicts including error info from message_events.
            If include_history=True, each message includes a 'history' field with
            the list of events ordered chronologically.
        """
        params: dict[str, Any] = {}
        where_clauses: list[str] = []

        # Subquery to get the latest error event for each message
        error_subquery = """
            SELECT message_pk, event_ts as error_ts, description as error
            FROM message_events
            WHERE event_type = 'error'
            AND id = (
                SELECT MAX(id) FROM message_events e2
                WHERE e2.message_pk = message_events.message_pk
                AND e2.event_type = 'error'
            )
        """

        if tenant_id:
            # Join with accounts to filter by tenant_id and with error events
            query = f"""
                SELECT m.pk, m.id, m.tenant_id, m.account_id, m.priority, m.payload, m.batch_code,
                       m.deferred_ts, m.smtp_ts, m.created_at, m.updated_at, m.is_pec,
                       err.error_ts, err.error
                FROM messages m
                LEFT JOIN accounts a ON m.account_id = a.id
                LEFT JOIN ({error_subquery}) err ON m.pk = err.message_pk
            """
            where_clauses.append("a.tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id
        else:
            query = f"""
                SELECT m.pk, m.id, m.tenant_id, m.account_id, m.priority, m.payload, m.batch_code,
                       m.deferred_ts, m.smtp_ts, m.created_at, m.updated_at, m.is_pec,
                       err.error_ts, err.error
                FROM messages m
                LEFT JOIN ({error_subquery}) err ON m.pk = err.message_pk
            """

        if active_only:
            where_clauses.append("m.smtp_ts IS NULL")

        if where_clauses:
            query += " WHERE " + " AND ".join(where_clauses)

        query += " ORDER BY m.priority ASC, m.created_at ASC, m.id ASC"

        rows = await self.db.adapter.fetch_all(query, params)
        messages = [self._decode_payload(row) for row in rows]

        if include_history and messages:
            messages = await self._add_history_to_messages(messages)

        return messages

    async def _add_history_to_messages(
        self, messages: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Add event history to each message in a single query."""
        message_pks = [m["pk"] for m in messages]
        placeholders = ", ".join(f":pk_{i}" for i in range(len(message_pks)))
        params = {f"pk_{i}": pk for i, pk in enumerate(message_pks)}

        events_query = f"""
            SELECT id as event_id, message_pk, event_type, event_ts,
                   description, metadata, reported_ts
            FROM message_events
            WHERE message_pk IN ({placeholders})
            ORDER BY event_ts ASC, id ASC
        """
        event_rows = await self.db.adapter.fetch_all(events_query, params)

        # Group events by message_pk
        events_by_pk: dict[str, list[dict[str, Any]]] = {m["pk"]: [] for m in messages}
        for row in event_rows:
            event = dict(row)
            if event.get("metadata"):
                try:
                    event["metadata"] = json.loads(event["metadata"])
                except (json.JSONDecodeError, TypeError):
                    event["metadata"] = None
            msg_pk = event.pop("message_pk")
            if msg_pk in events_by_pk:
                events_by_pk[msg_pk].append(event)

        # Add history to each message
        for msg in messages:
            msg["history"] = events_by_pk.get(msg["pk"], [])

        return messages

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
        """Decode payload JSON in message dict and convert is_pec to bool."""
        payload = data.pop("payload", None)
        if payload is not None:
            try:
                data["message"] = json.loads(payload)
            except json.JSONDecodeError:
                data["message"] = {"raw_payload": payload}
        else:
            data["message"] = None
        # Convert is_pec to bool
        if "is_pec" in data:
            data["is_pec"] = bool(data["is_pec"]) if data["is_pec"] else False
        return data


__all__ = ["MessagesTable"]
