# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The tenant routes: ``/tenant`` and its ids, ``/tenants`` and its sync view.

``TenantRoutes`` carries the six routes that share the ``/tenant`` segment —
create, the three verbs on one id, and the two on its api key — so one entry
answers them all and reads ``request.method`` to tell them apart. The shape of
the request says which group it belongs to: no id is the create, an id alone is
the trio, an id plus ``api-key`` is the key pair.

The auth gate is not uniform across them, and the router cannot express that:
``GET`` and ``PUT`` on one tenant are open to that tenant's own key, everything
else is admin-only. So the entry declares the weaker gate and the admin-only
branches call ``require_admin_tag`` themselves — a tenant key reaching them is
the 403 v1 answered, never a silent success.
"""

from __future__ import annotations

from typing import Any

from genro_asgi.exceptions import HTTPException, HTTPNotFound
from genro_routes import route

from ..auth_tags import ADMIN_TAG, TENANT_TAG
from ..http_schema import (
    AddTenantResponse,
    ApiKeyResponse,
    BasicOkResponse,
    TenantInfo,
    TenantPayload,
    TenantsResponse,
    TenantsSyncStatusResponse,
    TenantUpdatePayload,
)
from .base import ProxyRoutes

__all__ = ["TenantRoutes", "TenantsRoutes"]


class TenantRoutes(ProxyRoutes):
    """``/tenant`` — creation, the three verbs on one id, and the api key."""

    @route(auth_rule=TENANT_TAG)
    async def index(
        self,
        tenant_id: str | None = None,
        sub: str | None = None,
        *,
        request: Any,
        body_data: Any = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Dispatch the six ``/tenant`` routes on the verb and on the path shape."""
        method = request.method
        if tenant_id is None:
            if method != "POST":
                raise HTTPException(405, f"{method} not allowed on /tenant")
            return await self.add_tenant(request, body_data)
        if sub is None:
            if method == "GET":
                return await self.get_tenant(request, tenant_id)
            if method == "PUT":
                return await self.update_tenant(request, tenant_id, body_data)
            if method == "DELETE":
                return await self.delete_tenant(request, tenant_id)
            raise HTTPException(405, f"{method} not allowed on /tenant/{{tenant_id}}")
        if sub != "api-key":
            raise HTTPNotFound(f"/tenant/{tenant_id}/{sub}")
        if method == "POST":
            return await self.create_api_key(request, tenant_id)
        if method == "DELETE":
            return await self.revoke_api_key(request, tenant_id)
        raise HTTPException(405, f"{method} not allowed on /tenant/{{tenant_id}}/api-key")

    async def add_tenant(self, request: Any, body_data: Any) -> dict[str, Any]:
        """Register the tenant the body describes; a new one receives its api key."""
        self.application.require_admin_tag(request)
        payload = self.validated_payload(TenantPayload, body_data)
        result = await self.core.handle_command("addTenant", payload.model_dump())
        return AddTenantResponse.model_validate(result).model_dump(exclude_none=True)

    async def get_tenant(self, request: Any, tenant_id: str) -> dict[str, Any]:
        """Return one tenant's configuration, without the outcome key."""
        self.application.require_tenant_scope(request, tenant_id)
        result = await self.core.handle_command("getTenant", {"id": tenant_id})
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        result.pop("ok", None)
        return TenantInfo(**result).model_dump(mode="json", exclude_none=True)

    async def update_tenant(
        self, request: Any, tenant_id: str, body_data: Any
    ) -> dict[str, Any]:
        """Apply the fields the body names, leaving every other one alone."""
        self.application.require_tenant_scope(request, tenant_id)
        payload = self.validated_payload(TenantUpdatePayload, body_data)
        update_data = payload.model_dump(exclude_none=True)
        update_data["id"] = tenant_id
        result = await self.core.handle_command("updateTenant", update_data)
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return BasicOkResponse.model_validate(result).model_dump(exclude_none=True)

    async def delete_tenant(self, request: Any, tenant_id: str) -> dict[str, Any]:
        """Remove the tenant with its accounts and its queue. Irreversible."""
        self.application.require_admin_tag(request)
        result = await self.core.handle_command("deleteTenant", {"id": tenant_id})
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return BasicOkResponse.model_validate(result).model_dump(exclude_none=True)

    async def create_api_key(self, request: Any, tenant_id: str) -> dict[str, Any]:
        """Generate the tenant's api key, invalidating whatever it had."""
        self.application.require_admin_tag(request)
        api_key = await self.core.db.tenants.create_api_key(tenant_id)
        if not api_key:
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return ApiKeyResponse(ok=True, api_key=api_key).model_dump(exclude_none=True)

    async def revoke_api_key(self, request: Any, tenant_id: str) -> dict[str, Any]:
        """Invalidate the tenant's api key, leaving it with none."""
        self.application.require_admin_tag(request)
        revoked = await self.core.db.tenants.revoke_api_key(tenant_id)
        if not revoked:
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return BasicOkResponse(ok=True).model_dump(exclude_none=True)


class TenantsRoutes(ProxyRoutes):
    """``/tenants`` — the listing and the sync view, both admin-only."""

    @route(auth_rule=ADMIN_TAG)
    async def index(self, *, active_only: bool = False, **kwargs: Any) -> dict[str, Any]:
        """List every tenant, or only the active ones."""
        result = await self.core.handle_command("listTenants", {"active_only": active_only})
        return TenantsResponse.model_validate(result).model_dump(
            mode="json", exclude_none=True
        )

    @route(name="sync-status", auth_rule=ADMIN_TAG)
    async def sync_status(self, **kwargs: Any) -> dict[str, Any]:
        """Report every tenant's last sync, whether one is due, and its DND state."""
        result = await self.core.handle_command("listTenantsSyncStatus", {})
        return TenantsSyncStatusResponse.model_validate(result).model_dump(
            mode="json", exclude_none=True
        )
