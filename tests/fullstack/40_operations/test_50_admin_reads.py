# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The two admin reads nothing else in the suite asserts.

``GET /tenants/sync-status`` is the monitoring view of the report cycle:
per tenant, when it was last synced, whether a sync is due, whether it asked
not to be disturbed. ``GET /instance`` is the singleton configuration.

Both are read-only, and both were unexercised: ``/instance`` was reached only
by ``configure_bounce_receiver``, in PUT, never in GET.
"""

from __future__ import annotations

import time

import httpx
import pytest

from tests import api_routes
from tests.fullstack.helpers import CLIENT_TENANT1_URL, MAILPROXY_URL

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestTenantsSyncStatus:
    """GET /tenants/sync-status — the sync flags of every tenant."""

    async def test_every_tenant_carries_its_sync_flags(
        self, api_client, setup_test_tenants
    ):
        """Both test tenants appear, with their flags and their client address."""
        resp = await api_client.get(api_routes.TENANTS_SYNC_STATUS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["sync_interval_seconds"] == 300

        by_id = {t["id"]: t for t in data["tenants"]}
        assert "test-tenant-1" in by_id
        assert "test-tenant-2" in by_id

        for tenant_id in ("test-tenant-1", "test-tenant-2"):
            entry = by_id[tenant_id]
            assert entry["active"] is True
            assert isinstance(entry["next_sync_due"], bool)
            assert isinstance(entry["in_dnd"], bool)

        # The view reports the address the proxy would call, not a placeholder.
        assert by_id["test-tenant-1"]["client_base_url"] == CLIENT_TENANT1_URL

    async def test_a_deleted_tenant_leaves_the_view(self, api_client):
        """The view reads the tenant table on each call, not a cache."""
        ts = int(time.time())
        tenant_id = f"syncstatus-tenant-{ts}"
        resp = await api_client.post(
            api_routes.TENANT,
            json={"id": tenant_id, "name": f"Sync Status Tenant {ts}"},
        )
        assert resp.status_code in (200, 201), resp.text

        resp = await api_client.get(api_routes.TENANTS_SYNC_STATUS)
        assert tenant_id in [t["id"] for t in resp.json()["tenants"]]

        resp = await api_client.delete(api_routes.tenant(tenant_id))
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.TENANTS_SYNC_STATUS)
        assert tenant_id not in [t["id"] for t in resp.json()["tenants"]]

    async def test_sync_status_requires_the_admin_token(self):
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(api_routes.TENANTS_SYNC_STATUS)
            assert resp.status_code == 401


class TestInstanceConfiguration:
    """GET /instance — the singleton row, minus what must not travel."""

    async def test_instance_carries_the_bounce_configuration(self, api_client):
        resp = await api_client.get(api_routes.INSTANCE)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == 1
        assert isinstance(data["bounce_enabled"], bool)
        assert "bounce_imap_host" in data
        assert "bounce_imap_port" in data

    async def test_instance_never_returns_the_bounce_password(self, api_client):
        """``get_instance`` drops ``bounce_imap_password`` before answering."""
        resp = await api_client.get(api_routes.INSTANCE)
        assert resp.status_code == 200
        assert "bounce_imap_password" not in resp.json()

    async def test_instance_requires_the_admin_token(self):
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(api_routes.INSTANCE)
            assert resp.status_code == 401

    async def test_a_tenant_key_is_refused_on_instance(self, api_client):
        """A valid tenant key answers 403: the token is real, the scope is not."""
        ts = int(time.time())
        tenant_id = f"instance-guard-{ts}"
        resp = await api_client.post(
            api_routes.TENANT,
            json={"id": tenant_id, "name": f"Instance Guard Tenant {ts}"},
        )
        assert resp.status_code in (200, 201), resp.text
        try:
            resp = await api_client.post(api_routes.tenant_api_key(tenant_id))
            assert resp.status_code == 200, resp.text
            tenant_token = resp.json()["api_key"]

            async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
                resp = await client.get(
                    api_routes.INSTANCE, headers={"X-API-Token": tenant_token}
                )
                assert resp.status_code == 403
        finally:
            await api_client.delete(api_routes.tenant(tenant_id))
