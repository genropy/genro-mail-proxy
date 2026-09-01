# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""CommandsRoutes: the six scheduler and queue commands under ``/commands``.

Every route of this group is token-protected without being admin-only, exactly
as the v1 ``/commands`` router was (one shared ``auth_dependency``). The three
tenant-scoped ones — the two message routes and nothing else — verify the token
against the tenant they name, as v1 did; ``suspend`` and ``activate`` do not,
and that asymmetry is v1's, kept as it stands.

A command the engine answers ``ok: False`` to becomes a 400 carrying its error,
with one exception: ``add-messages`` answers 400 with the whole rejection list
in the detail, because a batch can fail for as many reasons as it has messages.
"""

from __future__ import annotations

import logging
from typing import Any

from genro_asgi.exceptions import HTTPException
from genro_routes import route

from ..auth_tags import TENANT_TAG
from ..http_schema import (
    AddMessagesResponse,
    BasicOkResponse,
    CleanupMessagesPayload,
    CleanupMessagesResponse,
    DeleteMessagesPayload,
    DeleteMessagesResponse,
    EnqueueMessagesPayload,
    SuspendResponse,
)
from .base import ProxyRoutes

__all__ = ["CommandsRoutes"]

logger = logging.getLogger(__name__)


class CommandsRoutes(ProxyRoutes):
    """The ``/commands`` group: run-now, suspend, activate, and the three on messages."""

    @route(name="run-now", auth_rule=TENANT_TAG)
    async def run_now(self, *, request: Any, **kwargs: Any) -> dict[str, Any]:
        """Wake the dispatch cycle now, for this token's tenant or for all.

        The tenant comes from the token and from nowhere else: an admin token
        wakes every tenant, a tenant key only its own. A ``tenant_id`` in the
        query is accepted and discarded — v1 did the same.
        """
        tenant_id = self.application.get_token_tenant_id(request)
        result = await self.core.handle_command("run now", {"tenant_id": tenant_id})
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return BasicOkResponse.model_validate(result).model_dump(exclude_none=True)

    @route(auth_rule=TENANT_TAG)
    async def suspend(
        self, *, tenant_id: str, batch_code: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Hold a tenant's sending, or only the batch the query names."""
        result = await self.core.handle_command(
            "suspend", {"tenant_id": tenant_id, "batch_code": batch_code}
        )
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return SuspendResponse.model_validate(result).model_dump(exclude_none=True)

    @route(auth_rule=TENANT_TAG)
    async def activate(
        self, *, tenant_id: str, batch_code: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Release what suspend held, for the whole tenant or for one batch."""
        result = await self.core.handle_command(
            "activate", {"tenant_id": tenant_id, "batch_code": batch_code}
        )
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return SuspendResponse.model_validate(result).model_dump(exclude_none=True)

    @route(name="add-messages", auth_rule=TENANT_TAG)
    async def add_messages(
        self, *, body_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Enqueue a batch of messages; report what was queued and what was not.

        The 400 raised when the engine refuses the batch carries the rejection
        list in its detail — the v1 body, kept because the Genropy client reads
        the queued/rejected split off a successful answer and the error path off
        the status code alone.
        """
        payload = self.validated_payload(EnqueueMessagesPayload, body_data)
        serialized: list[dict[str, Any]] = []
        for message in payload.messages:
            data = message.model_dump(by_alias=True, exclude_none=True)
            if message.attachments is not None:
                data["attachments"] = [
                    attachment.model_dump(exclude_none=True)
                    for attachment in message.attachments
                ]
            serialized.append(data)
        command_data: dict[str, Any] = {"messages": serialized}
        if payload.default_priority is not None:
            command_data["default_priority"] = payload.default_priority
        result = await self.core.handle_command("addMessages", command_data)
        if not isinstance(result, dict) or result.get("ok") is not True:
            detail = {"error": result.get("error"), "rejected": result.get("rejected")}
            logger.error("add-messages failed: %s", detail)
            raise HTTPException(400, detail)
        return AddMessagesResponse.model_validate(result).model_dump(exclude_none=True)

    @route(name="delete-messages", auth_rule=TENANT_TAG)
    async def delete_messages(
        self, *, request: Any, tenant_id: str, body_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Drop the messages the body names, within the tenant the query names."""
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        self.application.require_tenant_scope(request, tenant_id)
        payload = self.validated_payload(DeleteMessagesPayload, body_data)
        result = await self.core.handle_command(
            "deleteMessages", {"tenant_id": tenant_id, "ids": payload.ids}
        )
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return DeleteMessagesResponse.model_validate(result).model_dump(exclude_none=True)

    @route(name="cleanup-messages", auth_rule=TENANT_TAG)
    async def cleanup_messages(
        self, *, request: Any, tenant_id: str, body_data: Any = None, **kwargs: Any
    ) -> dict[str, Any]:
        """Apply the retention policy to one tenant's reported messages."""
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        self.application.require_tenant_scope(request, tenant_id)
        payload = self.validated_payload(CleanupMessagesPayload, body_data)
        command_data: dict[str, Any] = {"tenant_id": tenant_id}
        if payload.older_than_seconds is not None:
            command_data["older_than_seconds"] = payload.older_than_seconds
        result = await self.core.handle_command("cleanupMessages", command_data)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return CleanupMessagesResponse.model_validate(result).model_dump(exclude_none=True)
