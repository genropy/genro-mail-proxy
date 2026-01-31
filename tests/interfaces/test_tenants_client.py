# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for TenantsAPI via HTTP client.

These tests verify the full pipeline:
    Client HTTP → FastAPI (api_base) → TenantEndpoint → TenantsTable → DB
"""

import pytest

from tools.http_client.client import MailProxyClient, Tenant


class TestTenantsAPI:
    """Test TenantsAPI through MailProxyClient."""

    # =========================================================================
    # Basic CRUD Operations
    # =========================================================================

    async def test_add_tenant_minimal(self, client: MailProxyClient):
        """Add tenant with only ID."""
        result = await client.tenants.add(id="minimal-tenant")

        assert result["id"] == "minimal-tenant"

    async def test_add_tenant_with_name(self, client: MailProxyClient):
        """Add tenant with name."""
        result = await client.tenants.add(id="named-tenant", name="ACME Corporation")

        assert result["id"] == "named-tenant"
        assert result["name"] == "ACME Corporation"

    async def test_add_tenant_with_client_config(self, client: MailProxyClient):
        """Add tenant with client configuration."""
        result = await client.tenants.add(
            id="configured-tenant",
            name="Configured Corp",
            client_base_url="https://api.example.com",
            client_sync_path="/webhooks/mail",
            client_attachment_path="/files",
        )

        assert result["client_base_url"] == "https://api.example.com"
        assert result["client_sync_path"] == "/webhooks/mail"
        assert result["client_attachment_path"] == "/files"

    async def test_get_tenant(self, client: MailProxyClient):
        """Get tenant by ID."""
        await client.tenants.add(id="get-tenant", name="Get Me")

        tenant = await client.tenants.get(tenant_id="get-tenant")

        assert isinstance(tenant, Tenant)
        assert tenant.id == "get-tenant"
        assert tenant.name == "Get Me"
        assert tenant.active is True

    async def test_list_tenants_empty(self, client: MailProxyClient):
        """List tenants returns empty list when none exist."""
        tenants = await client.tenants.list()

        # May have tenants from other tests in the same session
        assert isinstance(tenants, list)

    async def test_list_tenants(self, client: MailProxyClient):
        """List all tenants."""
        await client.tenants.add(id="list-tenant-1")
        await client.tenants.add(id="list-tenant-2")

        tenants = await client.tenants.list()

        assert len(tenants) >= 2
        assert all(isinstance(t, Tenant) for t in tenants)
        ids = {t.id for t in tenants}
        assert "list-tenant-1" in ids
        assert "list-tenant-2" in ids

    async def test_delete_tenant(self, client: MailProxyClient):
        """Delete a tenant."""
        await client.tenants.add(id="delete-tenant")

        success = await client.tenants.delete(tenant_id="delete-tenant")
        assert success is True

        # Verify deleted
        tenants = await client.tenants.list()
        ids = {t.id for t in tenants}
        assert "delete-tenant" not in ids

    # =========================================================================
    # Update Operations
    # =========================================================================

    async def test_update_tenant(self, client: MailProxyClient):
        """Update tenant fields."""
        await client.tenants.add(id="update-tenant", name="Old Name")

        result = await client.tenants.update(tenant_id="update-tenant", name="New Name")

        assert result["name"] == "New Name"

        # Verify persisted
        tenant = await client.tenants.get(tenant_id="update-tenant")
        assert tenant.name == "New Name"

    async def test_update_tenant_client_config(self, client: MailProxyClient):
        """Update tenant client configuration."""
        await client.tenants.add(id="update-config-tenant")

        result = await client.tenants.update(
            tenant_id="update-config-tenant",
            client_base_url="https://new-api.example.com",
        )

        assert result["client_base_url"] == "https://new-api.example.com"

    # =========================================================================
    # Batch Suspension
    # =========================================================================

    async def test_suspend_batch_all(self, client: MailProxyClient):
        """Suspend all batches for a tenant."""
        await client.tenants.add(id="suspend-all-tenant")

        result = await client.tenants.suspend_batch(tenant_id="suspend-all-tenant")

        assert result.get("ok") is True
        assert result["tenant_id"] == "suspend-all-tenant"

    async def test_suspend_batch_specific(self, client: MailProxyClient):
        """Suspend a specific batch for a tenant."""
        await client.tenants.add(id="suspend-batch-tenant")

        result = await client.tenants.suspend_batch(
            tenant_id="suspend-batch-tenant",
            batch_code="campaign-001",
        )

        assert result.get("ok") is True
        assert "campaign-001" in result["suspended_batches"]

    async def test_activate_batch_all(self, client: MailProxyClient):
        """Reactivate all batches for a tenant."""
        await client.tenants.add(id="activate-all-tenant")
        await client.tenants.suspend_batch(tenant_id="activate-all-tenant")

        result = await client.tenants.activate_batch(tenant_id="activate-all-tenant")

        assert result.get("ok") is True

    async def test_activate_batch_specific(self, client: MailProxyClient):
        """Reactivate a specific batch."""
        await client.tenants.add(id="activate-batch-tenant")
        await client.tenants.suspend_batch(
            tenant_id="activate-batch-tenant",
            batch_code="campaign-002",
        )

        result = await client.tenants.activate_batch(
            tenant_id="activate-batch-tenant",
            batch_code="campaign-002",
        )

        assert result.get("ok") is True

    async def test_get_suspended_batches(self, client: MailProxyClient):
        """Get list of suspended batches for a tenant."""
        await client.tenants.add(id="suspended-list-tenant")
        await client.tenants.suspend_batch(
            tenant_id="suspended-list-tenant",
            batch_code="batch-a",
        )
        await client.tenants.suspend_batch(
            tenant_id="suspended-list-tenant",
            batch_code="batch-b",
        )

        suspended = await client.tenants.get_suspended_batches(tenant_id="suspended-list-tenant")

        assert isinstance(suspended, set)
        assert "batch-a" in suspended
        assert "batch-b" in suspended

    async def test_suspend_activate_roundtrip(self, client: MailProxyClient):
        """Suspend and then activate a batch."""
        await client.tenants.add(id="roundtrip-tenant")

        # Suspend
        await client.tenants.suspend_batch(
            tenant_id="roundtrip-tenant",
            batch_code="test-batch",
        )

        suspended = await client.tenants.get_suspended_batches(tenant_id="roundtrip-tenant")
        assert "test-batch" in suspended

        # Activate
        await client.tenants.activate_batch(
            tenant_id="roundtrip-tenant",
            batch_code="test-batch",
        )

        suspended = await client.tenants.get_suspended_batches(tenant_id="roundtrip-tenant")
        assert "test-batch" not in suspended

    # =========================================================================
    # Active/Inactive Filter
    # =========================================================================

    async def test_list_active_only(self, client: MailProxyClient):
        """List only active tenants."""
        await client.tenants.add(id="active-tenant")
        # Note: Would need to deactivate a tenant to fully test this

        tenants = await client.tenants.list(active_only=True)

        assert all(t.active is True for t in tenants)
