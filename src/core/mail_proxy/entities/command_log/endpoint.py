# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Command log endpoint: API audit trail operations.

Designed for introspection by api_base/cli_base to auto-generate routes/commands.
Schema is derived from method signatures via inspect + pydantic.create_model.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from ...interface.endpoint_base import BaseEndpoint, POST

if TYPE_CHECKING:
    from .table import CommandLogTable


class CommandLogEndpoint(BaseEndpoint):
    """Command log endpoint. Methods are introspected for API/CLI generation."""

    name = "command_log"

    def __init__(self, table: CommandLogTable):
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
            since_ts: Filter commands after this timestamp.
            until_ts: Filter commands before this timestamp.
            endpoint_filter: Filter by endpoint (partial match).
            limit: Max results.
            offset: Skip first N results.

        Returns:
            List of command records.
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
        """Get a specific command by ID.

        Args:
            command_id: Command log entry ID.

        Returns:
            Command record dict.

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

        Args:
            tenant_id: Filter by tenant.
            since_ts: Filter commands after this timestamp.
            until_ts: Filter commands before this timestamp.

        Returns:
            List of commands with endpoint, tenant_id, payload, command_ts.
        """
        return await self.table.export_commands(
            tenant_id=tenant_id,
            since_ts=since_ts,
            until_ts=until_ts,
        )

    @POST
    async def purge(self, threshold_ts: int) -> dict[str, Any]:
        """Delete command logs older than threshold.

        Args:
            threshold_ts: Delete commands with command_ts < threshold.

        Returns:
            Dict with deleted count.
        """
        count = await self.table.purge_before(threshold_ts)
        return {"ok": True, "deleted": count}


__all__ = ["CommandLogEndpoint"]
