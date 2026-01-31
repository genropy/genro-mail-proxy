# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for tenant API key management (Enterprise Edition).

These tests verify tenant API key endpoints via HTTP client:
    Client HTTP → FastAPI → TenantEndpoint_EE → TenantsTable_EE → DB
"""

import time

import pytest

from tools.http_client.client import MailProxyClient


@pytest.mark.ee
class TestTenantAPIKeysAPI:
    """Test tenant API key management through MailProxyClient (EE)."""

    # =========================================================================
    # API Key Creation
    # =========================================================================

    async def test_create_api_key(self, client: MailProxyClient):
        """Create API key for a tenant."""
        # Create tenant
        await client.tenants.add(id="apikey-tenant")

        # Create API key
        result = await client.tenants.create_api_key(tenant_id="apikey-tenant")

        assert result.get("ok") is True
        assert "api_key" in result
        # Key should only be shown once
        assert len(result["api_key"]) > 20  # Reasonable key length

    async def test_create_api_key_with_expiration(self, client: MailProxyClient):
        """Create API key with expiration timestamp."""
        await client.tenants.add(id="expiring-key-tenant")

        # Expire in 1 hour
        expires_at = int(time.time()) + 3600

        result = await client.tenants.create_api_key(
            tenant_id="expiring-key-tenant",
            expires_at=expires_at,
        )

        assert result.get("ok") is True
        assert "api_key" in result

    async def test_create_api_key_regenerates(self, client: MailProxyClient):
        """Creating API key for tenant that already has one regenerates it."""
        await client.tenants.add(id="regen-key-tenant")

        # Create first key
        result1 = await client.tenants.create_api_key(tenant_id="regen-key-tenant")
        key1 = result1["api_key"]

        # Create second key (should replace first)
        result2 = await client.tenants.create_api_key(tenant_id="regen-key-tenant")
        key2 = result2["api_key"]

        # Keys should be different
        assert key1 != key2

    # =========================================================================
    # API Key Revocation
    # =========================================================================

    async def test_revoke_api_key(self, client: MailProxyClient):
        """Revoke API key for a tenant."""
        await client.tenants.add(id="revoke-key-tenant")
        await client.tenants.create_api_key(tenant_id="revoke-key-tenant")

        result = await client.tenants.revoke_api_key(tenant_id="revoke-key-tenant")

        assert result.get("ok") is True

    async def test_revoke_nonexistent_key(self, client: MailProxyClient):
        """Revoking key for tenant without key is a no-op."""
        await client.tenants.add(id="no-key-tenant")

        # Should not error
        result = await client.tenants.revoke_api_key(tenant_id="no-key-tenant")

        assert result.get("ok") is True

    # =========================================================================
    # Key Lifecycle
    # =========================================================================

    async def test_api_key_lifecycle(self, client: MailProxyClient):
        """Full lifecycle: create, use, regenerate, revoke."""
        await client.tenants.add(id="lifecycle-tenant")

        # Create key
        result = await client.tenants.create_api_key(tenant_id="lifecycle-tenant")
        assert result.get("ok") is True
        first_key = result["api_key"]

        # Regenerate key
        result = await client.tenants.create_api_key(tenant_id="lifecycle-tenant")
        assert result.get("ok") is True
        second_key = result["api_key"]
        assert first_key != second_key

        # Revoke key
        result = await client.tenants.revoke_api_key(tenant_id="lifecycle-tenant")
        assert result.get("ok") is True
