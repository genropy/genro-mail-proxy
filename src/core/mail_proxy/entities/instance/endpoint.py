# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Instance endpoint: service-level operations for genro-mail-proxy.

Operations exposed via API and CLI (auto-generated from signatures):
- health: Container orchestration health check
- status: Authenticated service status
- run_now: Trigger immediate dispatch cycle
- suspend: Pause sending for tenant/batch
- activate: Resume sending for tenant/batch
- get: Get instance configuration
- update: Update instance configuration
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...interface.endpoint_base import BaseEndpoint, POST

if TYPE_CHECKING:
    from .table import InstanceTable


class InstanceEndpoint(BaseEndpoint):
    """Instance-level operations. Methods are introspected for API/CLI generation."""

    name = "instance"

    def __init__(self, table: InstanceTable, proxy: object | None = None):
        """Initialize endpoint with instance table and optional proxy reference.

        Args:
            table: InstanceTable for configuration storage.
            proxy: Optional MailProxy instance for service operations.
        """
        super().__init__(table)
        self.proxy = proxy

    async def health(self) -> dict:
        """Health check for container orchestration.

        Returns:
            Dict with status "ok".
        """
        return {"status": "ok"}

    async def status(self) -> dict:
        """Authenticated service status.

        Returns:
            Dict with ok=True and active state.
        """
        active = True
        if self.proxy is not None:
            active = getattr(self.proxy, "_active", True)
        return {"ok": True, "active": active}

    @POST
    async def run_now(self, tenant_id: str | None = None) -> dict:
        """Trigger immediate dispatch cycle.

        Args:
            tenant_id: If provided, only reset this tenant's sync timer.

        Returns:
            Dict with ok=True.
        """
        if self.proxy is not None:
            result = await self.proxy.handle_command("run now", {"tenant_id": tenant_id})
            return result
        return {"ok": True}

    @POST
    async def suspend(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict:
        """Suspend message sending for a tenant.

        Args:
            tenant_id: The tenant to suspend.
            batch_code: Optional batch code. If None, suspends all.

        Returns:
            Dict with suspended batches and pending count.
        """
        if self.proxy is not None:
            result = await self.proxy.handle_command("suspend", {
                "tenant_id": tenant_id,
                "batch_code": batch_code,
            })
            return result
        return {"ok": True, "tenant_id": tenant_id, "batch_code": batch_code}

    @POST
    async def activate(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict:
        """Resume message sending for a tenant.

        Args:
            tenant_id: The tenant to activate.
            batch_code: Optional batch code. If None, clears all.

        Returns:
            Dict with remaining suspended batches.
        """
        if self.proxy is not None:
            result = await self.proxy.handle_command("activate", {
                "tenant_id": tenant_id,
                "batch_code": batch_code,
            })
            return result
        return {"ok": True, "tenant_id": tenant_id, "batch_code": batch_code}

    async def get(self) -> dict:
        """Get instance configuration.

        Returns:
            Instance configuration dict.
        """
        instance = await self.table.get_instance()
        if instance is None:
            return {"ok": True, "id": 1, "name": "mail-proxy", "edition": "ce"}
        return {"ok": True, **instance}

    @POST
    async def update(
        self,
        name: str | None = None,
        api_token: str | None = None,
        edition: str | None = None,
    ) -> dict:
        """Update instance configuration.

        Args:
            name: Instance name.
            api_token: API token.
            edition: Edition (ce or ee).

        Returns:
            Dict with ok=True.
        """
        updates = {}
        if name is not None:
            updates["name"] = name
        if api_token is not None:
            updates["api_token"] = api_token
        if edition is not None:
            updates["edition"] = edition

        if updates:
            await self.table.update_instance(updates)
        return {"ok": True}

    async def get_sync_status(self) -> dict:
        """Get sync status for all tenants.

        Returns the last sync timestamp and Do Not Disturb status for each tenant.
        Useful for monitoring tenant synchronization health.

        Returns:
            Dict with ok=True and tenants list containing:
            - id: Tenant identifier
            - last_sync_ts: Unix timestamp of last sync (or future for DND)
            - next_sync_due: True if sync interval has expired
            - in_dnd: True if tenant is in Do Not Disturb mode
        """
        if self.proxy is not None:
            result = await self.proxy.handle_command("listTenantsSyncStatus", {})
            return result
        return {"ok": True, "tenants": []}

    @POST
    async def upgrade_to_ee(self) -> dict:
        """Upgrade instance from Community Edition to Enterprise Edition.

        Performs an explicit upgrade from CE to EE mode:
        1. Verifies that Enterprise modules are installed
        2. Sets edition="ee" in the instance configuration
        3. If a "default" tenant exists without an API key, generates one

        The upgrade is idempotent - calling it when already in EE mode is safe.

        Returns:
            Dict with ok=True, edition, optional default_tenant_token, and message.

        Raises:
            ValueError: If Enterprise modules are not installed.
        """
        from ... import HAS_ENTERPRISE

        if not HAS_ENTERPRISE:
            raise ValueError("Enterprise modules not installed. Install with: pip install genro-mail-proxy[ee]")

        # Check if already EE
        if await self.table.is_enterprise():
            return {"ok": True, "edition": "ee", "message": "Already in Enterprise Edition"}

        # Upgrade to EE
        await self.table.set_edition("ee")

        # If "default" tenant exists without token, generate one
        if self.proxy is not None:
            tenants_table = self.proxy.db.table("tenants")
            default_tenant = await tenants_table.get("default")
            if default_tenant and not default_tenant.get("api_key_hash"):
                token = await tenants_table.create_api_key("default")
                return {
                    "ok": True,
                    "edition": "ee",
                    "default_tenant_token": token,
                    "message": "Upgraded to Enterprise Edition. Save the default tenant token - it will not be shown again.",
                }

        return {"ok": True, "edition": "ee", "message": "Upgraded to Enterprise Edition"}


__all__ = ["InstanceEndpoint"]
