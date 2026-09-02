# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""CommandLogRoutes: the audit trail of the command endpoints.

Two admin-only reads over the same rows. ``/command-log`` answers the filtered
page plus a count, ``/command-log/export`` the replay-shaped form of the same
window. Both go straight to the database: the trail is written by the engine
when it runs a logged command, never by a route.

``total`` counts the returned page, not the whole trail — a v1 behaviour the
transport keeps as it stands.
"""

from __future__ import annotations

from typing import Any

from genro_routes import route

from ..auth_tags import ADMIN_TAG
from ..http_schema import CommandLogEntry, CommandLogResponse
from .base import ProxyRoutes

__all__ = ["CommandLogRoutes"]


class CommandLogRoutes(ProxyRoutes):
    """``/command-log`` — the trail, read as a page or exported for replay."""

    @route(auth_rule=ADMIN_TAG)
    async def index(
        self,
        *,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
        endpoint_filter: str | None = None,
        limit: int = 100,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """List the logged commands the filters select."""
        commands = await self.core.db.list_commands(
            tenant_id=tenant_id,
            since_ts=since_ts,
            until_ts=until_ts,
            endpoint_filter=endpoint_filter,
            limit=limit,
        )
        response = CommandLogResponse(
            ok=True,
            commands=[CommandLogEntry(**command) for command in commands],
            total=len(commands),
        )
        return response.model_dump(mode="json", exclude_none=True)

    @route(auth_rule=ADMIN_TAG)
    async def export(
        self,
        *,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Export the same window in the replay-friendly shape."""
        commands = await self.core.db.export_commands(
            tenant_id=tenant_id,
            since_ts=since_ts,
            until_ts=until_ts,
        )
        return {"ok": True, "commands": commands}
