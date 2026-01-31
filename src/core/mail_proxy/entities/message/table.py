# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Email message queue table manager.

This module provides the MessagesTable class for managing email messages
in the delivery queue. Each message contains a JSON payload with email
content and is associated with a tenant and SMTP account.

Messages progress through states:
    - Pending: smtp_ts IS NULL, deferred_ts IS NULL
    - Deferred: smtp_ts IS NULL, deferred_ts IS NOT NULL
    - Processed: smtp_ts IS NOT NULL

The table uses a UUID primary key (pk) with a unique constraint on
(tenant_id, id) for multi-tenant isolation and idempotent inserts.

Example:
    Queue and send messages::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        messages = proxy.db.table("messages")

        # Queue a message batch
        result = await messages.insert_batch([{
            "id": "msg-001",
            "tenant_id": "acme",
            "account_id": "main",
            "payload": {
                "from": "sender@acme.com",
                "to": ["user@example.com"],
                "subject": "Hello",
                "body": "Test message",
            },
        }])

        # Fetch messages ready for delivery
        import time
        ready = await messages.fetch_ready(limit=10, now_ts=int(time.time()))

        # Mark as sent
        for msg in ready:
            await messages.mark_sent(msg["pk"], int(time.time()))

Note:
    Enterprise Edition (EE) extends this class with MessagesTable_EE
    mixin, adding PEC-specific methods for Italian certified email.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

from genro_toolbox import get_uuid

from sql import Integer, String, Table, Timestamp


