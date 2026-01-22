# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SQLite-backed persistence layer for the mail dispatcher.

This module provides the Persistence class that handles all database
operations for the async mail service, including:

- SMTP account management (create, read, update, delete)
- Message queue operations (insert, fetch, update status)
- Send log for rate limiting calculations

The persistence layer uses aiosqlite for async SQLite operations,
supporting both file-based databases and in-memory databases for testing.

Example:
    Basic usage of the persistence layer::

        persistence = Persistence("/data/mail.db")
        await persistence.init_db()

        # Add an SMTP account
        await persistence.add_account({
            "id": "primary",
            "host": "smtp.example.com",
            "port": 465,
            "user": "sender@example.com",
            "password": "secret"
        })

        # Insert messages for delivery
        await persistence.insert_messages([
            {"id": "msg1", "account_id": "primary", "priority": 2, "payload": {...}}
        ])

"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import Any

import aiosqlite


class Persistence:
    """Async SQLite persistence layer for mail service state management.

    Provides all database operations needed by the mail dispatcher including
    account management, message queue operations, and send logging for rate
    limiting.

    The class uses async context managers for database connections to ensure
    proper resource cleanup. Each operation opens and closes its own
    connection, making it safe for concurrent use.

    Attributes:
        db_path: Path to the SQLite database file, or ":memory:" for
            an in-memory database.
    """

    def __init__(self, db_path: str = "/data/mail_service.db"):
        """Initialize the persistence layer with a database path.

        Args:
            db_path: Path to the SQLite database file. Use ":memory:" for
                an in-memory database suitable for testing.
        """
        self.db_path = db_path or ":memory:"

    async def init_db(self) -> None:
        """Initialize the database schema with all required tables.

        Creates or migrates the database schema including tables for:
        - tenants: Multi-tenant configuration
        - accounts: SMTP server configurations
        - messages: Email queue with status tracking
        - send_log: Send history for rate limiting

        This method is idempotent and safely handles schema migrations
        by adding new columns to existing tables when needed.
        """
        async with aiosqlite.connect(self.db_path) as db:
            # Tenants table (new for multi-tenant support)
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    id TEXT PRIMARY KEY,
                    name TEXT,
                    client_auth TEXT,
                    client_base_url TEXT,
                    client_sync_path TEXT,
                    client_attachment_path TEXT,
                    rate_limits TEXT,
                    active INTEGER DEFAULT 1,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    user TEXT,
                    password TEXT,
                    ttl INTEGER DEFAULT 300,
                    limit_per_minute INTEGER,
                    limit_per_hour INTEGER,
                    limit_per_day INTEGER,
                    limit_behavior TEXT,
                    use_tls INTEGER,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
                )
                """
            )
            # Migrations for existing databases
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN use_tls INTEGER")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN batch_size INTEGER")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN tenant_id TEXT")
            except aiosqlite.OperationalError:
                pass
            try:
                await db.execute("ALTER TABLE accounts ADD COLUMN updated_at TEXT")
            except aiosqlite.OperationalError:
                pass

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS send_log (
                    account_id TEXT,
                    timestamp INTEGER
                )
                """
            )

            await db.execute("DROP TABLE IF EXISTS pending_messages")
            await db.execute("DROP TABLE IF EXISTS deferred_messages")
            await db.execute("DROP TABLE IF EXISTS delivery_reports")
            await db.execute("DROP TABLE IF EXISTS queued_messages")

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    account_id TEXT,
                    priority INTEGER NOT NULL DEFAULT 2,
                    payload TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    deferred_ts INTEGER,
                    sent_ts INTEGER,
                    error_ts INTEGER,
                    error TEXT,
                    reported_ts INTEGER
                )
                """
            )

            # Instance configuration table (replaces config.ini)
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS instance_config (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await db.commit()

    # Tenants ------------------------------------------------------------------
    async def add_tenant(self, tenant: dict[str, Any]) -> None:
        """Insert or replace a tenant configuration.

        Args:
            tenant: Dict with keys: id, name, client_auth, client_base_url,
                   client_sync_path, client_attachment_path, rate_limits, active.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO tenants
                (id, name, client_auth, client_base_url, client_sync_path, client_attachment_path, rate_limits, active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    tenant["id"],
                    tenant.get("name"),
                    json.dumps(tenant.get("client_auth")) if tenant.get("client_auth") else None,
                    tenant.get("client_base_url"),
                    tenant.get("client_sync_path"),
                    tenant.get("client_attachment_path"),
                    json.dumps(tenant.get("rate_limits")) if tenant.get("rate_limits") else None,
                    1 if tenant.get("active", True) else 0,
                ),
            )
            await db.commit()

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        """Fetch a tenant configuration by ID.

        Args:
            tenant_id: Unique tenant identifier.

        Returns:
            Tenant dict with decoded JSON fields, or None if not found.
        """
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT * FROM tenants WHERE id=?", (tenant_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            tenant = dict(zip(cols, row, strict=True))

        # Decode JSON fields
        for field in ("client_auth", "rate_limits"):
            if tenant.get(field):
                tenant[field] = json.loads(tenant[field])
        tenant["active"] = bool(tenant.get("active", 1))
        return tenant

    async def list_tenants(self, active_only: bool = False) -> list[dict[str, Any]]:
        """Return all tenants.

        Args:
            active_only: If True, only return active tenants.
        """
        async with aiosqlite.connect(self.db_path) as db:
            query = "SELECT * FROM tenants"
            if active_only:
                query += " WHERE active = 1"
            query += " ORDER BY id"
            async with db.execute(query) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]

        result = []
        for row in rows:
            tenant = dict(zip(cols, row, strict=True))
            for field in ("client_auth", "rate_limits"):
                if tenant.get(field):
                    tenant[field] = json.loads(tenant[field])
            tenant["active"] = bool(tenant.get("active", 1))
            result.append(tenant)
        return result

    async def update_tenant(self, tenant_id: str, updates: dict[str, Any]) -> bool:
        """Update a tenant's fields.

        Args:
            tenant_id: The tenant ID to update.
            updates: Dict of fields to update.

        Returns:
            True if tenant was found and updated, False otherwise.
        """
        if not updates:
            return False

        # Build SET clause dynamically
        set_parts = []
        values = []
        for key, value in updates.items():
            if key in ("client_auth", "rate_limits"):
                set_parts.append(f"{key} = ?")
                values.append(json.dumps(value) if value else None)
            elif key == "active":
                set_parts.append("active = ?")
                values.append(1 if value else 0)
            elif key in ("name", "client_base_url", "client_sync_path", "client_attachment_path"):
                set_parts.append(f"{key} = ?")
                values.append(value)

        if not set_parts:
            return False

        set_parts.append("updated_at = CURRENT_TIMESTAMP")
        values.append(tenant_id)

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                f"UPDATE tenants SET {', '.join(set_parts)} WHERE id = ?",
                tuple(values),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant and all associated accounts/messages.

        Returns:
            True if tenant was deleted, False if not found.
        """
        async with aiosqlite.connect(self.db_path) as db:
            # First delete all accounts belonging to this tenant
            # This will cascade to messages via account deletion
            async with db.execute(
                "SELECT id FROM accounts WHERE tenant_id = ?", (tenant_id,)
            ) as cur:
                account_rows = await cur.fetchall()

            for (account_id,) in account_rows:
                await db.execute("DELETE FROM messages WHERE account_id = ?", (account_id,))
                await db.execute("DELETE FROM send_log WHERE account_id = ?", (account_id,))
                await db.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

            # Delete the tenant
            cursor = await db.execute("DELETE FROM tenants WHERE id = ?", (tenant_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def get_tenant_for_account(self, account_id: str) -> dict[str, Any] | None:
        """Get the tenant configuration for a given account.

        Args:
            account_id: The account ID.

        Returns:
            Tenant dict or None if account has no tenant.
        """
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            """
                SELECT t.* FROM tenants t
                JOIN accounts a ON a.tenant_id = t.id
                WHERE a.id = ?
                """,
            (account_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            cols = [c[0] for c in cur.description]
            tenant = dict(zip(cols, row, strict=True))

        for field in ("client_auth", "rate_limits"):
            if tenant.get(field):
                tenant[field] = json.loads(tenant[field])
        tenant["active"] = bool(tenant.get("active", 1))
        return tenant

    # Accounts -----------------------------------------------------------------
    async def add_account(self, acc: dict[str, Any]) -> None:
        """Insert or overwrite an SMTP account definition."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO accounts
                (id, tenant_id, host, port, user, password, ttl, limit_per_minute, limit_per_hour, limit_per_day, limit_behavior, use_tls, batch_size, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    acc["id"],
                    acc.get("tenant_id"),
                    acc["host"],
                    int(acc["port"]),
                    acc.get("user"),
                    acc.get("password"),
                    int(acc.get("ttl", 300)),
                    acc.get("limit_per_minute"),
                    acc.get("limit_per_hour"),
                    acc.get("limit_per_day"),
                    acc.get("limit_behavior", "defer"),
                    None if acc.get("use_tls") is None else (1 if acc.get("use_tls") else 0),
                    acc.get("batch_size"),
                ),
            )
            await db.commit()

    async def list_accounts(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Return SMTP accounts, optionally filtered by tenant.

        Args:
            tenant_id: If provided, only return accounts for this tenant.
        """
        async with aiosqlite.connect(self.db_path) as db:
            if tenant_id:
                query = """
                    SELECT id, tenant_id, host, port, user, ttl, limit_per_minute, limit_per_hour,
                           limit_per_day, limit_behavior, use_tls, batch_size, created_at, updated_at
                    FROM accounts WHERE tenant_id = ?
                    ORDER BY id
                """
                params = (tenant_id,)
            else:
                query = """
                    SELECT id, tenant_id, host, port, user, ttl, limit_per_minute, limit_per_hour,
                           limit_per_day, limit_behavior, use_tls, batch_size, created_at, updated_at
                    FROM accounts ORDER BY id
                """
                params = ()
            async with db.execute(query, params) as cur:
                rows = await cur.fetchall()
                cols = [c[0] for c in cur.description]
        result = [dict(zip(cols, row, strict=True)) for row in rows]
        for acc in result:
            if "use_tls" in acc:
                acc["use_tls"] = bool(acc["use_tls"]) if acc["use_tls"] is not None else None
        return result

    async def delete_account(self, account_id: str) -> None:
        """Remove a previously stored SMTP account and related state."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM accounts WHERE id=?", (account_id,))
            await db.execute("DELETE FROM messages WHERE account_id=?", (account_id,))
            await db.execute("DELETE FROM send_log WHERE account_id=?", (account_id,))
            await db.commit()

    async def get_account(self, account_id: str) -> dict[str, Any]:
        """Fetch a single SMTP account or raise if it does not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)) as cur:
                row = await cur.fetchone()
                if not row:
                    raise ValueError(f"Account '{account_id}' not found")
                cols = [c[0] for c in cur.description]
                account = dict(zip(cols, row, strict=True))
        if "use_tls" in account:
            account["use_tls"] = bool(account["use_tls"]) if account["use_tls"] is not None else None
        return account

    # Messages -----------------------------------------------------------------
    @staticmethod
    def _decode_message_row(row: tuple[Any, ...], columns: Sequence[str]) -> dict[str, Any]:
        data = dict(zip(columns, row, strict=True))
        payload = data.pop("payload", None)
        if payload is not None:
            try:
                data["message"] = json.loads(payload)
            except json.JSONDecodeError:
                data["message"] = {"raw_payload": payload}
        else:
            data["message"] = None
        return data

    async def insert_messages(self, entries: Sequence[dict[str, Any]]) -> list[str]:
        """Persist a batch of messages for delivery.

        If a message with the same ID already exists but has NOT been sent,
        it will be updated with the new data. Sent messages are never replaced.

        Args:
            entries: List of message dicts with id, account_id, priority, payload.

        Returns:
            List of message IDs that were successfully inserted or updated.
        """
        if not entries:
            return []
        inserted: list[str] = []
        async with aiosqlite.connect(self.db_path) as db:
            for entry in entries:
                msg_id = entry["id"]
                payload = json.dumps(entry["payload"])
                account_id = entry.get("account_id")
                priority = int(entry.get("priority", 2))
                deferred_ts = entry.get("deferred_ts")

                cursor = await db.execute(
                    """
                    INSERT INTO messages (id, account_id, priority, payload, deferred_ts)
                    VALUES (?, ?, ?, ?, ?)
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
                    (msg_id, account_id, priority, payload, deferred_ts),
                )

                # Check if operation succeeded (INSERT or UPDATE)
                if cursor.rowcount:
                    inserted.append(msg_id)
            await db.commit()
        return inserted

    async def fetch_ready_messages(
        self,
        *,
        limit: int,
        now_ts: int,
        priority: int | None = None,
        min_priority: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch messages ready for SMTP delivery.

        Returns messages that are not sent, not deferred past now_ts,
        and not currently being reported, ordered by priority.

        Args:
            limit: Maximum number of messages to return.
            now_ts: Current Unix timestamp for deferred_ts comparison.
            priority: If set, fetch only messages with this exact priority.
            min_priority: If set, fetch only messages with priority >= this value.

        Returns:
            List of message dicts with id, account_id, priority, payload, message.
        """
        conditions = [
            "sent_ts IS NULL",
            "error_ts IS NULL",
            "(deferred_ts IS NULL OR deferred_ts <= ?)",
        ]
        params: list[int] = [now_ts]

        if priority is not None:
            conditions.append("priority = ?")
            params.append(priority)
        elif min_priority is not None:
            conditions.append("priority >= ?")
            params.append(min_priority)

        params.append(limit)

        query = f"""
            SELECT id, account_id, priority, payload, deferred_ts
            FROM messages
            WHERE {' AND '.join(conditions)}
            ORDER BY priority ASC, created_at ASC, id ASC
            LIMIT ?
        """

        async with aiosqlite.connect(self.db_path) as db, db.execute(query, params) as cur:
            rows = await cur.fetchall()
            cols = [c[0] for c in cur.description]
        return [self._decode_message_row(row, cols) for row in rows]

    async def set_deferred(self, msg_id: str, deferred_ts: int) -> None:
        """Update the deferred timestamp for a message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET deferred_ts=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=? AND sent_ts IS NULL AND error_ts IS NULL
                """,
                (deferred_ts, msg_id),
            )
            await db.commit()

    async def clear_deferred(self, msg_id: str) -> None:
        """Clear the deferred timestamp for a message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET deferred_ts=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (msg_id,),
            )
            await db.commit()

    async def mark_sent(self, msg_id: str, sent_ts: int) -> None:
        """Mark a message as sent.

        Resets reported_ts so the message will be reported with final state.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET sent_ts=?, error_ts=NULL, error=NULL, deferred_ts=NULL, reported_ts=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (sent_ts, msg_id),
            )
            await db.commit()

    async def mark_error(self, msg_id: str, error_ts: int, error: str) -> None:
        """Mark a message as failed.

        Resets reported_ts and deferred_ts so the message will be reported with final error state.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET error_ts=?, error=?, sent_ts=NULL, deferred_ts=NULL, reported_ts=NULL, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (error_ts, error, msg_id),
            )
            await db.commit()

    async def update_message_payload(self, msg_id: str, payload: dict[str, Any]) -> None:
        """Update the payload field of a message (used for retry count tracking)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE messages
                SET payload=?, updated_at=CURRENT_TIMESTAMP
                WHERE id=?
                """,
                (json.dumps(payload), msg_id),
            )
            await db.commit()

    async def delete_message(self, msg_id: str) -> bool:
        """Remove a message regardless of its state."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM messages WHERE id=?", (msg_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def purge_messages_for_account(self, account_id: str) -> None:
        """Delete every message linked to the given account."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM messages WHERE account_id=?", (account_id,))
            await db.commit()

    async def existing_message_ids(self, ids: Iterable[str]) -> set[str]:
        """Return the subset of ids that already exist in storage."""
        id_list = [mid for mid in ids if mid]
        if not id_list:
            return set()
        placeholders = ",".join("?" for _ in id_list)
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            f"SELECT id FROM messages WHERE id IN ({placeholders})",
            id_list,
        ) as cur:
            rows = await cur.fetchall()
        return {row[0] for row in rows}

    async def fetch_reports(self, limit: int) -> list[dict[str, Any]]:
        """Return messages that need to be reported back to the client.

        Only returns messages in final states (sent or error).
        Messages with only deferred_ts are not reported (internal retry logic).
        Includes tenant_id from the associated account for per-tenant routing.
        """
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            """
                SELECT m.id, m.account_id, m.priority, m.payload, m.sent_ts, m.error_ts,
                       m.error, m.deferred_ts, a.tenant_id
                FROM messages m
                LEFT JOIN accounts a ON m.account_id = a.id
                WHERE m.reported_ts IS NULL
                  AND (m.sent_ts IS NOT NULL OR m.error_ts IS NOT NULL)
                ORDER BY m.updated_at ASC, m.id ASC
                LIMIT ?
                """,
            (limit,),
        ) as cur:
            rows = await cur.fetchall()
            cols = [c[0] for c in cur.description]
        return [self._decode_message_row(row, cols) for row in rows]

    async def mark_reported(self, message_ids: Iterable[str], reported_ts: int) -> None:
        """Set the reported timestamp for the provided messages."""
        ids = [mid for mid in message_ids if mid]
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"""
                UPDATE messages
                SET reported_ts=?, updated_at=CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                (reported_ts, *ids),
            )
            await db.commit()

    async def remove_reported_before(self, threshold_ts: int) -> int:
        """Delete reported messages older than ``threshold_ts``.

        Only deletes messages in final states (sent or error).
        Messages with only deferred_ts are kept in queue until they reach a final state.
        """
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                DELETE FROM messages
                WHERE reported_ts IS NOT NULL
                  AND reported_ts < ?
                  AND (sent_ts IS NOT NULL OR error_ts IS NOT NULL)
                """,
                (threshold_ts,),
            )
            await db.commit()
            return cursor.rowcount

    async def list_messages(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Return messages for inspection purposes."""
        query = """
            SELECT id, account_id, priority, payload, deferred_ts, sent_ts, error_ts,
                   error, reported_ts, created_at, updated_at
            FROM messages
        """
        params: tuple[Any, ...] = ()
        if active_only:
            query += " WHERE sent_ts IS NULL AND error_ts IS NULL"
        query += " ORDER BY priority ASC, created_at ASC, id ASC"
        async with aiosqlite.connect(self.db_path) as db, db.execute(query, params) as cur:
            rows = await cur.fetchall()
            cols = [c[0] for c in cur.description]
        return [self._decode_message_row(row, cols) for row in rows]

    async def count_active_messages(self) -> int:
        """Return the number of messages still awaiting delivery."""
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            """
                SELECT COUNT(*) FROM messages
                WHERE sent_ts IS NULL AND error_ts IS NULL
                """
        ) as cur:
            row = await cur.fetchone()
        return int(row[0] if row else 0)

    # Send log -----------------------------------------------------------------
    async def log_send(self, account_id: str, timestamp: int) -> None:
        """Record a delivery event for rate limiting purposes."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("INSERT INTO send_log (account_id, timestamp) VALUES (?, ?)", (account_id, timestamp))
            await db.commit()

    async def count_sends_since(self, account_id: str, since_ts: int) -> int:
        """Count messages sent after ``since_ts`` for the given account."""
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT COUNT(*) FROM send_log WHERE account_id=? AND timestamp > ?",
            (account_id, since_ts),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0] if row else 0)

    # Instance config ------------------------------------------------------
    async def get_config(self, key: str, default: str | None = None) -> str | None:
        """Get a configuration value by key.

        Args:
            key: The configuration key to retrieve.
            default: Default value if key not found.

        Returns:
            The configuration value or default if not found.
        """
        async with aiosqlite.connect(self.db_path) as db, db.execute(
            "SELECT value FROM instance_config WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else default

    async def set_config(self, key: str, value: str) -> None:
        """Set a configuration value.

        Args:
            key: The configuration key.
            value: The value to set.
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO instance_config (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (key, value),
            )
            await db.commit()

    async def get_all_config(self) -> dict[str, str]:
        """Get all configuration values.

        Returns:
            Dict mapping keys to values.
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT key, value FROM instance_config") as cur:
                rows = await cur.fetchall()
        return {row[0]: row[1] for row in rows}
