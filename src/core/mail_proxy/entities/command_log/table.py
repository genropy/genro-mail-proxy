# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Command log table manager for API audit trail.

This module implements an audit log for API commands. Each API call that
modifies state (POST, PUT, DELETE) is recorded with its full payload,
enabling replay of command sequences for debugging, migration, or recovery.

Logged commands:
- POST /commands/add-messages: Queue messages for delivery
- POST /commands/delete-messages: Remove messages from queue
- POST /commands/cleanup-messages: Remove old reported messages
- POST /account: Create/update SMTP account
- DELETE /account/{id}: Remove SMTP account
- POST /tenant: Create/update tenant
- DELETE /tenant/{id}: Remove tenant
"""

from __future__ import annotations

import json
import time
from typing import Any

from sql import Integer, String, Table


class CommandLogTable(Table):
    """Command log table: API audit trail for replay.

    Records every state-modifying API command with timestamp, endpoint,
    tenant context, request payload, and response status.
    """

    name = "command_log"

    def configure(self) -> None:
        c = self.columns
        c.column("id", Integer, primary_key=True)  # autoincrement
        c.column("command_ts", Integer, nullable=False)  # Unix timestamp
        c.column("endpoint", String, nullable=False)  # e.g., "POST /commands/add-messages"
        c.column("tenant_id", String)  # Tenant context (if applicable)
        c.column("payload", String, nullable=False)  # JSON request body
        c.column("response_status", Integer)  # HTTP status code
        c.column("response_body", String)  # JSON response (summary)

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
        """Record an API command.

        Args:
            endpoint: HTTP method + path (e.g., "POST /commands/add-messages").
            payload: Request body as dict.
            tenant_id: Optional tenant context.
            response_status: HTTP response status code.
            response_body: Response body as dict.
            command_ts: Unix timestamp. Defaults to current time.

        Returns:
            The ID of the inserted log entry.
        """
        ts = command_ts if command_ts is not None else int(time.time())
        payload_json = json.dumps(payload)
        response_json = json.dumps(response_body) if response_body else None

        params = {
            "command_ts": ts,
            "endpoint": endpoint,
            "tenant_id": tenant_id,
            "payload": payload_json,
            "response_status": response_status,
            "response_body": response_json,
        }

        # Check if using PostgreSQL
        is_postgres = hasattr(self.db.adapter, "_pool") and self.db.adapter._pool is not None  # type: ignore[attr-defined]

        if is_postgres:
            row = await self.db.adapter.fetch_one(
                """
                INSERT INTO command_log (command_ts, endpoint, tenant_id, payload, response_status, response_body)
                VALUES (:command_ts, :endpoint, :tenant_id, :payload, :response_status, :response_body)
                RETURNING id
                """,
                params,
            )
            return int(row["id"]) if row else 0
        else:
            # SQLite: insert and get max id (last_insert_rowid doesn't work across connections)
            await self.execute(
                """
                INSERT INTO command_log (command_ts, endpoint, tenant_id, payload, response_status, response_body)
                VALUES (:command_ts, :endpoint, :tenant_id, :payload, :response_status, :response_body)
                """,
                params,
            )
            row = await self.db.adapter.fetch_one(
                "SELECT MAX(id) as id FROM command_log", {}
            )
            return int(row["id"]) if row else 0

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
            since_ts: Filter commands after this timestamp.
            until_ts: Filter commands before this timestamp.
            endpoint_filter: Filter by endpoint (partial match).
            limit: Max results.
            offset: Skip first N results.

        Returns:
            List of command records.
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
        """Get a specific command by ID."""
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

        Returns only the essential fields needed for replay:
        endpoint, tenant_id, payload, command_ts (for ordering).
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

        Args:
            threshold_ts: Delete commands with command_ts < threshold.

        Returns:
            Number of deleted records.
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