class MessagesTable(Table):
    """Email message queue with scheduling and deferred delivery.

    Manages the email delivery queue with support for priority ordering,
    deferred delivery (retry scheduling), batch operations, and
    multi-tenant isolation.

    Attributes:
        name: Table name ("messages").
        pkey: Primary key column ("pk", UUID string).

    Table Schema:
        - pk: UUID primary key (generated on insert)
        - id: Client-provided message identifier (unique per tenant)
        - tenant_id: Tenant identifier for isolation
        - account_id: Legacy account reference (business key)
        - account_pk: FK to accounts.pk (UUID)
        - priority: Delivery priority (0=immediate, 1=high, 2=medium, 3=low)
        - payload: JSON email content (from, to, subject, body, etc.)
        - batch_code: Optional campaign/batch identifier
        - deferred_ts: Retry timestamp (NULL = ready for delivery)
        - smtp_ts: SMTP attempt timestamp (NULL = pending)
        - is_pec: PEC flag for Italian certified email (EE)
        - created_at, updated_at: Timestamps

    Example:
        Basic message queue operations::

            messages = proxy.db.table("messages")

            # Insert messages
            await messages.insert_batch([
                {"id": "m1", "tenant_id": "t1", "account_id": "a1",
                 "payload": {"from": "a@b.com", "to": ["c@d.com"], ...}},
            ])

            # Fetch ready messages
            ready = await messages.fetch_ready(limit=100, now_ts=now)

            # Mark sent/error
            await messages.mark_sent(pk, now)
            await messages.mark_error(pk, now)
    """

    name = "messages"
    pkey = "pk"

    def create_table_sql(self) -> str:
        """Generate CREATE TABLE with UNIQUE constraint.

        Adds UNIQUE (tenant_id, id) constraint to ensure idempotent
        inserts and multi-tenant isolation.

        Returns:
            SQL CREATE TABLE statement with UNIQUE constraint.
        """
        sql = super().create_table_sql()
        last_paren = sql.rfind(")")
        return sql[:last_paren] + ',\n    UNIQUE ("tenant_id", "id")\n)'

    def configure(self) -> None:
        """Define table columns.

        Columns:
            pk: UUID primary key (generated via get_uuid()).
            id: Client message identifier (unique per tenant).
            tenant_id: Owning tenant (denormalized for query efficiency).
            account_id: Legacy account reference (business key).
            account_pk: FK to accounts.pk UUID.
            priority: Delivery priority (0-3, default 2).
            payload: JSON email content.
            batch_code: Optional batch/campaign identifier.
            created_at, updated_at: Timestamps.
            deferred_ts: Unix timestamp for retry scheduling.
            smtp_ts: Unix timestamp when SMTP was attempted.
            is_pec: PEC flag (1=awaiting receipts, EE only).
        """
        c = self.columns
        c.column("pk", String)
        c.column("id", String, nullable=False)
        c.column("tenant_id", String, nullable=False)
        c.column("account_id", String)
        c.column("account_pk", String)
        c.column("priority", Integer, nullable=False, default=2)
        c.column("payload", String, nullable=False)
        c.column("batch_code", String)
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("deferred_ts", Integer)
        c.column("smtp_ts", Integer)
        c.column("is_pec", Integer, default=0)

    async def migrate_from_legacy_schema(self) -> bool:
        """Migrate from INTEGER pk to UUID pk schema.

        Legacy databases used INTEGER autoincrement primary key.
        This migration adds UUID 'pk' column as new primary key.

        Returns:
            True if migration performed, False if not needed.
        """
        try:
            await self.db.adapter.fetch_one("SELECT pk FROM messages LIMIT 1")
            return False
        except Exception:
            pass

        try:
            await self.db.adapter.fetch_one("SELECT id FROM messages LIMIT 1")
        except Exception:
            return False

        await self.db.adapter.execute("""
            CREATE TABLE messages_new (
                pk TEXT PRIMARY KEY,
                id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                account_id TEXT,
                priority INTEGER NOT NULL DEFAULT 2,
                payload TEXT NOT NULL,
                batch_code TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                deferred_ts INTEGER,
                smtp_ts INTEGER,
                is_pec INTEGER DEFAULT 0,
                UNIQUE (tenant_id, id)
            )
        """)

        rows = await self.db.adapter.fetch_all(
            "SELECT id, tenant_id, account_id, priority, payload, batch_code, "
            "created_at, updated_at, deferred_ts, smtp_ts, is_pec FROM messages"
        )
        for row in rows:
            pk = get_uuid()
            await self.db.adapter.execute(
                """INSERT INTO messages_new
                   (pk, id, tenant_id, account_id, priority, payload, batch_code,
                    created_at, updated_at, deferred_ts, smtp_ts, is_pec)
                   VALUES (:pk, :id, :tenant_id, :account_id, :priority, :payload,
                           :batch_code, :created_at, :updated_at, :deferred_ts,
                           :smtp_ts, :is_pec)""",
                {"pk": pk, **dict(row)},
            )

        await self.db.adapter.execute("DROP TABLE messages")
        await self.db.adapter.execute("ALTER TABLE messages_new RENAME TO messages")

        return True

    async def migrate_account_pk(self) -> bool:
        """Populate account_pk from account_id + tenant_id.

        Links messages to accounts via UUID instead of business key.

        Returns:
            True if migration performed, False if not needed.
        """
        try:
            await self.db.adapter.fetch_one("SELECT account_pk FROM messages LIMIT 1")
        except Exception:
            return False

        row = await self.db.adapter.fetch_one(
            """SELECT COUNT(*) as cnt FROM messages
               WHERE account_id IS NOT NULL AND account_pk IS NULL"""
        )
        if not row or row["cnt"] == 0:
            return False

        await self.db.adapter.execute(
            """UPDATE messages
               SET account_pk = (
                   SELECT a.pk FROM accounts a
                   WHERE a.tenant_id = messages.tenant_id
                     AND a.id = messages.account_id
               )
               WHERE account_id IS NOT NULL AND account_pk IS NULL"""
        )

        return True

    async def insert_batch(
        self,
        entries: Sequence[dict[str, Any]],
        pec_account_ids: set[str] | None = None,
        tenant_id: str | None = None,
        auto_pec: bool = True,
    ) -> list[dict[str, str]]:
        """Persist a batch of messages for delivery.

        Performs upsert based on (tenant_id, id):
            - New message: insert with generated UUID pk
            - Existing pending: update fields
            - Existing processed: skip (already sent)

        Args:
            entries: List of message dicts. Each must have:
                - id: Client message identifier
                - tenant_id or use tenant_id param
                - account_id: SMTP account identifier
                - payload: Email content dict
                Optional: priority, deferred_ts, batch_code, account_pk.
            pec_account_ids: Set of account IDs that are PEC accounts.
                If None and auto_pec=True, fetched from accounts table.
            tenant_id: Default tenant ID if not in each entry.
            auto_pec: If True, auto-fetch PEC account IDs.

        Returns:
            List of {"id": msg_id, "pk": pk} for inserted/updated messages.

        Example:
            ::

                result = await messages.insert_batch([
                    {
                        "id": "msg-001",
                        "tenant_id": "acme",
                        "account_id": "main",
                        "payload": {
                            "from": "sender@acme.com",
                            "to": ["user@example.com"],
                            "subject": "Test",
                            "body": "Hello",
                        },
                    },
                ])
                # Returns: [{"id": "msg-001", "pk": "uuid-..."}]
        """
        if not entries:
            return []

        if pec_account_ids is None and auto_pec:
            pec_account_ids = await self.db.table("accounts").get_pec_account_ids()

        pec_accounts = pec_account_ids or set()
        result: list[dict[str, str]] = []

        for entry in entries:
            msg_id = entry["id"]
            entry_tenant_id = entry.get("tenant_id") or tenant_id
            if not entry_tenant_id:
                continue

            account_id = entry.get("account_id")
            account_pk = entry.get("account_pk")
            priority = int(entry.get("priority", 2))
            deferred_ts = entry.get("deferred_ts")
            batch_code = entry.get("batch_code")
            is_pec = 1 if account_id in pec_accounts else 0
            payload = json.dumps(entry["payload"])

            if account_id and not account_pk:
                acc_row = await self.db.adapter.fetch_one(
                    "SELECT pk FROM accounts WHERE tenant_id = :tenant_id AND id = :account_id",
                    {"tenant_id": entry_tenant_id, "account_id": account_id},
                )
                if acc_row:
                    account_pk = acc_row["pk"]

            existing = await self.db.adapter.fetch_one(
                "SELECT pk, smtp_ts FROM messages WHERE tenant_id = :tenant_id AND id = :id",
                {"tenant_id": entry_tenant_id, "id": msg_id},
            )

            if existing:
                if existing["smtp_ts"] is not None:
                    continue

                pk = existing["pk"]
                async with self.record(pk) as rec:
                    rec["account_id"] = account_id
                    rec["account_pk"] = account_pk
                    rec["priority"] = priority
                    rec["payload"] = payload
                    rec["batch_code"] = batch_code
                    rec["deferred_ts"] = deferred_ts
                    rec["is_pec"] = is_pec
            else:
                pk = get_uuid()
                await self.insert(
                    {
                        "pk": pk,
                        "id": msg_id,
                        "tenant_id": entry_tenant_id,
                        "account_id": account_id,
                        "account_pk": account_pk,
                        "priority": priority,
                        "payload": payload,
                        "batch_code": batch_code,
                        "deferred_ts": deferred_ts,
                        "is_pec": is_pec,
                    }
                )

            result.append({"id": msg_id, "pk": pk})

        return result

    async def fetch_ready(
        self,
        *,
        limit: int,
        now_ts: int,
        priority: int | None = None,
        min_priority: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch messages ready for SMTP delivery.

        Returns pending messages ordered by priority and creation time.
        Excludes messages from suspended tenants/batches.

        Args:
            limit: Maximum messages to fetch.
            now_ts: Current Unix timestamp for deferred check.
            priority: Exact priority to filter (0-3).
            min_priority: Minimum priority to filter.

        Returns:
            List of message dicts with decoded payload.

        Note:
            Suspension logic:
                - tenant.suspended_batches = "*": all messages skipped
                - tenant.suspended_batches contains batch_code: skipped
                - Messages without batch_code: only skipped when "*"
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
            LEFT JOIN accounts a ON m.account_pk = a.pk
            LEFT JOIN tenants t ON m.tenant_id = t.id
            WHERE {" AND ".join(conditions)}
            ORDER BY m.priority ASC, m.created_at ASC, m.pk ASC
            LIMIT :limit
        """

        rows = await self.db.adapter.fetch_all(query, params)
        return [self._decode_payload(row) for row in rows]

    async def set_deferred(self, pk: str, deferred_ts: int) -> None:
        """Schedule message for retry at specified timestamp.

        Resets smtp_ts to NULL so message becomes pending again.

        Args:
            pk: Message UUID primary key.
            deferred_ts: Unix timestamp for retry.
        """
        async with self.record(pk) as rec:
            rec["deferred_ts"] = deferred_ts
            rec["smtp_ts"] = None

    async def clear_deferred(self, pk: str) -> None:
        """Clear deferred timestamp, making message immediately ready.

        Args:
            pk: Message UUID primary key.
        """
        async with self.record(pk) as rec:
            rec["deferred_ts"] = None

    async def mark_sent(self, pk: str, smtp_ts: int) -> None:
        """Mark message as successfully sent.

        Args:
            pk: Message UUID primary key.
            smtp_ts: Unix timestamp of successful SMTP send.
        """
        async with self.record(pk) as rec:
            rec["smtp_ts"] = smtp_ts
            rec["deferred_ts"] = None

    async def mark_error(self, pk: str, smtp_ts: int) -> None:
        """Mark message as sent with error.

        Args:
            pk: Message UUID primary key.
            smtp_ts: Unix timestamp of failed SMTP attempt.
        """
        async with self.record(pk) as rec:
            rec["smtp_ts"] = smtp_ts
            rec["deferred_ts"] = None

    async def update_payload(self, pk: str, payload: dict[str, Any]) -> None:
        """Update message payload.

        Args:
            pk: Message UUID primary key.
            payload: New email content dict.
        """
        async with self.record(pk) as rec:
            rec["payload"] = json.dumps(payload)

    async def get(self, msg_id: str, tenant_id: str) -> dict[str, Any] | None:
        """Get message by client ID and tenant.

        Args:
            msg_id: Client-provided message ID.
            tenant_id: Tenant identifier.

        Returns:
            Message dict with decoded payload, or None if not found.
        """
        row = await self.db.adapter.fetch_one(
            "SELECT * FROM messages WHERE tenant_id = :tenant_id AND id = :id",
            {"tenant_id": tenant_id, "id": msg_id},
        )
        if row is None:
            return None
        return self._decode_payload(row)

    async def get_by_pk(self, pk: str) -> dict[str, Any] | None:
        """Get message by internal primary key.

        Args:
            pk: Message UUID primary key.

        Returns:
            Message dict with decoded payload, or None if not found.
        """
        row = await self.db.adapter.fetch_one(
            "SELECT * FROM messages WHERE pk = :pk",
            {"pk": pk},
        )
        if row is None:
            return None
        return self._decode_payload(row)

    async def remove_by_pk(self, pk: str) -> bool:
        """Delete message by primary key.

        Args:
            pk: Message UUID primary key.

        Returns:
            True if deleted, False if not found.
        """
        rowcount = await self.delete(where={"pk": pk})
        return rowcount > 0

    async def purge_for_account(self, account_id: str) -> None:
        """Delete all messages for an account.

        Args:
            account_id: Account identifier.
        """
        await self.delete(where={"account_id": account_id})

    async def existing_ids(self, ids: Iterable[str]) -> set[str]:
        """Check which message IDs already exist.

        Args:
            ids: Iterable of message IDs to check.

        Returns:
            Set of IDs that exist in the messages table.
        """
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
        """Get message IDs that belong to a tenant.

        Validates ownership by checking tenant_id in accounts table.

        Args:
            ids: List of message IDs to check.
            tenant_id: Tenant identifier.

        Returns:
            Set of message IDs owned by the tenant.
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
            JOIN accounts a ON m.account_pk = a.pk
            WHERE m.id IN ({placeholders})
              AND a.tenant_id = :tenant_id
            """,
            params,
        )
        return {row["id"] for row in rows}

    async def remove_fully_reported_before(self, threshold_ts: int) -> int:
        """Delete messages whose events are all reported before threshold.

        A message can be removed when:
            - It has been processed (smtp_ts IS NOT NULL)
            - All its events have been reported
            - Most recent reported_ts is older than threshold

        Args:
            threshold_ts: Unix timestamp threshold.

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
        """Delete fully reported messages for a tenant.

        Args:
            threshold_ts: Unix timestamp threshold.
            tenant_id: Tenant identifier.

        Returns:
            Number of deleted messages.
        """
        return await self.execute(
            """
            DELETE FROM messages
            WHERE pk IN (
                SELECT m.pk FROM messages m
                JOIN accounts a ON m.account_pk = a.pk
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
        """List messages with optional filters.

        Args:
            tenant_id: Filter by tenant.
            active_only: Only return pending messages (smtp_ts IS NULL).
            include_history: Include event history for each message.

        Returns:
            List of message dicts with decoded payload and optional history.
        """
        params: dict[str, Any] = {}
        where_clauses: list[str] = []

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
            query = f"""
                SELECT m.pk, m.id, m.tenant_id, m.account_id, m.priority, m.payload, m.batch_code,
                       m.deferred_ts, m.smtp_ts, m.created_at, m.updated_at, m.is_pec,
                       t.name as tenant_name,
                       err.error_ts, err.error
                FROM messages m
                LEFT JOIN accounts a ON m.account_pk = a.pk
                LEFT JOIN tenants t ON m.tenant_id = t.id
                LEFT JOIN ({error_subquery}) err ON m.pk = err.message_pk
            """
            where_clauses.append("m.tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id
        else:
            query = f"""
                SELECT m.pk, m.id, m.tenant_id, m.account_id, m.priority, m.payload, m.batch_code,
                       m.deferred_ts, m.smtp_ts, m.created_at, m.updated_at, m.is_pec,
                       t.name as tenant_name,
                       err.error_ts, err.error
                FROM messages m
                LEFT JOIN tenants t ON m.tenant_id = t.id
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
        """Add event history to each message in a single query.

        Args:
            messages: List of message dicts.

        Returns:
            Messages with 'history' field containing event list.
        """
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

        for msg in messages:
            msg["history"] = events_by_pk.get(msg["pk"], [])

        return messages

    async def count_active(self) -> int:
        """Count messages awaiting delivery.

        Returns:
            Number of messages with smtp_ts IS NULL.
        """
        row = await self.db.adapter.fetch_one(
            "SELECT COUNT(*) as cnt FROM messages WHERE smtp_ts IS NULL"
        )
        return int(row["cnt"]) if row else 0

    async def count_pending_for_tenant(self, tenant_id: str, batch_code: str | None = None) -> int:
        """Count pending messages for a tenant.

        Args:
            tenant_id: Tenant identifier.
            batch_code: Optional batch code filter.

        Returns:
            Number of pending messages.
        """
        params: dict[str, Any] = {"tenant_id": tenant_id}

        if batch_code is not None:
            query = """
                SELECT COUNT(*) as cnt
                FROM messages m
                JOIN accounts a ON m.account_pk = a.pk
                WHERE a.tenant_id = :tenant_id
                  AND m.batch_code = :batch_code
                  AND m.smtp_ts IS NULL
            """
            params["batch_code"] = batch_code
        else:
            query = """
                SELECT COUNT(*) as cnt
                FROM messages m
                JOIN accounts a ON m.account_pk = a.pk
                WHERE a.tenant_id = :tenant_id
                  AND m.smtp_ts IS NULL
            """

        row = await self.db.adapter.fetch_one(query, params)
        return int(row["cnt"]) if row else 0

    def _decode_payload(self, data: dict[str, Any]) -> dict[str, Any]:
        """Decode payload JSON and convert is_pec to bool.

        Args:
            data: Raw database row dict.

        Returns:
            Dict with 'message' field containing parsed payload.
        """
        payload = data.pop("payload", None)
        if payload is not None:
            try:
                data["message"] = json.loads(payload)
            except json.JSONDecodeError:
                data["message"] = {"raw_payload": payload}
        else:
            data["message"] = None
        if "is_pec" in data:
            data["is_pec"] = bool(data["is_pec"]) if data["is_pec"] else False
        return data


__all__ = ["MessagesTable"]
