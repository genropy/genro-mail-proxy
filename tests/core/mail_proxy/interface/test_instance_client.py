# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for InstanceAPI via HTTP client.

These tests verify the full pipeline:
    Client HTTP → FastAPI (api_base) → InstanceEndpoint → InstanceTable → DB
"""

import pytest

from tools.http_client.client import MailProxyClient


class TestInstanceAPI:
    """Test InstanceAPI through MailProxyClient."""

    # =========================================================================
    # Health and Status
    # =========================================================================

    async def test_health(self, client: MailProxyClient):
        """Health endpoint returns ok status."""
        result = await client.instance.health()

        assert result["status"] == "ok"

    async def test_health_convenience(self, client: MailProxyClient):
        """Health convenience method returns boolean."""
        healthy = await client.health()

        assert healthy is True

    async def test_status(self, client: MailProxyClient):
        """Status endpoint returns ok and active state."""
        result = await client.instance.status()

        assert result.get("ok") is True
        assert "active" in result

    async def test_status_convenience(self, client: MailProxyClient):
        """Status convenience method returns dict."""
        result = await client.status()

        assert result.get("ok") is True

    # =========================================================================
    # Instance Configuration
    # =========================================================================

    async def test_get_instance(self, client: MailProxyClient):
        """Get instance configuration."""
        result = await client.instance.get()

        assert result.get("ok") is True

    async def test_update_instance_name(self, client: MailProxyClient):
        """Update instance name."""
        result = await client.instance.update(name="test-instance")

        assert result.get("ok") is True

    async def test_update_instance_edition(self, client: MailProxyClient):
        """Update instance edition."""
        result = await client.instance.update(edition="ce")

        assert result.get("ok") is True

    # =========================================================================
    # Dispatch Control
    # =========================================================================

    @pytest.mark.skip(reason="Requires full MailProxy with handle_command")
    async def test_run_now(self, client: MailProxyClient):
        """Trigger immediate dispatch cycle."""
        result = await client.instance.run_now()

        assert result.get("ok") is True

    @pytest.mark.skip(reason="Requires full MailProxy with handle_command")
    async def test_run_now_for_tenant(self, client: MailProxyClient, setup_tenant):
        """Trigger dispatch for specific tenant."""
        result = await client.instance.run_now(tenant_id=setup_tenant)

        assert result.get("ok") is True

    @pytest.mark.skip(reason="Requires full MailProxy with handle_command")
    async def test_run_now_convenience(self, client: MailProxyClient):
        """Run now convenience method on client."""
        result = await client.run_now()

        assert result.get("ok") is True

    # =========================================================================
    # Suspend/Activate via Instance API
    # =========================================================================

    async def test_suspend_tenant(self, client: MailProxyClient, setup_tenant):
        """Suspend sending for a tenant via instance API."""
        result = await client.instance.suspend(tenant_id=setup_tenant)

        assert result.get("ok") is True
        assert result["tenant_id"] == setup_tenant

    async def test_suspend_batch(self, client: MailProxyClient, setup_tenant):
        """Suspend specific batch via instance API."""
        result = await client.instance.suspend(
            tenant_id=setup_tenant,
            batch_code="campaign-x",
        )

        assert result.get("ok") is True
        assert "campaign-x" in result["suspended_batches"]

    async def test_activate_tenant(self, client: MailProxyClient, setup_tenant):
        """Activate sending for a tenant via instance API."""
        # First suspend
        await client.instance.suspend(tenant_id=setup_tenant)

        # Then activate
        result = await client.instance.activate(tenant_id=setup_tenant)

        assert result.get("ok") is True
        assert result["tenant_id"] == setup_tenant

    async def test_activate_batch(self, client: MailProxyClient, setup_tenant):
        """Activate specific batch via instance API."""
        # First suspend
        await client.instance.suspend(
            tenant_id=setup_tenant,
            batch_code="campaign-y",
        )

        # Then activate
        result = await client.instance.activate(
            tenant_id=setup_tenant,
            batch_code="campaign-y",
        )

        assert result.get("ok") is True

    async def test_suspend_convenience(self, client: MailProxyClient, setup_tenant):
        """Suspend convenience method on client."""
        result = await client.suspend(tenant_id=setup_tenant)

        assert result.get("ok") is True

    async def test_activate_convenience(self, client: MailProxyClient, setup_tenant):
        """Activate convenience method on client."""
        await client.suspend(tenant_id=setup_tenant)

        result = await client.activate(tenant_id=setup_tenant)

        assert result.get("ok") is True

    # =========================================================================
    # Sync Status
    # =========================================================================

    async def test_get_sync_status(self, client: MailProxyClient, setup_tenant):
        """Get sync status for all tenants."""
        result = await client.instance.get_sync_status()

        assert result.get("ok") is True
        assert "tenants" in result

    # =========================================================================
    # Edition Upgrade
    # =========================================================================

    async def test_upgrade_to_ee(self, client: MailProxyClient):
        """Attempt to upgrade to Enterprise Edition."""
        result = await client.instance.upgrade_to_ee()

        # Result depends on whether EE modules are available
        assert "ok" in result or "error" in result
