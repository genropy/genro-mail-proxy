# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Mail proxy database manager with pre-registered tables.

Extends SqlDb with mail-proxy specific tables and high-level operations.

Example:
    db = MailProxyDb("/data/mail.db")
    await db.init_db()

    # High-level operations
    await db.add_tenant({"id": "acme", "name": "ACME Corp"})
    tenant = await db.get_tenant("acme")

    await db.add_account({"id": "smtp1", "host": "smtp.example.com", "port": 587})

    await db.insert_messages([{"id": "msg1", "payload": {...}}])
    ready = await db.fetch_ready_messages(limit=10, now_ts=time.time())
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .entities import (
    AccountsTable,
    InstanceConfigTable,
    MessagesTable,
    SendLogTable,
    TenantsTable,
)
from .sql import SqlDb


class MailProxyDb(SqlDb):
    """Mail proxy database with pre-registered tables.

    Provides high-level operations delegating to table managers.
    Backward compatible with the old Persistence API.
    """

    def __init__(self, connection_string: str = "/data/mail_service.db"):
        """Initialize the mail proxy database.

        Args:
            connection_string: Database connection string. Formats:
                - "/path/to/db.sqlite" - SQLite file
                - ":memory:" - SQLite in-memory
                - "sqlite:/path/to/db" - SQLite explicit
                - "postgresql://user:pass@host/db" - PostgreSQL
        """
        super().__init__(connection_string)
        self.db_path = connection_string  # Backward compatibility

        # Register all tables
        self.add_table(TenantsTable)
        self.add_table(AccountsTable)
        self.add_table(MessagesTable)
        self.add_table(SendLogTable)
        self.add_table(InstanceConfigTable)

    @property
    def tenants(self) -> TenantsTable:
        return self.table("tenants")  # type: ignore[return-value]

    @property
    def accounts(self) -> AccountsTable:
        return self.table("accounts")  # type: ignore[return-value]

    @property
    def messages(self) -> MessagesTable:
        return self.table("messages")  # type: ignore[return-value]

    @property
    def send_log(self) -> SendLogTable:
        return self.table("send_log")  # type: ignore[return-value]

    @property
    def config(self) -> InstanceConfigTable:
        return self.table("instance_config")  # type: ignore[return-value]

    async def init_db(self) -> None:
        """Initialize database: connect, create schema, run migrations."""
        await self.connect()
        await self.check_structure()

        # Run migrations for existing databases
        for col in ["use_tls", "batch_size", "tenant_id", "updated_at"]:
            await self.accounts.add_column_if_missing(col)

        await self.tenants.add_column_if_missing("large_file_config")

    # -------------------------------------------------------------------------
    # Tenants
    # -------------------------------------------------------------------------
    async def add_tenant(self, tenant: dict[str, Any]) -> None:
        await self.tenants.add(tenant)

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        return await self.tenants.get(tenant_id)

    async def list_tenants(self, active_only: bool = False) -> list[dict[str, Any]]:
        return await self.tenants.list_all(active_only)

    async def update_tenant(self, tenant_id: str, updates: dict[str, Any]) -> bool:
        return await self.tenants.update_fields(tenant_id, updates)

    async def delete_tenant(self, tenant_id: str) -> bool:
        """Delete tenant and cascade to accounts/messages."""
        accs = await self.accounts.select(columns=["id"], where={"tenant_id": tenant_id})
        for acc in accs:
            account_id = acc["id"]
            await self.messages.purge_for_account(account_id)
            await self.send_log.purge_for_account(account_id)
            await self.accounts.remove(account_id)
        return await self.tenants.remove(tenant_id)

    async def get_tenant_for_account(self, account_id: str) -> dict[str, Any] | None:
        return await self.tenants.get_for_account(account_id)

    # -------------------------------------------------------------------------
    # Accounts
    # -------------------------------------------------------------------------
    async def add_account(self, acc: dict[str, Any]) -> None:
        await self.accounts.add(acc)

    async def list_accounts(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        return await self.accounts.list_all(tenant_id)

    async def delete_account(self, account_id: str) -> None:
        await self.messages.purge_for_account(account_id)
        await self.send_log.purge_for_account(account_id)
        await self.accounts.remove(account_id)

    async def get_account(self, account_id: str) -> dict[str, Any]:
        return await self.accounts.get(account_id)

    # -------------------------------------------------------------------------
    # Messages
    # -------------------------------------------------------------------------
    async def insert_messages(self, entries: Sequence[dict[str, Any]]) -> list[str]:
        return await self.messages.insert_batch(entries)

    async def fetch_ready_messages(
        self,
        *,
        limit: int,
        now_ts: int,
        priority: int | None = None,
        min_priority: int | None = None,
    ) -> list[dict[str, Any]]:
        return await self.messages.fetch_ready(
            limit=limit, now_ts=now_ts, priority=priority, min_priority=min_priority
        )

    async def set_deferred(self, msg_id: str, deferred_ts: int) -> None:
        await self.messages.set_deferred(msg_id, deferred_ts)

    async def clear_deferred(self, msg_id: str) -> None:
        await self.messages.clear_deferred(msg_id)

    async def mark_sent(self, msg_id: str, sent_ts: int) -> None:
        await self.messages.mark_sent(msg_id, sent_ts)

    async def mark_error(self, msg_id: str, error_ts: int, error: str) -> None:
        await self.messages.mark_error(msg_id, error_ts, error)

    async def update_message_payload(self, msg_id: str, payload: dict[str, Any]) -> None:
        await self.messages.update_payload(msg_id, payload)

    async def delete_message(self, msg_id: str) -> bool:
        return await self.messages.remove(msg_id)

    async def purge_messages_for_account(self, account_id: str) -> None:
        await self.messages.purge_for_account(account_id)

    async def existing_message_ids(self, ids: Iterable[str]) -> set[str]:
        return await self.messages.existing_ids(ids)

    async def fetch_reports(self, limit: int) -> list[dict[str, Any]]:
        return await self.messages.fetch_reports(limit)

    async def mark_reported(self, message_ids: Iterable[str], reported_ts: int) -> None:
        await self.messages.mark_reported(message_ids, reported_ts)

    async def remove_reported_before(self, threshold_ts: int) -> int:
        return await self.messages.remove_reported_before(threshold_ts)

    async def list_messages(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        return await self.messages.list_all(active_only=active_only)

    async def count_active_messages(self) -> int:
        return await self.messages.count_active()

    # -------------------------------------------------------------------------
    # Send log
    # -------------------------------------------------------------------------
    async def log_send(self, account_id: str, timestamp: int) -> None:
        await self.send_log.log(account_id, timestamp)

    async def count_sends_since(self, account_id: str, since_ts: int) -> int:
        return await self.send_log.count_since(account_id, since_ts)

    # -------------------------------------------------------------------------
    # Instance config
    # -------------------------------------------------------------------------
    async def get_config(self, key: str, default: str | None = None) -> str | None:
        return await self.config.get(key, default)

    async def set_config(self, key: str, value: str) -> None:
        await self.config.set(key, value)

    async def get_all_config(self) -> dict[str, str]:
        return await self.config.get_all()


# Backward compatibility alias
Persistence = MailProxyDb

__all__ = ["MailProxyDb", "Persistence"]
