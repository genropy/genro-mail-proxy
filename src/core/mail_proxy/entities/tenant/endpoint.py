# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tenant endpoint: CRUD operations for tenants.

Designed for introspection by api_base/cli_base to auto-generate routes/commands.
Schema is derived from method signatures via inspect + pydantic.create_model.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, TYPE_CHECKING

from ...interface.endpoint_base import BaseEndpoint, POST

if TYPE_CHECKING:
    from .table import TenantsTable


# Helper enums and constants

class AuthMethod(str, Enum):
    """Authentication methods for HTTP endpoints."""
    NONE = "none"
    BEARER = "bearer"
    BASIC = "basic"


class LargeFileAction(str, Enum):
    """Action when attachment exceeds size threshold."""
    WARN = "warn"
    REJECT = "reject"
    REWRITE = "rewrite"


DEFAULT_SYNC_PATH = "/mail-proxy/sync"
DEFAULT_ATTACHMENT_PATH = "/mail-proxy/attachments"


# Helper functions

def get_tenant_sync_url(tenant: dict[str, Any]) -> str | None:
    """Build full sync URL from tenant config."""
    base_url = tenant.get("client_base_url")
    if not base_url:
        return None
    sync_path = tenant.get("client_sync_path") or DEFAULT_SYNC_PATH
    return f"{base_url.rstrip('/')}{sync_path}"


def get_tenant_attachment_url(tenant: dict[str, Any]) -> str | None:
    """Build full attachment URL from tenant config."""
    base_url = tenant.get("client_base_url")
    if not base_url:
        return None
    attachment_path = tenant.get("client_attachment_path") or DEFAULT_ATTACHMENT_PATH
    return f"{base_url.rstrip('/')}{attachment_path}"


class TenantEndpoint(BaseEndpoint):
    """Tenant management endpoint. Methods are introspected for API/CLI generation."""

    name = "tenants"

    def __init__(self, table: TenantsTable):
        super().__init__(table)

    @POST
    async def add(
        self,
        id: str,
        name: str | None = None,
        client_auth: dict[str, Any] | None = None,
        client_base_url: str | None = None,
        client_sync_path: str | None = None,
        client_attachment_path: str | None = None,
        rate_limits: dict[str, Any] | None = None,
        large_file_config: dict[str, Any] | None = None,
        active: bool = True,
    ) -> dict:
        """Add or update a tenant. Returns API key for new tenants."""
        data = {k: v for k, v in locals().items() if k != "self"}
        api_key = await self.table.add(data)
        tenant = await self.table.get(id)
        if api_key:
            tenant["api_key"] = api_key
        return tenant

    async def get(self, tenant_id: str) -> dict:
        """Get a single tenant configuration."""
        tenant = await self.table.get(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant '{tenant_id}' not found")
        return tenant

    async def list(self, active_only: bool = False) -> list[dict]:
        """List all tenants."""
        return await self.table.list_all(active_only=active_only)

    @POST
    async def delete(self, tenant_id: str) -> bool:
        """Delete a tenant and all associated data."""
        return await self.table.remove(tenant_id)

    @POST
    async def update(
        self,
        tenant_id: str,
        name: str | None = None,
        client_auth: dict[str, Any] | None = None,
        client_base_url: str | None = None,
        client_sync_path: str | None = None,
        client_attachment_path: str | None = None,
        rate_limits: dict[str, Any] | None = None,
        large_file_config: dict[str, Any] | None = None,
        active: bool | None = None,
    ) -> dict:
        """Update tenant fields."""
        fields = {k: v for k, v in locals().items() if k not in ("self", "tenant_id") and v is not None}
        await self.table.update_fields(tenant_id, fields)
        return await self.table.get(tenant_id)

    @POST
    async def suspend_batch(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict:
        """Suspend sending for a tenant, optionally for a specific batch.

        Args:
            tenant_id: The tenant ID.
            batch_code: Optional batch code. If None, suspends all sending.

        Returns:
            Dict with ok=True and suspended_batches set.
        """
        success = await self.table.suspend_batch(tenant_id, batch_code)
        if not success:
            raise ValueError(f"Tenant '{tenant_id}' not found")
        suspended = await self.table.get_suspended_batches(tenant_id)
        return {"ok": True, "tenant_id": tenant_id, "suspended_batches": list(suspended)}

    @POST
    async def activate_batch(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict:
        """Resume sending for a tenant, optionally for a specific batch.

        Args:
            tenant_id: The tenant ID.
            batch_code: Optional batch code. If None, clears all suspensions.

        Returns:
            Dict with ok=True and remaining suspended_batches.

        Raises:
            ValueError: If tenant not found or trying to remove single batch from "*".
        """
        success = await self.table.activate_batch(tenant_id, batch_code)
        if not success:
            tenant = await self.table.get(tenant_id)
            if not tenant:
                raise ValueError(f"Tenant '{tenant_id}' not found")
            raise ValueError("Cannot remove single batch when all suspended. Use activate_batch(None) first.")
        suspended = await self.table.get_suspended_batches(tenant_id)
        return {"ok": True, "tenant_id": tenant_id, "suspended_batches": list(suspended)}

    async def get_suspended_batches(self, tenant_id: str) -> dict:
        """Get suspended batches for a tenant.

        Args:
            tenant_id: The tenant ID.

        Returns:
            Dict with ok=True and suspended_batches set.
        """
        tenant = await self.table.get(tenant_id)
        if not tenant:
            raise ValueError(f"Tenant '{tenant_id}' not found")
        suspended = await self.table.get_suspended_batches(tenant_id)
        return {"ok": True, "tenant_id": tenant_id, "suspended_batches": list(suspended)}


__all__ = [
    "AuthMethod",
    "DEFAULT_ATTACHMENT_PATH",
    "DEFAULT_SYNC_PATH",
    "LargeFileAction",
    "TenantEndpoint",
    "get_tenant_attachment_url",
    "get_tenant_sync_url",
]
