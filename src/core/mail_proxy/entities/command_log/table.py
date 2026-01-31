# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Command log table for API audit trail.

This module implements an audit log for API commands, recording every
state-modifying operation with its full payload. This enables:

- Debugging: Trace what happened and when
- Migration: Replay commands to reconstruct state
- Recovery: Restore from command history after failures
- Compliance: Audit trail for regulatory requirements

Logged commands include:
    - POST /commands/add-messages: Queue messages for delivery
    - POST /commands/delete-messages: Remove messages from queue
    - POST /commands/cleanup-messages: Remove old reported messages
    - POST /account: Create/update SMTP account
    - DELETE /account/{id}: Remove SMTP account
    - POST /tenant: Create/update tenant
    - DELETE /tenant/{id}: Remove tenant

Example:
    Log an API command::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        log = proxy.db.table("command_log")

        # Record a command
        cmd_id = await log.log_command(
            endpoint="POST /commands/add-messages",
            payload={"messages": [{"to": "user@example.com"}]},
            tenant_id="acme",
            response_status=200,
        )

        # List recent commands
        commands = await log.list_commands(tenant_id="acme", limit=10)

        # Export for replay
        export = await log.export_commands(tenant_id="acme")
"""

from __future__ import annotations

import json
import time
from typing import Any

from sql import Integer, String, Table


class CommandLogTable(Table):
    """Audit log table for API command tracking.

    Records every state-modifying API command with timestamp, endpoint,
    tenant context, request payload, and response status. Commands are
    stored chronologically for replay and debugging.

    Attributes:
        name: Table name ("command_log").
        pkey: Primary key column ("id", auto-increment INTEGER).

    Table Schema:
        - id: Auto-increment primary key
        - command_ts: Unix timestamp of command
        - endpoint: HTTP method + path (e.g., "POST /commands/add-messages")
        - tenant_id: Tenant context (nullable for global commands)
        - payload: JSON request body
        - response_status: HTTP status code
        - response_body: JSON response summary

    Example:
        Query command history::

            log = proxy.db.table("command_log")

            # Get last 24 hours of commands for a tenant
            import time
            since = int(time.time()) - 86400
            commands = await log.list_commands(
                tenant_id="acme",
                since_ts=since,
            )

            # Export for migration
            export = await log.export_commands()
            # Returns: [{"endpoint": "...", "payload": {...}, ...}, ...]
    """

    name = "command_log"
    pkey = "id"

    def new_pkey_value(self) -> None:
        """Return None for INTEGER PRIMARY KEY autoincrement."""
        return None

    def configure(self) -> None:
        """Define table columns.

        Columns:
            id: Auto-increment primary key (INTEGER).
            command_ts: Unix timestamp of command execution.
            endpoint: HTTP method and path combined.
            tenant_id: Tenant context for multi-tenant filtering.
            payload: JSON-serialized request body.
            response_status: HTTP response status code.
            response_body: JSON-serialized response summary.
        """
        c = self.columns
        c.column("id", Integer)
        c.column("command_ts", Integer, nullable=False)
        c.column("endpoint", String, nullable=False)
        c.column("tenant_id", String)
        c.column("payload", String, nullable=False)
        c.column("response_status", Integer)
        c.column("response_body", String)

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
        """Record an API command in the audit log.

        Args:
            endpoint: HTTP method + path (e.g., "POST /commands/add-messages").
            payload: Request body as dict (will be JSON-serialized).
            tenant_id: Tenant context for multi-tenant commands.
            response_status: HTTP response status code.
            response_body: Response body as dict (will be JSON-serialized).
            command_ts: Unix timestamp. Defaults to current time.

        Returns:
            Auto-generated command log ID.

        Example:
            ::

                cmd_id = await log.log_command(
                    endpoint="POST /account",
                    payload={"id": "main", "host": "smtp.example.com"},
                    tenant_id="acme",
                    response_status=200,
                )
        """
        ts = command_ts if command_ts is not None else int(time.time())

        record: dict[str, Any] = {
            "command_ts": ts,
            "endpoint": endpoint,
            "tenant_id": tenant_id,
            "payload": json.dumps(payload),
            "response_status": response_status,
            "response_body": json.dumps(response_body) if response_body else None,
        }

        await self.insert(record)
        return int(record.get("id", 0))

    async def list_commands(
        self,
        *,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
        endpoint_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List logged commands with optional filters.

        Args:
            tenant_id: Filter by tenant.
            since_ts: Include commands with command_ts >= since_ts.
            until_ts: Include commands with command_ts <= until_ts.
            endpoint_filter: Filter by endpoint (SQL LIKE partial match).
            limit: Maximum number of results to return.
            offset: Skip first N results for pagination.

        Returns:
            List of command records with parsed JSON fields,
            ordered by timestamp ascending.

        Example:
            ::

                # Get add-messages commands for last hour
                import time
                since = int(time.time()) - 3600
                commands = await log.list_commands(
                    tenant_id="acme",
                    since_ts=since,
                    endpoint_filter="add-messages",
                )
        """
        conditions = []
        params: dict[str, Any] = {"limit": limit, "offset": offset}

        if tenant_id:
            conditions.append("tenant_id = :tenant_id")
            params["tenant_id"] = tenant_id
        if since_ts:
            conditions.append("command_ts >= :since_ts")
            params["since_ts"] = since_ts
        if until_ts:
            conditions.append("command_ts <= :until_ts")
            params["until_ts"] = until_ts
        if endpoint_filter:
            conditions.append("endpoint LIKE :endpoint_filter")
            params["endpoint_filter"] = f"%{endpoint_filter}%"

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        rows = await self.db.adapter.fetch_all(
            f"""
            SELECT id, command_ts, endpoint, tenant_id, payload, response_status, response_body
            FROM command_log
            WHERE {where_clause}
            ORDER BY command_ts ASC, id ASC
            LIMIT :limit OFFSET :offset
            """,
            params,
        )

        result = []
        for row in rows:
            record = dict(row)
            # Parse JSON fields
            if record.get("payload"):
                try:
                    record["payload"] = json.loads(record["payload"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if record.get("response_body"):
                try:
                    record["response_body"] = json.loads(record["response_body"])
                except (json.JSONDecodeError, TypeError):
                    pass
            result.append(record)
        return result

    async def get_command(self, command_id: int) -> dict[str, Any] | None:
        """Retrieve a single command log entry by ID.

        Args:
            command_id: Command log entry ID.

        Returns:
            Command record dict with parsed JSON fields, or None if not found.
        """
        row = await self.db.adapter.fetch_one(
            """
            SELECT id, command_ts, endpoint, tenant_id, payload, response_status, response_body
            FROM command_log
            WHERE id = :id
            """,
            {"id": command_id},
        )
        if not row:
            return None

        record = dict(row)
        if record.get("payload"):
            try:
                record["payload"] = json.loads(record["payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        if record.get("response_body"):
            try:
                record["response_body"] = json.loads(record["response_body"])
            except (json.JSONDecodeError, TypeError):
                pass
        return record

    async def export_commands(
        self,
        *,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Export commands in replay-friendly format.

        Returns minimal fields needed for replay: endpoint, tenant_id,
        payload, and command_ts (for ordering). Response data is excluded.

        Args:
            tenant_id: Filter by tenant.
            since_ts: Include commands with command_ts >= since_ts.
            until_ts: Include commands with command_ts <= until_ts.

        Returns:
            List of command dicts suitable for replay.

        Example:
            ::

                # Export all commands for migration
                export = await log.export_commands()

                # Replay on new instance
                for cmd in export:
                    await api.call(cmd["endpoint"], cmd["payload"])
        """
        commands = await self.list_commands(
            tenant_id=tenant_id,
            since_ts=since_ts,
            until_ts=until_ts,
            limit=100000,  # Large limit for export
        )

        return [
            {
                "endpoint": cmd["endpoint"],
                "tenant_id": cmd["tenant_id"],
                "payload": cmd["payload"],
                "command_ts": cmd["command_ts"],
            }
            for cmd in commands
        ]

    async def purge_before(self, threshold_ts: int) -> int:
        """Delete command logs older than threshold.

        Used for log rotation to prevent unbounded growth.

        Args:
            threshold_ts: Delete commands with command_ts < threshold_ts.

        Returns:
            Number of deleted records.

        Example:
            ::

                # Delete logs older than 30 days
                import time
                threshold = int(time.time()) - (30 * 86400)
                deleted = await log.purge_before(threshold)
                print(f"Purged {deleted} old log entries")
        """
        # Get count first for return value
        row = await self.db.adapter.fetch_one(
            "SELECT COUNT(*) as cnt FROM command_log WHERE command_ts < :threshold_ts",
            {"threshold_ts": threshold_ts},
        )
        count = int(row["cnt"]) if row else 0

        if count > 0:
            await self.execute(
                "DELETE FROM command_log WHERE command_ts < :threshold_ts",
                {"threshold_ts": threshold_ts},
            )
        return count


__all__ = ["CommandLogTable"]
