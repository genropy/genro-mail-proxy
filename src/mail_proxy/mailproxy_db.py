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

import time
from collections.abc import Iterable, Sequence
from typing import Any

from .entities import (
    AccountsTable,
    InstanceConfigTable,
    InstanceTable,
    MessageEventTable,
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
        self.add_table(MessageEventTable)
        self.add_table(SendLogTable)
        self.add_table(InstanceConfigTable)
        self.add_table(InstanceTable)

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
    def message_events(self) -> MessageEventTable:
        return self.table("message_events")  # type: ignore[return-value]

    @property
    def config(self) -> InstanceConfigTable:
        return self.table("instance_config")  # type: ignore[return-value]

    @property
    def instance(self) -> InstanceTable:
        return self.table("instance")  # type: ignore[return-value]

    async def init_db(self) -> None:
        """Initialize database: connect, create schema, run migrations.

        After creating tables, sync_schema() is called on each table to add
        any columns that may be missing from older database versions. This
        enables automatic schema migration when new columns are added.
        """
        await self.connect()
        await self.check_structure()

        # Sync schema for all tables - adds any missing columns automatically
        await self.tenants.sync_schema()
        await self.accounts.sync_schema()
        await self.messages.sync_schema()
        await self.message_events.sync_schema()
        await self.send_log.sync_schema()

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
            await self.accounts.remove(tenant_id, account_id)
        return await self.tenants.remove(tenant_id)

    async def get_tenant_for_account(self, account_id: str) -> dict[str, Any] | None:
        return await self.tenants.get_for_account(account_id)

    # -------------------------------------------------------------------------
    # Accounts
    # -------------------------------------------------------------------------
    async def add_account(self, acc: dict[str, Any]) -> None:
        await self.accounts.add(acc)

    async def add_pec_account(self, acc: dict[str, Any]) -> None:
        """Add a PEC account with IMAP configuration."""
        await self.accounts.add_pec_account(acc)

    async def list_accounts(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        return await self.accounts.list_all(tenant_id)

    async def list_pec_accounts(self) -> list[dict[str, Any]]:
        """Return all PEC accounts."""
        return await self.accounts.list_pec_accounts()

    async def delete_account(self, tenant_id: str, account_id: str) -> None:
        """Delete an account and its related messages/logs.

        Args:
            tenant_id: The tenant that owns this account.
            account_id: The account identifier.
        """
        await self.messages.purge_for_account(account_id)
        await self.send_log.purge_for_account(account_id)
        await self.accounts.remove(tenant_id, account_id)

    async def get_account(self, account_id: str) -> dict[str, Any]:
        return await self.accounts.get(account_id)

    async def update_imap_sync_state(
        self,
        account_id: str,
        last_uid: int,
        uidvalidity: int | None = None,
    ) -> None:
        """Update IMAP sync state after processing PEC receipts."""
        await self.accounts.update_imap_sync_state(account_id, last_uid, uidvalidity)

    async def get_pec_account_ids(self) -> set[str]:
        """Return set of account IDs that are PEC accounts."""
        pec_accounts = await self.accounts.list_pec_accounts()
        return {acc["id"] for acc in pec_accounts}

    # -------------------------------------------------------------------------
    # Messages
    # -------------------------------------------------------------------------
    async def insert_messages(
        self, entries: Sequence[dict[str, Any]], auto_pec: bool = True
    ) -> list[dict[str, str]]:
        """Insert messages into the queue.

        Args:
            entries: List of message entries to insert.
            auto_pec: If True, automatically set is_pec=1 for messages
                sent via PEC accounts.

        Returns:
            List of {"id": msg_id, "pk": pk} for inserted messages.
        """
        pec_account_ids = await self.get_pec_account_ids() if auto_pec else None
        return await self.messages.insert_batch(entries, pec_account_ids)

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

    async def set_deferred(
        self, pk: str, deferred_ts: int, reason: str | None = None
    ) -> None:
        """Mark message as deferred for retry.

        Args:
            pk: Internal primary key for the update operation (UUID string).
            deferred_ts: Timestamp when message can be retried.
            reason: Optional reason for deferral.
        """
        await self.messages.set_deferred(pk, deferred_ts)
        # Record deferred event for reporting
        await self.message_events.add_event(
            message_pk=pk,
            event_type="deferred",
            event_ts=deferred_ts,
            description=reason,
        )

    async def clear_deferred(self, pk: str) -> None:
        """Clear the deferred timestamp for a message.

        Args:
            pk: Internal primary key of the message (UUID string).
        """
        await self.messages.clear_deferred(pk)

    async def mark_sent(self, pk: str, smtp_ts: int) -> None:
        """Mark message as successfully sent.

        Args:
            pk: Internal primary key for the update operation (UUID string).
            smtp_ts: Timestamp when SMTP send was attempted.
        """
        await self.messages.mark_sent(pk, smtp_ts)
        await self.message_events.add_event(
            message_pk=pk,
            event_type="sent",
            event_ts=smtp_ts,
        )

    async def mark_error(self, pk: str, smtp_ts: int, error: str) -> None:
        """Mark message as failed with error.

        Args:
            pk: Internal primary key for the update operation (UUID string).
            smtp_ts: Timestamp when SMTP send was attempted.
            error: Error description.
        """
        await self.messages.mark_error(pk, smtp_ts)
        await self.message_events.add_event(
            message_pk=pk,
            event_type="error",
            event_ts=smtp_ts,
            description=error,
        )

    async def update_message_payload(self, pk: str, payload: dict[str, Any]) -> None:
        """Update the payload field of a message.

        Args:
            pk: Internal primary key of the message (UUID string).
            payload: New payload data.
        """
        await self.messages.update_payload(pk, payload)

    async def clear_pec_flag(self, pk: str) -> None:
        """Clear is_pec flag when recipient is not a PEC address.

        Args:
            pk: Internal primary key of the message (UUID string).
        """
        await self.messages.clear_pec_flag(pk)

    async def get_pec_messages_without_acceptance(self, cutoff_ts: int) -> list[dict[str, Any]]:
        """Get PEC messages sent before cutoff without acceptance receipt."""
        return await self.messages.get_pec_without_acceptance(cutoff_ts)

    async def get_message(self, msg_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
        return await self.messages.get(msg_id, tenant_id)

    async def delete_message(self, msg_id: str, tenant_id: str | None = None) -> bool:
        """Delete a message and all its events.

        Args:
            msg_id: Client-provided message ID.
            tenant_id: Optional tenant ID for multi-tenant deletion.
        """
        # Get the message to find its pk
        msg = await self.messages.get(msg_id, tenant_id)
        if msg:
            await self.message_events.delete_for_message(msg["pk"])
        return await self.messages.remove(msg_id, tenant_id)

    async def purge_messages_for_account(self, account_id: str) -> None:
        """Delete all messages and their events for an account."""
        # Get all message pks for this account and delete their events
        messages = await self.messages.select(
            columns=["pk"], where={"account_id": account_id}
        )
        for msg in messages:
            await self.message_events.delete_for_message(msg["pk"])
        await self.messages.purge_for_account(account_id)

    async def existing_message_ids(self, ids: Iterable[str]) -> set[str]:
        return await self.messages.existing_ids(ids)

    async def mark_bounced(
        self,
        pk: str,
        bounce_type: str,
        bounce_code: str | None = None,
        bounce_reason: str | None = None,
        bounce_ts: int | None = None,
    ) -> None:
        """Record a bounce event for a message.

        Args:
            pk: Internal primary key of the message (UUID string).
            bounce_type: Type of bounce (hard, soft).
            bounce_code: SMTP error code (e.g., "550").
            bounce_reason: Reason for the bounce.
            bounce_ts: Timestamp of the bounce. Defaults to current time.
        """
        event_ts = bounce_ts if bounce_ts is not None else int(time.time())
        await self.message_events.add_event(
            message_pk=pk,
            event_type="bounce",
            event_ts=event_ts,
            description=bounce_reason,
            metadata={"bounce_type": bounce_type, "bounce_code": bounce_code},
        )

    async def remove_fully_reported_before(self, threshold_ts: int) -> int:
        """Delete messages whose all events have been reported before threshold."""
        return await self.messages.remove_fully_reported_before(threshold_ts)

    async def remove_fully_reported_before_for_tenant(
        self, threshold_ts: int, tenant_id: str
    ) -> int:
        """Delete fully reported messages older than threshold for a tenant."""
        return await self.messages.remove_fully_reported_before_for_tenant(
            threshold_ts, tenant_id
        )

    async def list_messages(
        self,
        *,
        tenant_id: str | None = None,
        active_only: bool = False,
        include_history: bool = False,
    ) -> list[dict[str, Any]]:
        return await self.messages.list_all(
            tenant_id=tenant_id,
            active_only=active_only,
            include_history=include_history,
        )

    async def count_active_messages(self) -> int:
        return await self.messages.count_active()

    async def count_pending_messages(
        self, tenant_id: str, batch_code: str | None = None
    ) -> int:
        """Count pending messages for a tenant, optionally filtered by batch_code."""
        return await self.messages.count_pending_for_tenant(tenant_id, batch_code)

    # -------------------------------------------------------------------------
    # Send log
    # -------------------------------------------------------------------------
    async def log_send(self, account_id: str, timestamp: int) -> None:
        await self.send_log.log(account_id, timestamp)

    async def count_sends_since(self, account_id: str, since_ts: int) -> int:
        return await self.send_log.count_since(account_id, since_ts)

    # -------------------------------------------------------------------------
    # Message events
    # -------------------------------------------------------------------------
    async def add_event(
        self,
        message_pk: str,
        event_type: str,
        event_ts: int,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Record a message event (sent, error, deferred, bounce, pec_*).

        Args:
            message_pk: Internal primary key of the message (UUID string).
            event_type: Type of event.
            event_ts: Unix timestamp when the event occurred.
            description: Optional description.
            metadata: Optional extra data.
        """
        return await self.message_events.add_event(
            message_pk, event_type, event_ts, description, metadata
        )

    async def fetch_unreported_events(self, limit: int) -> list[dict[str, Any]]:
        """Fetch events not yet reported to clients."""
        return await self.message_events.fetch_unreported(limit)

    async def mark_events_reported(self, event_ids: list[int], reported_ts: int) -> None:
        """Mark events as reported to client."""
        await self.message_events.mark_reported(event_ids, reported_ts)

    async def get_events_for_message(self, message_pk: str) -> list[dict[str, Any]]:
        """Get all events for a specific message.

        Args:
            message_pk: Internal primary key of the message (UUID string).
        """
        return await self.message_events.get_events_for_message(message_pk)

    async def delete_events_for_message(self, message_pk: str) -> int:
        """Delete all events for a message. Returns deleted count.

        Args:
            message_pk: Internal primary key of the message (UUID string).
        """
        return await self.message_events.delete_for_message(message_pk)

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
