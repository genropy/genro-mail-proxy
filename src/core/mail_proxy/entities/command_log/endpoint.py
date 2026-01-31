# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Command log REST API endpoint.

This module provides the CommandLogEndpoint class exposing operations
for querying and managing the API command audit trail.

The endpoint is designed for automatic introspection by api_base and
cli_base modules, which generate FastAPI routes and Typer commands
from method signatures.

Example:
    CLI commands auto-generated::

        mail-proxy command_log list --tenant-id acme --limit 50
        mail-proxy command_log get --command-id 123
        mail-proxy command_log export --tenant-id acme
        mail-proxy command_log purge --threshold-ts 1700000000

Note:
    The command log is append-only during normal operation. Only the
    purge command removes entries, for log rotation purposes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...interface.endpoint_base import POST, BaseEndpoint

if TYPE_CHECKING:
    from .table import CommandLogTable


class CommandLogEndpoint(BaseEndpoint):
    """REST API endpoint for command log audit trail.

    Provides read access to the command log with filtering and
    export capabilities, plus a purge operation for log rotation.

    Attributes:
        name: Endpoint name used in URL paths ("command_log").
        table: CommandLogTable instance for database operations.

    Example:
        Using the endpoint programmatically::

            endpoint = CommandLogEndpoint(db.table("command_log"))

            # List recent commands
            commands = await endpoint.list(tenant_id="acme", limit=10)

            # Export for backup
            export = await endpoint.export(tenant_id="acme")
    """

    name = "command_log"

    def __init__(self, table: CommandLogTable):
        """Initialize endpoint with table reference.

        Args:
            table: CommandLogTable instance for database operations.
        """
        super().__init__(table)

    async def list(
        self,
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
            endpoint_filter: Filter by endpoint (partial match).
            limit: Maximum results to return.
            offset: Skip first N results for pagination.

        Returns:
            List of command records ordered by timestamp.
        """
        return await self.table.list_commands(
            tenant_id=tenant_id,
            since_ts=since_ts,
            until_ts=until_ts,
            endpoint_filter=endpoint_filter,
            limit=limit,
            offset=offset,
        )

    async def get(self, command_id: int) -> dict[str, Any]:
        """Retrieve a specific command by ID.

        Args:
            command_id: Command log entry ID.

        Returns:
            Command record dict with parsed JSON fields.

        Raises:
            ValueError: If command not found.
        """
        command = await self.table.get_command(command_id)
        if not command:
            raise ValueError(f"Command '{command_id}' not found")
        return command

    async def export(
        self,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """Export commands in replay-friendly format.

        Returns minimal fields needed for replay, excluding response data.

        Args:
            tenant_id: Filter by tenant.
            since_ts: Include commands with command_ts >= since_ts.
            until_ts: Include commands with command_ts <= until_ts.

        Returns:
            List of command dicts with: endpoint, tenant_id, payload, command_ts.
        """
        return await self.table.export_commands(
            tenant_id=tenant_id,
            since_ts=since_ts,
            until_ts=until_ts,
        )

    @POST
    async def purge(self, threshold_ts: int) -> dict[str, Any]:
        """Delete command logs older than threshold.

        Used for log rotation to prevent unbounded growth.

        Args:
            threshold_ts: Delete commands with command_ts < threshold_ts.

        Returns:
            Dict with "ok" status and "deleted" count.
        """
        count = await self.table.purge_before(threshold_ts)
        return {"ok": True, "deleted": count}


__all__ = ["CommandLogEndpoint"]
