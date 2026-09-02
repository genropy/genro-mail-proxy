# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The SMTP-account routes: ``/account``, ``/account/{id}`` and ``/accounts``.

``AccountRoutes`` serves the two routes that share the ``/account`` segment.
They are two paths, not one, but a single genro-routes entry answers a segment
and everything under it, so one handler reads ``request.method`` and branches:
no id means ``POST /account`` (create or upsert), an id means
``DELETE /account/{id}``. ``AccountsRoutes`` serves the tenant-scoped list,
which has a segment of its own.

Both groups are token-protected without being admin-only, and both verify the
token against the tenant the request names — an account belongs to a tenant, so
a tenant key reaching another tenant's accounts is a 401.
"""

from __future__ import annotations

from typing import Any

from genro_asgi.exceptions import HTTPException
from genro_routes import route

from ..auth_tags import TENANT_TAG
from ..http_schema import AccountPayload, AccountsResponse, BasicOkResponse
from .base import ProxyRoutes

__all__ = ["AccountRoutes", "AccountsRoutes"]


class AccountRoutes(ProxyRoutes):
    """``/account`` — the create route and the id-scoped delete."""

    @route(auth_rule=TENANT_TAG)
    async def index(
        self,
        account_id: str | None = None,
        *,
        request: Any,
        tenant_id: str | None = None,
        body_data: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Dispatch the two ``/account`` routes on the verb and on the id."""
        if account_id is None:
            if request.method != "POST":
                raise HTTPException(405, f"{request.method} not allowed on /account")
            return await self.add_account(request, body_data)
        if request.method != "DELETE":
            raise HTTPException(
                405, f"{request.method} not allowed on /account/{{account_id}}"
            )
        return await self.delete_account(request, account_id, tenant_id)

    async def add_account(self, request: Any, body_data: Any) -> dict[str, Any]:
        """Register the account the body describes, or update it in place."""
        payload = self.validated_payload(AccountPayload, body_data)
        self.application.require_tenant_scope(request, payload.tenant_id)
        result = await self.core.handle_command("addAccount", payload.model_dump())
        return BasicOkResponse.model_validate(result).model_dump(exclude_none=True)

    async def delete_account(
        self, request: Any, account_id: str, tenant_id: str | None
    ) -> dict[str, Any]:
        """Remove the account, refusing it to anyone but its own tenant.

        ``tenant_id`` is required: v1 declared it a mandatory query parameter,
        so omitting it is a 422 and never a deletion spanning every tenant.
        """
        if tenant_id is None:
            raise HTTPException(422, "tenant_id is required")
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        self.application.require_tenant_scope(request, tenant_id)
        result = await self.core.handle_command(
            "deleteAccount", {"id": account_id, "tenant_id": tenant_id}
        )
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return BasicOkResponse.model_validate(result).model_dump(exclude_none=True)


class AccountsRoutes(ProxyRoutes):
    """``/accounts`` — the accounts of one tenant, that tenant's only."""

    @route(auth_rule=TENANT_TAG)
    async def index(
        self, *, request: Any, tenant_id: str, **kwargs: Any
    ) -> dict[str, Any]:
        """List the tenant's accounts, passwords excluded by the answer shape."""
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        self.application.require_tenant_scope(request, tenant_id)
        result = await self.core.handle_command("listAccounts", {"tenant_id": tenant_id})
        return AccountsResponse.model_validate(result).model_dump(
            mode="json", exclude_none=True
        )
