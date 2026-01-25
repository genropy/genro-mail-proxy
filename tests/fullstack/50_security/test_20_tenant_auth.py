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
        1. Create tenant
        2. Generate API key for tenant
        3. Use that key to access tenant's messages
        4. Verify access is granted
        """
        ts = int(time.time())
        tenant_id = f"auth-tenant-{ts}"

        # Create tenant
        tenant_data = {
            "id": tenant_id,
            "name": f"Auth Test Tenant {ts}",
        }
        resp = await api_client.post("/tenant", json=tenant_data)
        assert resp.status_code in (200, 201)

        # Generate API key for tenant
        resp = await api_client.post(f"/tenant/{tenant_id}/api-key")
        assert resp.status_code == 200, f"Failed to create API key: {resp.text}"
        api_key_data = resp.json()
        tenant_token = api_key_data["api_key"]

        # Create account for the tenant
        account_data = {
            "id": f"auth-account-{ts}",
            "tenant_id": tenant_id,
            "host": "localhost",
            "port": 1025,
            "use_tls": False,
        }
        resp = await api_client.post("/account", json=account_data)
        assert resp.status_code in (200, 201)

        # Use tenant-specific token to access resources
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                f"/messages?tenant_id={tenant_id}",
                headers={"X-API-Token": tenant_token}
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

        # Create tenant-1
        tenant1_id = f"auth-tenant1-{ts}"
        resp = await api_client.post("/tenant", json={
            "id": tenant1_id,
            "name": f"Auth Test Tenant 1 - {ts}",
        })
        assert resp.status_code in (200, 201)

        # Generate API key for tenant-1
        resp = await api_client.post(f"/tenant/{tenant1_id}/api-key")
        assert resp.status_code == 200
        tenant1_token = resp.json()["api_key"]

        # Create tenant-2
        tenant2_id = f"auth-tenant2-{ts}"
        resp = await api_client.post("/tenant", json={
            "id": tenant2_id,
            "name": f"Auth Test Tenant 2 - {ts}",
        })
        assert resp.status_code in (200, 201)

        # Try to access tenant-2 with tenant-1 token
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                f"/messages?tenant_id={tenant2_id}",
                headers={"X-API-Token": tenant1_token}
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

        # Create tenant with API key
        tenant_id = f"auth-global-{ts}"
        resp = await api_client.post("/tenant", json={
            "id": tenant_id,
            "name": f"Auth Global Test Tenant {ts}",
        })
        assert resp.status_code in (200, 201)

        # Generate tenant-specific key
        resp = await api_client.post(f"/tenant/{tenant_id}/api-key")
        assert resp.status_code == 200

        # Global token (from GMP_API_TOKEN env var) should still work
        # api_client fixture uses the global token
        resp = await api_client.get(f"/messages?tenant_id={tenant_id}")
        assert resp.status_code == 200, "Global token should access all tenant resources"

    async def test_invalid_token_rejected(
        self, api_client, setup_test_tenants
    ):
        """Invalid/unknown tokens should be rejected with 401."""
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                "/messages?tenant_id=test-tenant-1",
                headers={"X-API-Token": "invalid-token-xyz"}
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
        1. Create tenant and generate key A
        2. Generate new key B (invalidates A)
        3. Verify key A no longer works
        4. Verify key B works
        """
        ts = int(time.time())
        tenant_id = f"auth-rotate-{ts}"

        # Create tenant
        resp = await api_client.post("/tenant", json={
            "id": tenant_id,
            "name": f"Auth Rotate Test {ts}",
        })
        assert resp.status_code in (200, 201)

        # Generate key A
        resp = await api_client.post(f"/tenant/{tenant_id}/api-key")
        assert resp.status_code == 200
        token_a = resp.json()["api_key"]

        # Verify key A works
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                f"/messages?tenant_id={tenant_id}",
                headers={"X-API-Token": token_a}
            )
            assert resp.status_code == 200, "Token A should work initially"

        # Generate key B (rotates, invalidates A)
        resp = await api_client.post(f"/tenant/{tenant_id}/api-key")
        assert resp.status_code == 200
        token_b = resp.json()["api_key"]

        # Verify key A no longer works
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                f"/messages?tenant_id={tenant_id}",
                headers={"X-API-Token": token_a}
            )
            assert resp.status_code in (401, 403), "Old token A should be rejected after rotation"

            # Verify key B works
            resp = await client.get(
                f"/messages?tenant_id={tenant_id}",
                headers={"X-API-Token": token_b}
            )
            assert resp.status_code == 200, "New token B should work after rotation"

    async def test_token_revocation(
        self, api_client, setup_test_tenants
    ):
        """Revoking a token should invalidate it."""
        ts = int(time.time())
        tenant_id = f"auth-revoke-{ts}"

        # Create tenant
        resp = await api_client.post("/tenant", json={
            "id": tenant_id,
            "name": f"Auth Revoke Test {ts}",
        })
        assert resp.status_code in (200, 201)

        # Generate API key
        resp = await api_client.post(f"/tenant/{tenant_id}/api-key")
        assert resp.status_code == 200
        tenant_token = resp.json()["api_key"]

        # Verify token works
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                f"/messages?tenant_id={tenant_id}",
                headers={"X-API-Token": tenant_token}
            )
            assert resp.status_code == 200, "Token should work before revocation"

        # Revoke the token
        resp = await api_client.delete(f"/tenant/{tenant_id}/api-key")
        assert resp.status_code == 200, "Token revocation should succeed"

        # Verify token no longer works
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                f"/messages?tenant_id={tenant_id}",
                headers={"X-API-Token": tenant_token}
            )
            assert resp.status_code == 401, "Revoked token should be rejected"

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

        # Create tenant
        resp = await api_client.post("/tenant", json={
            "id": tenant_id,
            "name": f"Auth Scope Test {ts}",
        })
        assert resp.status_code in (200, 201)

        # Generate API key
        resp = await api_client.post(f"/tenant/{tenant_id}/api-key")
        assert resp.status_code == 200
        tenant_token = resp.json()["api_key"]

        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            headers = {"X-API-Token": tenant_token}

            # Should be able to create account in own tenant
            account = {
                "id": f"scope-account-{ts}",
                "tenant_id": tenant_id,
                "host": "localhost",
                "port": 1025,
                "use_tls": False,
            }
            resp = await client.post("/account", json=account, headers=headers)
            assert resp.status_code in (200, 201), "Should create account in own tenant"

            # Should NOT be able to create account in another tenant
            account_other = {
                "id": f"scope-account-other-{ts}",
                "tenant_id": "test-tenant-1",  # different tenant
                "host": "localhost",
                "port": 1025,
                "use_tls": False,
            }
            resp = await client.post("/account", json=account_other, headers=headers)
            assert resp.status_code in (401, 403), \
                "Should NOT create account in other tenant"
