# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Tenant management tests."""

from __future__ import annotations

import time

import pytest

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
        resp = await api_client.post("/tenant", json=tenant_data)
        assert resp.status_code in (200, 201)

    async def test_list_tenants(self, api_client, setup_test_tenants):
        """Can list all tenants."""
        resp = await api_client.get("/tenants")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2  # At least our test tenants

    async def test_get_tenant_details(self, api_client, setup_test_tenants):
        """Can get tenant details."""
        resp = await api_client.get("/tenant/test-tenant-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("id") == "test-tenant-1"

    async def test_update_tenant(self, api_client, setup_test_tenants):
        """Can update tenant details."""
        update_data = {"name": "Updated Tenant 1 Name"}
        resp = await api_client.put("/tenant/test-tenant-1", json=update_data)
        assert resp.status_code == 200

        # Verify update
        resp = await api_client.get("/tenant/test-tenant-1")
        data = resp.json()
        assert data.get("name") == "Updated Tenant 1 Name"
