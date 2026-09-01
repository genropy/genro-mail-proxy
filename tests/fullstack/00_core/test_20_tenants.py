# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Tenant management tests."""

from __future__ import annotations

import time

import httpx
import pytest

from tests import api_routes
from tests.fullstack.helpers import MAILHOG_TENANT1_SMTP, MAILPROXY_URL

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestTenantManagement:
    """Test tenant CRUD operations."""

    async def test_create_tenant(self, api_client):
        """Can create a new tenant."""
        tenant_data = {
            "id": f"crud-tenant-{int(time.time())}",
            "name": "CRUD Test Tenant",
            "client_base_url": "http://example.com",
            "active": True,
        }
        resp = await api_client.post(api_routes.TENANT, json=tenant_data)
        assert resp.status_code in (200, 201)

    async def test_list_tenants(self, api_client, setup_test_tenants):
        """Can list all tenants."""
        resp = await api_client.get(api_routes.tenants())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2  # At least our test tenants

    async def test_get_tenant_details(self, api_client, setup_test_tenants):
        """Can get tenant details."""
        resp = await api_client.get(api_routes.tenant("test-tenant-1"))
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("id") == "test-tenant-1"

    async def test_update_tenant(self, api_client, setup_test_tenants):
        """Can update tenant details."""
        update_data = {"name": "Updated Tenant 1 Name"}
        resp = await api_client.put(api_routes.tenant("test-tenant-1"), json=update_data)
        assert resp.status_code == 200

        # Verify update
        resp = await api_client.get(api_routes.tenant("test-tenant-1"))
        data = resp.json()
        assert data.get("name") == "Updated Tenant 1 Name"


class TestTenantDeletion:
    """DELETE /tenant/{tenant_id} — admin only, and irreversible.

    Every test here creates the tenant it deletes. ``test-tenant-1`` and
    ``test-tenant-2`` are never passed to this route: it removes the tenant's
    SMTP accounts and queued messages along with it, and the whole suite is
    built on those two.
    """

    async def test_delete_tenant_removes_it(self, api_client):
        ts = int(time.time())
        tenant_id = f"delete-tenant-{ts}"
        resp = await api_client.post(
            api_routes.TENANT,
            json={"id": tenant_id, "name": f"Delete Test Tenant {ts}"},
        )
        assert resp.status_code in (200, 201), resp.text

        resp = await api_client.get(api_routes.tenant(tenant_id))
        assert resp.status_code == 200, "the tenant to delete was not created"

        resp = await api_client.delete(api_routes.tenant(tenant_id))
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = await api_client.get(api_routes.tenant(tenant_id))
        assert resp.status_code == 404

        resp = await api_client.get(api_routes.tenants())
        assert tenant_id not in [t["id"] for t in resp.json()["tenants"]]

    async def test_delete_tenant_takes_its_accounts_with_it(self, api_client):
        """What the Genropy client relies on: one call clears the tenant."""
        ts = int(time.time())
        tenant_id = f"delete-cascade-{ts}"
        account_id = f"delete-cascade-account-{ts}"

        resp = await api_client.post(
            api_routes.TENANT,
            json={"id": tenant_id, "name": f"Delete Cascade Tenant {ts}"},
        )
        assert resp.status_code in (200, 201), resp.text
        resp = await api_client.post(
            api_routes.ACCOUNT,
            json={
                "id": account_id,
                "tenant_id": tenant_id,
                "host": MAILHOG_TENANT1_SMTP[0],
                "port": MAILHOG_TENANT1_SMTP[1],
                "use_tls": False,
            },
        )
        assert resp.status_code in (200, 201), resp.text

        resp = await api_client.get(api_routes.accounts(tenant_id=tenant_id))
        assert account_id in [a["id"] for a in resp.json()["accounts"]], (
            "the account to cascade was not created"
        )

        resp = await api_client.delete(api_routes.tenant(tenant_id))
        assert resp.status_code == 200

        resp = await api_client.get(api_routes.accounts(tenant_id=tenant_id))
        assert resp.status_code == 200
        assert resp.json()["accounts"] == []

    async def test_delete_unknown_tenant_returns_404(self, api_client):
        resp = await api_client.delete(
            api_routes.tenant(f"no-such-tenant-{int(time.time())}")
        )
        assert resp.status_code == 404

    async def test_delete_tenant_requires_the_admin_token(self, api_client):
        """An unauthenticated delete is refused, and the tenant survives it."""
        ts = int(time.time())
        tenant_id = f"delete-guard-{ts}"
        resp = await api_client.post(
            api_routes.TENANT,
            json={"id": tenant_id, "name": f"Delete Guard Tenant {ts}"},
        )
        assert resp.status_code in (200, 201), resp.text
        try:
            async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
                resp = await client.delete(api_routes.tenant(tenant_id))
                assert resp.status_code == 401

            resp = await api_client.get(api_routes.tenant(tenant_id))
            assert resp.status_code == 200, "the unauthenticated delete went through"
        finally:
            await api_client.delete(api_routes.tenant(tenant_id))
