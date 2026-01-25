# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Per-tenant API key authentication tests.

These tests verify the per-tenant authentication functionality:
- Tenant-specific API tokens can access their own resources
- Tenant tokens are rejected when accessing other tenants
- Global API token falls back for backward compatibility
- Token management (create, revoke, rotate)
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from tests.fullstack.helpers import (
    MAILPROXY_URL,
    MAILHOG_TENANT1_API,
    clear_mailhog,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestPerTenantApiKeys:
    """Test per-tenant API key authentication.

    These tests verify that:
    - Each tenant can have its own API token
    - Tenant tokens only grant access to that tenant's resources
    - Global token maintains backward compatibility
    - Token rotation/revocation works correctly
    """

    async def test_tenant_token_access_own_resources(
        self, api_client, setup_test_tenants
    ):
        """Tenant-specific token should access that tenant's resources.

        Flow:
        1. Create tenant with specific API token
        2. Use that token to access tenant's messages
        3. Verify access is granted
        """
        ts = int(time.time())
        tenant_id = f"auth-tenant-{ts}"
        tenant_token = f"tenant-token-{ts}"

        # Create tenant with specific token
        tenant_data = {
            "id": tenant_id,
            "name": f"Auth Test Tenant {ts}",
            "api_token": tenant_token,  # tenant-specific token
        }
        resp = await api_client.post("/tenant", json=tenant_data)
        if resp.status_code == 422:
            pytest.skip("Per-tenant API token not supported in tenant schema")
        assert resp.status_code in (200, 201)

        # Create account for the tenant
        account_data = {
            "id": f"auth-account-{ts}",
            "tenant_id": tenant_id,
            "host": "mailhog-tenant1",
            "port": 1025,
            "use_tls": False,
        }
        resp = await api_client.post("/account", json=account_data)
        assert resp.status_code in (200, 201)

        # Use tenant-specific token to access resources
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            # Access with tenant token
            resp = await client.get(
                f"/messages?tenant_id={tenant_id}",
                headers={"Authorization": f"Bearer {tenant_token}"}
            )
            assert resp.status_code == 200, "Tenant token should access own resources"

    async def test_tenant_token_rejected_for_other_tenant(
        self, api_client, setup_test_tenants
    ):
        """Tenant token should be rejected when accessing other tenant's resources.

        Flow:
        1. Create two tenants with different tokens
        2. Try to access tenant-2 resources with tenant-1 token
        3. Verify access is denied (401 or 403)
        """
        ts = int(time.time())

        # Create tenant-1 with token
        tenant1 = {
            "id": f"auth-tenant1-{ts}",
            "name": f"Auth Test Tenant 1 - {ts}",
            "api_token": f"token-tenant1-{ts}",
        }
        resp = await api_client.post("/tenant", json=tenant1)
        if resp.status_code == 422:
            pytest.skip("Per-tenant API token not supported")
        assert resp.status_code in (200, 201)

        # Create tenant-2 with different token
        tenant2 = {
            "id": f"auth-tenant2-{ts}",
            "name": f"Auth Test Tenant 2 - {ts}",
            "api_token": f"token-tenant2-{ts}",
        }
        resp = await api_client.post("/tenant", json=tenant2)
        assert resp.status_code in (200, 201)

        # Try to access tenant-2 with tenant-1 token
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                f"/messages?tenant_id={tenant2['id']}",
                headers={"Authorization": f"Bearer {tenant1['api_token']}"}
            )
            # Should be denied
            assert resp.status_code in (401, 403), \
                f"Tenant-1 token should not access tenant-2 resources, got {resp.status_code}"

    async def test_global_token_fallback(
        self, api_client, setup_test_tenants
    ):
        """Global API token should maintain access to all tenants.

        The GMP_API_TOKEN should work as a superuser token that can
        access all tenant resources for backward compatibility.
        """
        ts = int(time.time())

        # Create tenant with specific token
        tenant = {
            "id": f"auth-global-{ts}",
            "name": f"Auth Global Test Tenant {ts}",
            "api_token": f"tenant-specific-{ts}",
        }
        resp = await api_client.post("/tenant", json=tenant)
        if resp.status_code == 422:
            pytest.skip("Per-tenant API token not supported")
        assert resp.status_code in (200, 201)

        # Global token (from GMP_API_TOKEN env var) should still work
        # api_client fixture uses the global token
        resp = await api_client.get(f"/messages?tenant_id={tenant['id']}")
        assert resp.status_code == 200, "Global token should access all tenant resources"

    async def test_invalid_token_rejected(
        self, api_client, setup_test_tenants
    ):
        """Invalid/unknown tokens should be rejected with 401."""
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                "/messages?tenant_id=test-tenant-1",
                headers={"Authorization": "Bearer invalid-token-xyz"}
            )
            assert resp.status_code == 401, "Invalid token should be rejected"

    async def test_missing_token_rejected(
        self, api_client, setup_test_tenants
    ):
        """Requests without token should be rejected with 401."""
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get("/messages?tenant_id=test-tenant-1")
            assert resp.status_code == 401, "Missing token should be rejected"

    async def test_token_rotation(
        self, api_client, setup_test_tenants
    ):
        """Tenant should be able to rotate (change) their API token.

        Flow:
        1. Create tenant with token A
        2. Update tenant to use token B
        3. Verify token A no longer works
        4. Verify token B works
        """
        ts = int(time.time())

        token_a = f"token-a-{ts}"
        token_b = f"token-b-{ts}"

        # Create tenant with token A
        tenant = {
            "id": f"auth-rotate-{ts}",
            "name": f"Auth Rotate Test {ts}",
            "api_token": token_a,
        }
        resp = await api_client.post("/tenant", json=tenant)
        if resp.status_code == 422:
            pytest.skip("Per-tenant API token not supported")
        assert resp.status_code in (200, 201)

        # Verify token A works
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                f"/messages?tenant_id={tenant['id']}",
                headers={"Authorization": f"Bearer {token_a}"}
            )
            assert resp.status_code == 200, "Token A should work initially"

        # Rotate to token B (using global token for admin access)
        resp = await api_client.put(
            f"/tenant?tenant_id={tenant['id']}",
            json={"api_token": token_b}
        )
        if resp.status_code == 404:
            pytest.skip("Tenant update API not implemented")
        assert resp.status_code == 200

        # Verify token A no longer works
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                f"/messages?tenant_id={tenant['id']}",
                headers={"Authorization": f"Bearer {token_a}"}
            )
            assert resp.status_code in (401, 403), "Old token A should be rejected after rotation"

            # Verify token B works
            resp = await client.get(
                f"/messages?tenant_id={tenant['id']}",
                headers={"Authorization": f"Bearer {token_b}"}
            )
            assert resp.status_code == 200, "New token B should work after rotation"

    async def test_tenant_token_scoped_operations(
        self, api_client, setup_test_tenants
    ):
        """Tenant token should only allow operations within that tenant scope.

        Test various operations (create account, add messages, etc.) to ensure
        they respect tenant scope.
        """
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        tenant_id = f"auth-scope-{ts}"
        tenant_token = f"scope-token-{ts}"

        # Create tenant with token
        tenant = {
            "id": tenant_id,
            "name": f"Auth Scope Test {ts}",
            "api_token": tenant_token,
        }
        resp = await api_client.post("/tenant", json=tenant)
        if resp.status_code == 422:
            pytest.skip("Per-tenant API token not supported")
        assert resp.status_code in (200, 201)

        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            headers = {"Authorization": f"Bearer {tenant_token}"}

            # Should be able to create account in own tenant
            account = {
                "id": f"scope-account-{ts}",
                "tenant_id": tenant_id,
                "host": "mailhog-tenant1",
                "port": 1025,
                "use_tls": False,
            }
            resp = await client.post("/account", json=account, headers=headers)
            assert resp.status_code in (200, 201), "Should create account in own tenant"

            # Should NOT be able to create account in another tenant
            account_other = {
                "id": f"scope-account-other-{ts}",
                "tenant_id": "test-tenant-1",  # different tenant
                "host": "mailhog-tenant1",
                "port": 1025,
                "use_tls": False,
            }
            resp = await client.post("/account", json=account_other, headers=headers)
            assert resp.status_code in (401, 403), \
                "Should NOT create account in other tenant"
