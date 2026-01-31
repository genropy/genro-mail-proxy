# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition extensions for TenantEndpoint.

This module adds API key management functionality to the base TenantEndpoint.
EE mode allows tenants to have their own API keys for scoped authentication.

API key features:
- Create new API keys for tenants (with optional expiration)
- Revoke API keys without deleting tenants

Usage:
    class TenantEndpoint(TenantEndpoint_EE, TenantEndpointBase):
        pass
"""

from __future__ import annotations

from core.mail_proxy.interface.endpoint_base import POST


class TenantEndpoint_EE:
    """Enterprise Edition: API key management for tenants.

    Adds methods for:
    - Creating tenant-specific API keys
    - Revoking API keys
    """

    @POST
    async def create_api_key(
        self,
        tenant_id: str,
        expires_at: int | None = None,
    ) -> dict:
        """Create a new API key for a tenant.

        Generates a new random API key, replacing any existing key.
        The raw key is returned once and cannot be retrieved later.
        Save it immediately!

        Args:
            tenant_id: The tenant ID.
            expires_at: Optional Unix timestamp for key expiration.

        Returns:
            Dict with ok=True and api_key (show once).

        Raises:
            ValueError: If tenant not found.
        """
        api_key = await self.table.create_api_key(tenant_id, expires_at)  # type: ignore[attr-defined]
        if api_key is None:
            raise ValueError(f"Tenant '{tenant_id}' not found")
        return {
            "ok": True,
            "tenant_id": tenant_id,
            "api_key": api_key,
            "message": "Save this API key - it will not be shown again.",
        }

    @POST
    async def revoke_api_key(self, tenant_id: str) -> dict:
        """Revoke the API key for a tenant.

        Removes the API key, preventing further authentication with it.
        The tenant can still be accessed via instance token.

        Args:
            tenant_id: The tenant ID.

        Returns:
            Dict with ok=True.

        Raises:
            ValueError: If tenant not found.
        """
        success = await self.table.revoke_api_key(tenant_id)  # type: ignore[attr-defined]
        if not success:
            raise ValueError(f"Tenant '{tenant_id}' not found")
        return {"ok": True, "tenant_id": tenant_id, "message": "API key revoked"}


__all__ = ["TenantEndpoint_EE"]
