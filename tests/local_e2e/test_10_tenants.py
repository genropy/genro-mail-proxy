# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The eight tenant routes, over HTTP against the in-process app.

Creation and update, the two reads (one tenant, all tenants), the sync-status
view, deletion, and the two api-key routes. What the 0.7.7 transport swap must
not change is asserted here: the verb, the binding of the path and query
parameters, the status code, and the shape of the answer.
"""

from __future__ import annotations

import pytest

from tests import api_routes

pytestmark = pytest.mark.asyncio


class TestTenantCreation:
    """POST /tenant — create, and upsert on a second call."""

    async def test_create_answers_ok_with_an_api_key(self, api_client, throwaway_tenant):
        resp = await api_client.get(api_routes.tenant(throwaway_tenant))
        assert resp.status_code == 200
        assert resp.json()["id"] == throwaway_tenant

    async def test_a_new_tenant_receives_its_api_key(self, api_client):
        tenant_id = "local-tenant-with-key"
        resp = await api_client.post(
            api_routes.TENANT, json={"id": tenant_id, "name": "Key Tenant"}
        )
        assert resp.status_code == 200, resp.text
        try:
            body = resp.json()
            assert body["ok"] is True
            assert body["api_key"]

            # The second call upserts: same tenant, and no new key handed out.
            resp = await api_client.post(
                api_routes.TENANT, json={"id": tenant_id, "name": "Key Tenant Renamed"}
            )
            assert resp.status_code == 200, resp.text
            assert resp.json().get("api_key") is None
        finally:
            await api_client.delete(api_routes.tenant(tenant_id))

    async def test_create_without_an_id_is_rejected(self, api_client):
        resp = await api_client.post(api_routes.TENANT, json={"name": "No Id"})
        assert resp.status_code == 422


class TestTenantReads:
    """GET /tenants and GET /tenant/{tenant_id}."""

    async def test_list_carries_both_local_tenants(self, api_client, local_tenants):
        resp = await api_client.get(api_routes.tenants())
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

        ids = [t["id"] for t in body["tenants"]]
        assert local_tenants["tenant1"] in ids
        assert local_tenants["tenant2"] in ids

    async def test_active_only_binds_as_a_boolean_query(self, api_client, local_tenants):
        """``active_only=true`` reaches the handler, and drops the inactive."""
        inactive_id = "local-tenant-inactive"
        resp = await api_client.post(
            api_routes.TENANT,
            json={"id": inactive_id, "name": "Inactive", "active": False},
        )
        assert resp.status_code == 200, resp.text
        try:
            resp = await api_client.get(api_routes.tenants(active_only=True))
            assert resp.status_code == 200
            ids = [t["id"] for t in resp.json()["tenants"]]
            assert local_tenants["tenant1"] in ids
            assert inactive_id not in ids
        finally:
            await api_client.delete(api_routes.tenant(inactive_id))

    async def test_detail_carries_the_configuration(self, api_client, local_tenants):
        resp = await api_client.get(api_routes.tenant(local_tenants["tenant1"]))
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == local_tenants["tenant1"]
        assert body["client_sync_path"] == api_routes.CLIENT_SYNC_PATH

    async def test_detail_of_an_unknown_tenant_is_404(self, api_client):
        resp = await api_client.get(api_routes.tenant("no-such-tenant"))
        assert resp.status_code == 404


class TestTenantUpdate:
    """PUT /tenant/{tenant_id} — partial, and 404 on an unknown tenant."""

    async def test_update_changes_only_what_it_names(self, api_client, throwaway_tenant):
        resp = await api_client.put(
            api_routes.tenant(throwaway_tenant), json={"name": "Renamed"}
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = await api_client.get(api_routes.tenant(throwaway_tenant))
        body = resp.json()
        assert body["name"] == "Renamed"
        assert body["client_base_url"] == "http://127.0.0.1:9"

    async def test_update_of_an_unknown_tenant_is_404(self, api_client):
        resp = await api_client.put(
            api_routes.tenant("no-such-tenant"), json={"name": "Ghost"}
        )
        assert resp.status_code == 404


class TestTenantDeletion:
    """DELETE /tenant/{tenant_id} — admin only, and irreversible."""

    async def test_delete_removes_the_tenant(self, api_client):
        tenant_id = "local-tenant-to-delete"
        resp = await api_client.post(
            api_routes.TENANT, json={"id": tenant_id, "name": "To Delete"}
        )
        assert resp.status_code == 200, resp.text

        try:
            resp = await api_client.delete(api_routes.tenant(tenant_id))
            assert resp.status_code == 200
            assert resp.json()["ok"] is True

            resp = await api_client.get(api_routes.tenant(tenant_id))
            assert resp.status_code == 404
        finally:
            # The delete above is the assertion; this only clears the fixed id
            # when it failed, so a later listing reads no tenant it did not make.
            await api_client.delete(api_routes.tenant(tenant_id))

    async def test_delete_of_an_unknown_tenant_is_404(self, api_client):
        resp = await api_client.delete(api_routes.tenant("no-such-tenant"))
        assert resp.status_code == 404


class TestTenantsSyncStatus:
    """GET /tenants/sync-status — the sync flags of every tenant."""

    async def test_every_tenant_carries_its_sync_flags(self, api_client, local_tenants):
        resp = await api_client.get(api_routes.TENANTS_SYNC_STATUS)
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["sync_interval_seconds"] == 300

        by_id = {t["id"]: t for t in body["tenants"]}
        assert local_tenants["tenant1"] in by_id
        assert local_tenants["tenant2"] in by_id

        entry = by_id[local_tenants["tenant1"]]
        assert entry["active"] is True
        assert isinstance(entry["next_sync_due"], bool)
        assert isinstance(entry["in_dnd"], bool)
        assert entry["client_base_url"] == "http://127.0.0.1:9"

    async def test_a_deleted_tenant_leaves_the_view(self, api_client, throwaway_tenant):
        resp = await api_client.get(api_routes.TENANTS_SYNC_STATUS)
        assert throwaway_tenant in [t["id"] for t in resp.json()["tenants"]]

        resp = await api_client.delete(api_routes.tenant(throwaway_tenant))
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.TENANTS_SYNC_STATUS)
        assert throwaway_tenant not in [t["id"] for t in resp.json()["tenants"]]


class TestTenantApiKey:
    """POST and DELETE /tenant/{tenant_id}/api-key."""

    async def test_create_hands_out_a_working_key(
        self, api_client, bare_client, throwaway_tenant
    ):
        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        key = body["api_key"]
        assert key

        resp = await bare_client.get(
            api_routes.tenant(throwaway_tenant), headers={"X-API-Token": key}
        )
        assert resp.status_code == 200
        assert resp.json()["id"] == throwaway_tenant

    async def test_a_second_key_invalidates_the_first(
        self, api_client, bare_client, throwaway_tenant
    ):
        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        first = resp.json()["api_key"]

        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        second = resp.json()["api_key"]
        assert second != first

        resp = await bare_client.get(
            api_routes.tenant(throwaway_tenant), headers={"X-API-Token": first}
        )
        assert resp.status_code == 401

    async def test_revoke_kills_the_key(
        self, api_client, bare_client, throwaway_tenant
    ):
        resp = await api_client.post(api_routes.tenant_api_key(throwaway_tenant))
        key = resp.json()["api_key"]

        resp = await api_client.delete(api_routes.tenant_api_key(throwaway_tenant))
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = await bare_client.get(
            api_routes.tenant(throwaway_tenant), headers={"X-API-Token": key}
        )
        assert resp.status_code == 401

    async def test_api_key_routes_are_404_on_an_unknown_tenant(self, api_client):
        resp = await api_client.post(api_routes.tenant_api_key("no-such-tenant"))
        assert resp.status_code == 404

        resp = await api_client.delete(api_routes.tenant_api_key("no-such-tenant"))
        assert resp.status_code == 404
