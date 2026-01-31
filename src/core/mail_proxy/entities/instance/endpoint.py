# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Instance REST API endpoint for service-level operations.

This module provides the InstanceEndpoint class exposing service-level
operations for the mail proxy via REST API and CLI commands.

Operations include:
    - health: Container orchestration health check (unauthenticated)
    - status: Authenticated service status with active state
    - run_now: Trigger immediate dispatch cycle
    - suspend/activate: Control message sending per tenant/batch
    - get/update: Instance configuration management
    - get_sync_status: Monitor tenant synchronization health
    - upgrade_to_ee: Transition from Community to Enterprise Edition

Example:
    CLI commands auto-generated::

        mail-proxy instance health
        mail-proxy instance status
        mail-proxy instance run-now --tenant-id acme
        mail-proxy instance suspend --tenant-id acme
        mail-proxy instance activate --tenant-id acme
        mail-proxy instance get
        mail-proxy instance update --name production
        mail-proxy instance get-sync-status
        mail-proxy instance upgrade-to-ee

Note:
    Enterprise Edition (EE) extends this with InstanceEndpoint_EE mixin
    adding bounce detection configuration operations.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...interface.endpoint_base import POST, BaseEndpoint

if TYPE_CHECKING:
    from .table import InstanceTable


class InstanceEndpoint(BaseEndpoint):
    """REST API endpoint for instance-level operations.

    Provides service management operations including health checks,
    dispatch control, and configuration management.

    Attributes:
        name: Endpoint name used in URL paths ("instance").
        table: InstanceTable instance for configuration storage.
        proxy: Optional MailProxy instance for service operations.

    Example:
        Using the endpoint programmatically::

            endpoint = InstanceEndpoint(db.table("instance"), proxy)

            # Check service health
            health = await endpoint.health()

            # Trigger dispatch
            await endpoint.run_now(tenant_id="acme")

            # Update configuration
            await endpoint.update(name="production")
    """

    name = "instance"

    def __init__(self, table: InstanceTable, proxy: object | None = None):
        """Initialize endpoint with table and optional proxy reference.

        Args:
            table: InstanceTable for configuration storage.
            proxy: Optional MailProxy instance for service operations.
                   When provided, enables run_now, suspend, activate,
                   and get_sync_status to interact with the running service.
        """
        super().__init__(table)
        self.proxy = proxy

    async def health(self) -> dict:
        """Health check for container orchestration.

        Lightweight endpoint for liveness/readiness probes. Does not
        require authentication. Returns immediately without database access.

        Returns:
            Dict with status "ok".

        Example:
            ::

                # Kubernetes liveness probe
                # GET /instance/health
                {"status": "ok"}
        """
        return {"status": "ok"}

    async def status(self) -> dict:
        """Authenticated service status.

        Returns the current active state of the mail proxy service.
        Requires authentication.

        Returns:
            Dict with ok=True and active boolean indicating if
            the dispatch loop is running.
        """
        active = True
        if self.proxy is not None:
            active = getattr(self.proxy, "_active", True)
        return {"ok": True, "active": active}

    @POST
    async def run_now(self, tenant_id: str | None = None) -> dict:
        """Trigger immediate dispatch cycle.

        Resets the tenant's sync timer, causing the next dispatch loop
        iteration to process messages immediately.

        Args:
            tenant_id: If provided, only reset this tenant's sync timer.
                       If None, triggers dispatch for all tenants.

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

        Prevents messages from being dispatched for the specified tenant
        or batch. Messages remain in queue and will be sent when activated.

        Args:
            tenant_id: Tenant to suspend.
            batch_code: Optional batch code. If None, suspends all batches.

        Returns:
            Dict with suspended batches list and pending message count.
        """
        if self.proxy is not None:
            result = await self.proxy.handle_command(
                "suspend",
                {
                    "tenant_id": tenant_id,
                    "batch_code": batch_code,
                },
            )
            return result
        return {"ok": True, "tenant_id": tenant_id, "batch_code": batch_code}

    @POST
    async def activate(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict:
        """Resume message sending for a tenant.

        Removes suspension for the specified tenant or batch, allowing
        queued messages to be dispatched.

        Args:
            tenant_id: Tenant to activate.
            batch_code: Optional batch code. If None, clears all suspensions.

        Returns:
            Dict with remaining suspended batches list.
        """
        if self.proxy is not None:
            result = await self.proxy.handle_command(
                "activate",
                {
                    "tenant_id": tenant_id,
                    "batch_code": batch_code,
                },
            )
            return result
        return {"ok": True, "tenant_id": tenant_id, "batch_code": batch_code}

    async def get(self) -> dict:
        """Get instance configuration.

        Returns:
            Dict with ok=True and all instance configuration fields.
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
            name: New instance display name.
            api_token: New master API token.
            edition: New edition ("ce" or "ee").

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

        Returns synchronization health information for each tenant,
        useful for monitoring and debugging delivery issues.

        Returns:
            Dict with ok=True and tenants list. Each tenant contains:
                - id: Tenant identifier
                - last_sync_ts: Unix timestamp of last sync
                - next_sync_due: True if sync interval has expired
                - in_dnd: True if tenant is in Do Not Disturb mode
        """
        if self.proxy is not None:
            result = await self.proxy.handle_command("listTenantsSyncStatus", {})
            return result
        return {"ok": True, "tenants": []}

    @POST
    async def upgrade_to_ee(self) -> dict:
        """Upgrade from Community Edition to Enterprise Edition.

        Performs explicit upgrade from CE to EE mode:
            1. Verifies Enterprise modules are installed
            2. Sets edition="ee" in instance configuration
            3. Optionally generates API key for "default" tenant

        The upgrade is idempotent - calling when already EE is safe.

        Returns:
            Dict with ok=True, edition, optional default_tenant_token,
            and descriptive message.

        Raises:
            ValueError: If Enterprise modules are not installed.
        """
        import core.mail_proxy

        if not core.mail_proxy.HAS_ENTERPRISE:
            raise ValueError(
                "Enterprise modules not installed. Install with: pip install genro-mail-proxy[ee]"
            )

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
