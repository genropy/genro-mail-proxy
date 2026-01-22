# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Database persistence layer for the mail dispatcher.

This module provides the Persistence class that handles all database
operations for the async mail service, including:

- SMTP account management (create, read, update, delete)
- Message queue operations (insert, fetch, update status)
- Send log for rate limiting calculations

Supports multiple database backends via the sql/ adapter pattern:
- SQLite (default): "sqlite:/path/to/db" or just "/path/to/db"
- PostgreSQL: "postgresql://user:pass@host/db"

Example:
    Basic usage with SQLite (backward compatible)::

        persistence = Persistence("/data/mail.db")
        await persistence.init_db()

    Using PostgreSQL::

        persistence = Persistence("postgresql://user:pass@localhost/maildb")
        await persistence.init_db()
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING, Any

from .sql import DbAdapter, create_adapter

if TYPE_CHECKING:
    pass


class Persistence:
    """Async persistence layer for mail service state management.

    Supports SQLite and PostgreSQL via adapter pattern.
    All queries use :name placeholders (supported by both databases).

    Attributes:
        adapter: The database adapter instance.
        db_path: Original connection string (for backward compatibility).
    """

    def __init__(self, connection_string: str = "/data/mail_service.db"):
        """Initialize the persistence layer.

        Args:
            connection_string: Database connection string. Formats:
                - "/path/to/db.sqlite" - SQLite file (backward compatible)
                - ":memory:" - SQLite in-memory
                - "sqlite:/path/to/db" - SQLite explicit
                - "postgresql://user:pass@host/db" - PostgreSQL
        """
        self.db_path = connection_string  # Backward compatibility
        self.adapter: DbAdapter = create_adapter(connection_string)

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
        await self.adapter.connect()

        # Create tables
        await self.adapter.execute("""
            CREATE TABLE IF NOT EXISTS tenants (
                id TEXT PRIMARY KEY,
                name TEXT,
                client_auth TEXT,
                client_base_url TEXT,
                client_sync_path TEXT,
                client_attachment_path TEXT,
                rate_limits TEXT,
                large_file_config TEXT,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await self.adapter.execute("""
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
                batch_size INTEGER,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tenant_id) REFERENCES tenants(id) ON DELETE CASCADE
            )
        """)

        await self.adapter.execute("""
            CREATE TABLE IF NOT EXISTS send_log (
                account_id TEXT,
                timestamp INTEGER
            )
        """)

        await self.adapter.execute("""
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
        """)

        await self.adapter.execute("""
            CREATE TABLE IF NOT EXISTS instance_config (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Migrations for existing databases (SQLite-safe: ignores if column exists)
        for col, type_ in [
            ("use_tls", "INTEGER"),
            ("batch_size", "INTEGER"),
            ("tenant_id", "TEXT"),
            ("updated_at", "TEXT"),
        ]:
            try:
                await self.adapter.execute(f"ALTER TABLE accounts ADD COLUMN {col} {type_}")
            except Exception:
                pass  # Column already exists

        # Migration for tenants table
        try:
            await self.adapter.execute("ALTER TABLE tenants ADD COLUMN large_file_config TEXT")
        except Exception:
            pass  # Column already exists

    # -------------------------------------------------------------------------
    # Tenants
    # -------------------------------------------------------------------------
    async def add_tenant(self, tenant: dict[str, Any]) -> None:
        """Insert or update a tenant configuration."""
        await self.adapter.upsert(
            "tenants",
            {
                "id": tenant["id"],
                "name": tenant.get("name"),
                "client_auth": json.dumps(tenant.get("client_auth")) if tenant.get("client_auth") else None,
                "client_base_url": tenant.get("client_base_url"),
                "client_sync_path": tenant.get("client_sync_path"),
                "client_attachment_path": tenant.get("client_attachment_path"),
                "rate_limits": json.dumps(tenant.get("rate_limits")) if tenant.get("rate_limits") else None,
                "large_file_config": json.dumps(tenant.get("large_file_config")) if tenant.get("large_file_config") else None,
                "active": 1 if tenant.get("active", True) else 0,
            },
            conflict_columns=["id"],
            update_extras=["updated_at = CURRENT_TIMESTAMP"],
        )

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        """Fetch a tenant configuration by ID."""
        tenant = await self.adapter.fetch_one(
            "SELECT * FROM tenants WHERE id = :tenant_id",
            {"tenant_id": tenant_id},
        )
        if not tenant:
            return None
        return self._decode_tenant(tenant)

    async def list_tenants(self, active_only: bool = False) -> list[dict[str, Any]]:
        """Return all tenants."""
        query = "SELECT * FROM tenants"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY id"
        rows = await self.adapter.fetch_all(query)
        return [self._decode_tenant(row) for row in rows]

    async def update_tenant(self, tenant_id: str, updates: dict[str, Any]) -> bool:
        """Update a tenant's fields."""
        if not updates:
            return False

        set_parts = []
        params: dict[str, Any] = {"tenant_id": tenant_id}
        for key, value in updates.items():
            if key in ("client_auth", "rate_limits", "large_file_config"):
                set_parts.append(f"{key} = :{key}")
                params[key] = json.dumps(value) if value else None
            elif key == "active":
                set_parts.append("active = :active")
                params["active"] = 1 if value else 0
            elif key in ("name", "client_base_url", "client_sync_path", "client_attachment_path"):
                set_parts.append(f"{key} = :{key}")
                params[key] = value

        if not set_parts:
            return False

        set_parts.append("updated_at = CURRENT_TIMESTAMP")

        rowcount = await self.adapter.execute(
            f"UPDATE tenants SET {', '.join(set_parts)} WHERE id = :tenant_id",
            params,
        )
        return rowcount > 0

    async def delete_tenant(self, tenant_id: str) -> bool:
        """Delete a tenant and all associated accounts/messages."""
        # Get accounts for this tenant
        accounts = await self.adapter.fetch_all(
            "SELECT id FROM accounts WHERE tenant_id = :tenant_id",
            {"tenant_id": tenant_id},
        )

        # Delete related data
        for acc in accounts:
            account_id = acc["id"]
            await self.adapter.execute(
                "DELETE FROM messages WHERE account_id = :account_id",
                {"account_id": account_id},
            )
            await self.adapter.execute(
                "DELETE FROM send_log WHERE account_id = :account_id",
                {"account_id": account_id},
            )
            await self.adapter.execute(
                "DELETE FROM accounts WHERE id = :account_id",
                {"account_id": account_id},
            )

        rowcount = await self.adapter.execute(
            "DELETE FROM tenants WHERE id = :tenant_id",
            {"tenant_id": tenant_id},
        )
        return rowcount > 0

    async def get_tenant_for_account(self, account_id: str) -> dict[str, Any] | None:
        """Get the tenant configuration for a given account."""
        tenant = await self.adapter.fetch_one(
            """
            SELECT t.* FROM tenants t
            JOIN accounts a ON a.tenant_id = t.id
            WHERE a.id = :account_id
            """,
            {"account_id": account_id},
        )
        if not tenant:
            return None
        return self._decode_tenant(tenant)

    def _decode_tenant(self, tenant: dict[str, Any]) -> dict[str, Any]:
        """Decode JSON fields in tenant dict."""
        for field in ("client_auth", "rate_limits", "large_file_config"):
            if tenant.get(field):
                tenant[field] = json.loads(tenant[field])
        tenant["active"] = bool(tenant.get("active", 1))
        return tenant

    # -------------------------------------------------------------------------
    # Accounts
    # -------------------------------------------------------------------------
    async def add_account(self, acc: dict[str, Any]) -> None:
        """Insert or update an SMTP account definition."""
        await self.adapter.upsert(
            "accounts",
            {
                "id": acc["id"],
                "tenant_id": acc.get("tenant_id"),
                "host": acc["host"],
                "port": int(acc["port"]),
                "user": acc.get("user"),
                "password": acc.get("password"),
                "ttl": int(acc.get("ttl", 300)),
                "limit_per_minute": acc.get("limit_per_minute"),
                "limit_per_hour": acc.get("limit_per_hour"),
                "limit_per_day": acc.get("limit_per_day"),
                "limit_behavior": acc.get("limit_behavior", "defer"),
                "use_tls": None if acc.get("use_tls") is None else (1 if acc.get("use_tls") else 0),
                "batch_size": acc.get("batch_size"),
            },
            conflict_columns=["id"],
            update_extras=["updated_at = CURRENT_TIMESTAMP"],
        )

    async def list_accounts(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Return SMTP accounts, optionally filtered by tenant."""
        if tenant_id:
            query = """
                SELECT id, tenant_id, host, port, user, ttl, limit_per_minute, limit_per_hour,
                       limit_per_day, limit_behavior, use_tls, batch_size, created_at, updated_at
                FROM accounts WHERE tenant_id = :tenant_id
                ORDER BY id
            """
            rows = await self.adapter.fetch_all(query, {"tenant_id": tenant_id})
        else:
            query = """
                SELECT id, tenant_id, host, port, user, ttl, limit_per_minute, limit_per_hour,
                       limit_per_day, limit_behavior, use_tls, batch_size, created_at, updated_at
                FROM accounts ORDER BY id
            """
            rows = await self.adapter.fetch_all(query)

        for acc in rows:
            if "use_tls" in acc:
                acc["use_tls"] = bool(acc["use_tls"]) if acc["use_tls"] is not None else None
        return rows

    async def delete_account(self, account_id: str) -> None:
        """Remove a previously stored SMTP account and related state."""
        await self.adapter.execute(
            "DELETE FROM accounts WHERE id = :account_id",
            {"account_id": account_id},
        )
        await self.adapter.execute(
            "DELETE FROM messages WHERE account_id = :account_id",
            {"account_id": account_id},
        )
        await self.adapter.execute(
            "DELETE FROM send_log WHERE account_id = :account_id",
            {"account_id": account_id},
        )

    async def get_account(self, account_id: str) -> dict[str, Any]:
        """Fetch a single SMTP account or raise if it does not exist."""
        account = await self.adapter.fetch_one(
            "SELECT * FROM accounts WHERE id = :account_id",
            {"account_id": account_id},
        )
        if not account:
            raise ValueError(f"Account '{account_id}' not found")
        if "use_tls" in account:
            account["use_tls"] = bool(account["use_tls"]) if account["use_tls"] is not None else None
        return account

    # -------------------------------------------------------------------------
    # Messages
    # -------------------------------------------------------------------------
    @staticmethod
    def _decode_message_row(data: dict[str, Any]) -> dict[str, Any]:
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

    async def insert_messages(self, entries: Sequence[dict[str, Any]]) -> list[str]:
        """Persist a batch of messages for delivery."""
        if not entries:
            return []

        inserted: list[str] = []
        for entry in entries:
            msg_id = entry["id"]
            payload = json.dumps(entry["payload"])
            account_id = entry.get("account_id")
            priority = int(entry.get("priority", 2))
            deferred_ts = entry.get("deferred_ts")

            rowcount = await self.adapter.execute(
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

    async def fetch_ready_messages(
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

        rows = await self.adapter.fetch_all(query, params)
        return [self._decode_message_row(row) for row in rows]

    async def set_deferred(self, msg_id: str, deferred_ts: int) -> None:
        """Update the deferred timestamp for a message."""
        await self.adapter.execute(
            """
            UPDATE messages
            SET deferred_ts = :deferred_ts, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id AND sent_ts IS NULL AND error_ts IS NULL
            """,
            {"deferred_ts": deferred_ts, "msg_id": msg_id},
        )

    async def clear_deferred(self, msg_id: str) -> None:
        """Clear the deferred timestamp for a message."""
        await self.adapter.execute(
            """
            UPDATE messages
            SET deferred_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
            """,
            {"msg_id": msg_id},
        )

    async def mark_sent(self, msg_id: str, sent_ts: int) -> None:
        """Mark a message as sent."""
        await self.adapter.execute(
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
        await self.adapter.execute(
            """
            UPDATE messages
            SET error_ts = :error_ts, error = :error, sent_ts = NULL, deferred_ts = NULL,
                reported_ts = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
            """,
            {"error_ts": error_ts, "error": error, "msg_id": msg_id},
        )

    async def update_message_payload(self, msg_id: str, payload: dict[str, Any]) -> None:
        """Update the payload field of a message."""
        await self.adapter.execute(
            """
            UPDATE messages
            SET payload = :payload, updated_at = CURRENT_TIMESTAMP
            WHERE id = :msg_id
            """,
            {"payload": json.dumps(payload), "msg_id": msg_id},
        )

    async def delete_message(self, msg_id: str) -> bool:
        """Remove a message regardless of its state."""
        rowcount = await self.adapter.execute(
            "DELETE FROM messages WHERE id = :msg_id",
            {"msg_id": msg_id},
        )
        return rowcount > 0

    async def purge_messages_for_account(self, account_id: str) -> None:
        """Delete every message linked to the given account."""
        await self.adapter.execute(
            "DELETE FROM messages WHERE account_id = :account_id",
            {"account_id": account_id},
        )

    async def existing_message_ids(self, ids: Iterable[str]) -> set[str]:
        """Return the subset of ids that already exist in storage."""
        id_list = [mid for mid in ids if mid]
        if not id_list:
            return set()
        # Build dynamic query with named params
        params = {f"id_{i}": mid for i, mid in enumerate(id_list)}
        placeholders = ", ".join(f":id_{i}" for i in range(len(id_list)))
        rows = await self.adapter.fetch_all(
            f"SELECT id FROM messages WHERE id IN ({placeholders})",
            params,
        )
        return {row["id"] for row in rows}

    async def fetch_reports(self, limit: int) -> list[dict[str, Any]]:
        """Return messages that need to be reported back to the client."""
        rows = await self.adapter.fetch_all(
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
        return [self._decode_message_row(row) for row in rows]

    async def mark_reported(self, message_ids: Iterable[str], reported_ts: int) -> None:
        """Set the reported timestamp for the provided messages."""
        ids = [mid for mid in message_ids if mid]
        if not ids:
            return
        # Build dynamic query with named params
        params: dict[str, Any] = {"reported_ts": reported_ts}
        params.update({f"id_{i}": mid for i, mid in enumerate(ids)})
        placeholders = ", ".join(f":id_{i}" for i in range(len(ids)))
        await self.adapter.execute(
            f"""
            UPDATE messages
            SET reported_ts = :reported_ts, updated_at = CURRENT_TIMESTAMP
            WHERE id IN ({placeholders})
            """,
            params,
        )

    async def remove_reported_before(self, threshold_ts: int) -> int:
        """Delete reported messages older than threshold_ts."""
        return await self.adapter.execute(
            """
            DELETE FROM messages
            WHERE reported_ts IS NOT NULL
              AND reported_ts < :threshold_ts
              AND (sent_ts IS NOT NULL OR error_ts IS NOT NULL)
            """,
            {"threshold_ts": threshold_ts},
        )

    async def list_messages(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """Return messages for inspection purposes."""
        query = """
            SELECT id, account_id, priority, payload, deferred_ts, sent_ts, error_ts,
                   error, reported_ts, created_at, updated_at
            FROM messages
        """
        if active_only:
            query += " WHERE sent_ts IS NULL AND error_ts IS NULL"
        query += " ORDER BY priority ASC, created_at ASC, id ASC"

        rows = await self.adapter.fetch_all(query)
        return [self._decode_message_row(row) for row in rows]

    async def count_active_messages(self) -> int:
        """Return the number of messages still awaiting delivery."""
        row = await self.adapter.fetch_one(
            """
            SELECT COUNT(*) as cnt FROM messages
            WHERE sent_ts IS NULL AND error_ts IS NULL
            """
        )
        return int(row["cnt"]) if row else 0

    # -------------------------------------------------------------------------
    # Send log
    # -------------------------------------------------------------------------
    async def log_send(self, account_id: str, timestamp: int) -> None:
        """Record a delivery event for rate limiting purposes."""
        await self.adapter.execute(
            "INSERT INTO send_log (account_id, timestamp) VALUES (:account_id, :timestamp)",
            {"account_id": account_id, "timestamp": timestamp},
        )

    async def count_sends_since(self, account_id: str, since_ts: int) -> int:
        """Count messages sent after since_ts for the given account."""
        row = await self.adapter.fetch_one(
            "SELECT COUNT(*) as cnt FROM send_log WHERE account_id = :account_id AND timestamp > :since_ts",
            {"account_id": account_id, "since_ts": since_ts},
        )
        return int(row["cnt"]) if row else 0

    # -------------------------------------------------------------------------
    # Instance config
    # -------------------------------------------------------------------------
    async def get_config(self, key: str, default: str | None = None) -> str | None:
        """Get a configuration value by key."""
        row = await self.adapter.fetch_one(
            "SELECT value FROM instance_config WHERE key = :key",
            {"key": key},
        )
        return row["value"] if row else default

    async def set_config(self, key: str, value: str) -> None:
        """Set a configuration value."""
        await self.adapter.upsert(
            "instance_config",
            {"key": key, "value": value},
            conflict_columns=["key"],
            update_extras=["updated_at = CURRENT_TIMESTAMP"],
        )

    async def get_all_config(self) -> dict[str, str]:
        """Get all configuration values."""
        rows = await self.adapter.fetch_all(
            "SELECT key, value FROM instance_config"
        )
        return {row["key"]: row["value"] for row in rows}
