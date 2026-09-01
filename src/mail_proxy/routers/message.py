# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""MessagesRoutes: ``GET /messages``, the queue of one tenant.

The route reads, and only reads. Enqueue, deletion and the retention sweep are
commands and live in ``CommandsRoutes`` under ``/commands`` — the split is v1's
and the transport keeps it.

``tenant_id`` is required and the two flags are declared booleans: a value that
is not one is a 422, which is what makes the parameters provably still there.
"""

from __future__ import annotations

from typing import Any

from genro_asgi.exceptions import HTTPException
from genro_routes import route

from ..auth_tags import TENANT_TAG
from ..http_schema import MessagesResponse
from .base import ProxyRoutes

__all__ = ["MessagesRoutes"]


class MessagesRoutes(ProxyRoutes):
    """``/messages`` — the message records of one tenant."""

    @route(auth_rule=TENANT_TAG)
    async def index(
        self,
        *,
        request: Any,
        tenant_id: str,
        active_only: bool = False,
        include_history: bool = False,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """List the tenant's messages, optionally the pending ones with history."""
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        self.application.require_tenant_scope(request, tenant_id)
        result = await self.core.handle_command(
            "listMessages",
            {
                "tenant_id": tenant_id,
                "active_only": active_only,
                "include_history": include_history,
            },
        )
        return MessagesResponse.model_validate(result).model_dump(
            mode="json", exclude_none=True
        )
