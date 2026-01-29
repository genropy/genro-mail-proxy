# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Mail proxy database manager with pre-registered tables.

Extends SqlDb with mail-proxy specific tables. No business logic here -
all operations go through the table classes via self.table('name').

Example:
    db = MailProxyDb("/data/mail.db")
    await db.init_db()

    # Access tables via table() method
    await db.table('tenants').add({"id": "acme", "name": "ACME Corp"})
    tenant = await db.table('tenants').get("acme")

    await db.table('accounts').add({"id": "smtp1", "host": "smtp.example.com"})
    await db.table('messages').insert_batch([{"id": "msg1", "payload": {...}}])
"""

from __future__ import annotations

from typing import Any

from .entities import (
    AccountsTable,
    CommandLogTable,
    InstanceTable,
    MessageEventTable,
    MessagesTable,
    SendLogTable,
    TenantsTable,
)
from .sql import SqlDb


class MailProxyDb(SqlDb):
    """Mail proxy database with pre-registered tables.

    Access tables via table('name') method:
        db.table('tenants').get(tenant_id)
        db.table('accounts').list_all()
        db.table('messages').fetch_ready(...)
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
        self.add_table(CommandLogTable)
        self.add_table(InstanceTable)

    async def init_db(self) -> None:
        """Initialize database: connect, create schema, run migrations."""
        await self.connect()
        await self.check_structure()

        # Run legacy schema migrations before sync_schema
        import logging
        logger = logging.getLogger("mail_proxy")

        accounts = self.table('accounts')
        if await accounts.migrate_from_legacy_schema():
            logger.info(
                "Migrated accounts table from legacy schema (composite PK -> UUID pk)"
            )

        messages = self.table('messages')
        if await messages.migrate_from_legacy_schema():
            logger.info(
                "Migrated messages table from legacy schema (INTEGER pk -> UUID pk)"
            )

        # Sync schema for all tables - adds any missing columns automatically
        await self.table('tenants').sync_schema()
        await self.table('accounts').sync_schema()
        await self.table('messages').sync_schema()
        await self.table('message_events').sync_schema()
        await self.table('send_log').sync_schema()
        await self.table('command_log').sync_schema()
        await self.table('instance').sync_schema()

        # Populate account_pk for existing messages (after sync_schema adds the column)
        if await messages.migrate_account_pk():
            logger.info(
                "Migrated messages table: populated account_pk from account_id"
            )

        # Edition detection and default tenant creation
        await self._init_edition()

    async def _init_edition(self) -> None:
        """Initialize edition based on existing data and installed modules.

        Logic:
        - Fresh install with HAS_ENTERPRISE: set edition="ee", no default tenant
        - Fresh install without HAS_ENTERPRISE: set edition="ce", create default tenant
        - Existing DB with multiple tenants or non-default tenant: force edition="ee"
        - Existing DB with only "default" tenant: keep current edition
        """
        from . import HAS_ENTERPRISE

        tenants_table = self.table('tenants')
        instance_table = self.table('instance')

        tenants = await tenants_table.list_all()
        count = len(tenants)

        if count == 0:
            # Fresh install
            if HAS_ENTERPRISE:
                # EE fresh install: no default tenant, edition = "ee"
                await instance_table.set_edition("ee")
            else:
                # CE fresh install: create default tenant, edition = "ce"
                await tenants_table.ensure_default()
                await instance_table.set_edition("ce")

        elif count > 1 or (count == 1 and tenants[0]["id"] != "default"):
            # Existing DB with multi-tenant usage -> force EE
            await instance_table.set_edition("ee")

        # else: only "default" tenant exists -> keep current edition (CE or explicit upgrade)

    # ----------------------------------------------------------------- Convenience Properties
    # These provide direct access to table instances for backward compatibility

    @property
    def tenants(self) -> TenantsTable:
        """Direct access to tenants table."""
        return self.table('tenants')  # type: ignore[return-value]

    @property
    def accounts(self) -> AccountsTable:
        """Direct access to accounts table."""
        return self.table('accounts')  # type: ignore[return-value]

    @property
    def messages(self) -> MessagesTable:
        """Direct access to messages table."""
        return self.table('messages')  # type: ignore[return-value]

    @property
    def message_events(self) -> MessageEventTable:
        """Direct access to message_events table."""
        return self.table('message_events')  # type: ignore[return-value]

    @property
    def command_log(self) -> CommandLogTable:
        """Direct access to command_log table."""
        return self.table('command_log')  # type: ignore[return-value]

    @property
    def send_log(self) -> SendLogTable:
        """Direct access to send_log table."""
        return self.table('send_log')  # type: ignore[return-value]

    @property
    def instance(self) -> InstanceTable:
        """Direct access to instance table."""
        return self.table('instance')  # type: ignore[return-value]

    # Config convenience methods (backward compatibility with old key-value approach)
    # Typed columns in instance table
    _TYPED_CONFIG_KEYS = {"name", "api_token", "edition"}

    async def get_config(self, key: str, default: str | None = None) -> str | None:
        """Get a configuration value by key.

        Keys in _TYPED_CONFIG_KEYS are read from typed columns.
        Other keys are read from the JSON 'config' column.
        """
        row = await self.table('instance').ensure_instance()  # type: ignore[union-attr]
        if key in self._TYPED_CONFIG_KEYS:
            value = row.get(key)
        else:
            config = row.get("config") or {}
            value = config.get(key)
        return str(value) if value is not None else default

    async def set_config(self, key: str, value: str) -> None:
        """Set a configuration value.

        Keys in _TYPED_CONFIG_KEYS are saved to typed columns.
        Other keys are saved to the JSON 'config' column.
        """
        if key in self._TYPED_CONFIG_KEYS:
            await self.table('instance').update_instance({key: value})  # type: ignore[union-attr]
        else:
            row = await self.table('instance').ensure_instance()  # type: ignore[union-attr]
            config = row.get("config") or {}
            config[key] = value
            await self.table('instance').update_instance({"config": config})  # type: ignore[union-attr]

    async def get_all_config(self) -> dict[str, Any]:
        """Get all configuration values (typed columns + JSON config merged)."""
        row = await self.table('instance').ensure_instance()  # type: ignore[union-attr]
        result: dict[str, Any] = {}
        # Add typed columns
        for key in self._TYPED_CONFIG_KEYS:
            if row.get(key) is not None:
                result[key] = row[key]
        # Merge JSON config (overrides typed if same key exists)
        config = row.get("config") or {}
        result.update(config)
        return result

    # ----------------------------------------------------------------- Convenience Methods
    # These delegate to table methods for backward compatibility

    async def add_account(self, account: dict[str, Any]) -> str:
        """Add or update an account. Returns the account pk."""
        return await self.table('accounts').add(account)  # type: ignore[union-attr]

    async def list_accounts(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """List accounts, optionally filtered by tenant."""
        return await self.table('accounts').list_all(tenant_id)  # type: ignore[union-attr]

    async def get_account(self, tenant_id: str, account_id: str) -> dict[str, Any]:
        """Get a single account by tenant and account id."""
        return await self.table('accounts').get(tenant_id, account_id)  # type: ignore[union-attr]

    async def delete_account(self, tenant_id: str, account_id: str) -> None:
        """Delete an account by tenant and account id."""
        await self.table('accounts').remove(tenant_id, account_id)  # type: ignore[union-attr]

    async def add_pec_account(self, account: dict[str, Any]) -> str:
        """Add or update a PEC account with IMAP config."""
        return await self.table('accounts').add_pec_account(account)  # type: ignore[union-attr]

    async def list_pec_accounts(self) -> list[dict[str, Any]]:
        """List all PEC accounts."""
        return await self.table('accounts').list_pec_accounts()  # type: ignore[union-attr]

    async def get_pec_account_ids(self) -> set[str]:
        """Get the set of account IDs that are PEC accounts."""
        accounts = await self.table('accounts').list_pec_accounts()  # type: ignore[union-attr]
        return {acc["id"] for acc in accounts}

    async def update_imap_sync_state(
        self,
        tenant_id: str,
        account_id: str,
        last_uid: int,
        uidvalidity: int | None = None,
    ) -> None:
        """Update IMAP sync state for a PEC account."""
        await self.table('accounts').update_imap_sync_state(  # type: ignore[union-attr]
            tenant_id, account_id, last_uid, uidvalidity
        )

    async def insert_messages(
        self,
        entries: list[dict[str, Any]],
        pec_account_ids: set[str] | None = None,
        tenant_id: str | None = None,
        auto_pec: bool = True,
    ) -> list[dict[str, str]]:
        """Insert messages into the queue."""
        # Auto-fetch PEC account IDs if not provided and auto_pec is True
        if pec_account_ids is None and auto_pec:
            pec_account_ids = await self.get_pec_account_ids()
        return await self.table('messages').insert_batch(entries, pec_account_ids, tenant_id)  # type: ignore[union-attr]

    async def fetch_ready_messages(
        self,
        *,
        limit: int,
        now_ts: int,
        priority: int | None = None,
        min_priority: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch messages ready for delivery."""
        return await self.table('messages').fetch_ready(  # type: ignore[union-attr]
            limit=limit, now_ts=now_ts, priority=priority, min_priority=min_priority
        )

    async def set_deferred(self, pk: str, next_retry_ts: int, reason: str | None = None) -> None:
        """Set the next retry time for a message and optionally record an event."""
        import time
        await self.table('messages').set_deferred(pk, next_retry_ts)  # type: ignore[union-attr]
        if reason is not None:
            await self.table('message_events').add_event(pk, "deferred", int(time.time()), description=reason)  # type: ignore[union-attr]

    async def clear_deferred(self, pk: str) -> None:
        """Clear the deferred state for a message."""
        await self.table('messages').clear_deferred(pk)  # type: ignore[union-attr]

    async def mark_sent(self, pk: str, smtp_ts: int) -> None:
        """Mark a message as sent."""
        await self.table('message_events').add_event(pk, "sent", smtp_ts)  # type: ignore[union-attr]

    async def mark_error(self, pk: str, smtp_ts: int, error: str) -> None:
        """Mark a message as having an error."""
        await self.table('message_events').add_event(pk, "error", smtp_ts, description=error)  # type: ignore[union-attr]

    async def mark_bounced(
        self,
        pk: str,
        bounce_ts: int,
        bounce_type: str,
        bounce_code: str,
        bounce_reason: str,
    ) -> None:
        """Mark a message as bounced."""
        await self.table('message_events').add_event(  # type: ignore[union-attr]
            pk, "bounce", bounce_ts,
            description=bounce_reason,
            metadata={"bounce_type": bounce_type, "bounce_code": bounce_code}
        )

    async def fetch_unreported_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Fetch events that haven't been reported to the client."""
        return await self.table('message_events').fetch_unreported(limit=limit)  # type: ignore[union-attr]

    async def mark_events_reported(self, event_ids: list[int], reported_ts: int) -> None:
        """Mark events as reported."""
        await self.table('message_events').mark_reported(event_ids, reported_ts)  # type: ignore[union-attr]

    async def get_events_for_message(self, pk: str) -> list[dict[str, Any]]:
        """Get all events for a message."""
        return await self.table('message_events').get_events_for_message(pk)  # type: ignore[union-attr]

    async def add_event(
        self,
        message_pk: str,
        event_type: str,
        event_ts: int,
        description: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Add an event for a message."""
        return await self.table('message_events').add_event(  # type: ignore[union-attr]
            message_pk, event_type, event_ts, description=description, metadata=metadata
        )

    async def delete_events_for_message(self, pk: str) -> int:
        """Delete all events for a message."""
        return await self.table('message_events').delete_for_message(pk)  # type: ignore[union-attr]

    async def remove_fully_reported_before(self, threshold_ts: int) -> int:
        """Remove messages whose events are all reported before threshold."""
        return await self.table('messages').remove_fully_reported_before(threshold_ts)  # type: ignore[union-attr]

    async def remove_fully_reported_before_for_tenant(
        self, threshold_ts: int, tenant_id: str
    ) -> int:
        """Remove messages for a tenant whose events are all reported before threshold."""
        return await self.table('messages').remove_fully_reported_before_for_tenant(threshold_ts, tenant_id)  # type: ignore[union-attr]

    async def list_messages(
        self,
        tenant_id: str | None = None,
        active_only: bool = False,
        include_history: bool = False,
    ) -> list[dict[str, Any]]:
        """List messages with optional filters."""
        return await self.table('messages').list_all(  # type: ignore[union-attr]
            tenant_id=tenant_id, active_only=active_only, include_history=include_history
        )

    async def existing_message_ids(self, ids: list[str]) -> set[str]:
        """Check which message IDs already exist in the database."""
        return await self.table('messages').existing_ids(ids)  # type: ignore[union-attr]

    async def get_message(self, msg_id: str, tenant_id: str) -> dict[str, Any] | None:
        """Get a single message by ID."""
        return await self.table('messages').get(msg_id, tenant_id)  # type: ignore[union-attr]

    async def count_pending_messages(
        self, tenant_id: str, batch_code: str | None = None
    ) -> int:
        """Count pending messages for a tenant, optionally by batch_code."""
        return await self.table('messages').count_pending_for_tenant(tenant_id, batch_code)  # type: ignore[union-attr]

    async def clear_pec_flag(self, pk: str) -> None:
        """Clear the is_pec flag when recipient is not a PEC address."""
        await self.table('messages').clear_pec_flag(pk)  # type: ignore[union-attr]

    async def get_pec_messages_without_acceptance(self, cutoff_ts: int) -> list[dict[str, Any]]:
        """Get PEC messages sent before cutoff without acceptance receipt."""
        return await self.table('messages').get_pec_without_acceptance(cutoff_ts)  # type: ignore[union-attr]

    # Send log methods
    async def log_send(self, account_id: str, timestamp: int) -> None:
        """Log a send event for rate limiting."""
        await self.table('send_log').log(account_id, timestamp)  # type: ignore[union-attr]

    async def count_sends_since(self, account_id: str, since_ts: int) -> int:
        """Count messages sent since a timestamp for rate limiting."""
        return await self.table('send_log').count_since(account_id, since_ts)  # type: ignore[union-attr]

    # Command log methods
    async def log_command(
        self,
        endpoint: str,
        payload: dict[str, Any],
        *,
        tenant_id: str | None = None,
        response_status: int | None = None,
        response_body: dict[str, Any] | None = None,
        command_ts: int | None = None,
    ) -> int:
        """Log a command."""
        return await self.table('command_log').log_command(  # type: ignore[union-attr]
            endpoint=endpoint,
            payload=payload,
            tenant_id=tenant_id,
            response_status=response_status,
            response_body=response_body,
            command_ts=command_ts,
        )

    async def list_commands(
        self,
        tenant_id: str | None = None,
        endpoint_filter: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """List commands."""
        return await self.table('command_log').list_commands(  # type: ignore[union-attr]
            tenant_id=tenant_id,
            endpoint_filter=endpoint_filter,
            since_ts=since_ts,
            until_ts=until_ts,
        )

    async def export_commands(self) -> list[dict[str, Any]]:
        """Export all commands."""
        return await self.table('command_log').export_commands()  # type: ignore[union-attr]

    async def purge_commands_before(self, threshold_ts: int) -> int:
        """Purge commands older than threshold."""
        return await self.table('command_log').purge_before(threshold_ts)  # type: ignore[union-attr]


# Backward compatibility alias
Persistence = MailProxyDb

__all__ = ["MailProxyDb", "Persistence"]
