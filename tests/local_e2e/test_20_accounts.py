# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The three SMTP-account routes, over HTTP against the in-process app.

Creation, the tenant-scoped list, and deletion. ``tenant_id`` is a required
query parameter on the two scoped routes, not an optional filter: omitting it
is a 422, never a query that quietly spans every tenant.
"""

from __future__ import annotations

import pytest

from tests import api_routes

pytestmark = pytest.mark.asyncio


class TestAccountCreation:
    """POST /account — create, and upsert on a second call."""

    async def test_create_answers_ok(self, api_client, throwaway_tenant):
        resp = await api_client.post(
            api_routes.ACCOUNT,
            json={
                "id": f"acc-{throwaway_tenant}",
                "tenant_id": throwaway_tenant,
                "host": "127.0.0.1",
                "port": 9,
                "use_tls": False,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

    async def test_create_stores_the_rate_limits(self, api_client, throwaway_tenant):
        account_id = f"acc-limits-{throwaway_tenant}"
        resp = await api_client.post(
            api_routes.ACCOUNT,
            json={
                "id": account_id,
                "tenant_id": throwaway_tenant,
                "host": "127.0.0.1",
                "port": 9,
                "limit_per_minute": 5,
                "limit_behavior": "defer",
            },
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.accounts(throwaway_tenant))
        stored = {a["id"]: a for a in resp.json()["accounts"]}[account_id]
        assert stored["limit_per_minute"] == 5
        assert stored["limit_behavior"] == "defer"

    async def test_create_without_a_host_is_rejected(self, api_client, throwaway_tenant):
        resp = await api_client.post(
            api_routes.ACCOUNT,
            json={"id": "acc-no-host", "tenant_id": throwaway_tenant, "port": 9},
        )
        assert resp.status_code == 422


class TestAccountList:
    """GET /accounts — scoped to one tenant, and only to that one."""

    async def test_list_carries_the_tenant_account(self, api_client, local_tenants):
        resp = await api_client.get(api_routes.accounts(local_tenants["tenant1"]))
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

        accounts = body["accounts"]
        assert accounts, "the tenant has no account, the assertion proves nothing"
        assert local_tenants["account1"] in [a["id"] for a in accounts]

    async def test_list_carries_no_other_tenant(self, api_client, local_tenants):
        resp = await api_client.get(api_routes.accounts(local_tenants["tenant1"]))
        accounts = resp.json()["accounts"]

        assert accounts, "the tenant has no account, the assertion proves nothing"
        foreign = [a for a in accounts if a["tenant_id"] != local_tenants["tenant1"]]
        assert foreign == [], f"cross-tenant leak in /accounts: {foreign}"

    async def test_list_never_returns_the_password(self, api_client, throwaway_tenant):
        account_id = f"acc-secret-{throwaway_tenant}"
        resp = await api_client.post(
            api_routes.ACCOUNT,
            json={
                "id": account_id,
                "tenant_id": throwaway_tenant,
                "host": "127.0.0.1",
                "port": 9,
                "user": "someone",
                "password": "the-smtp-password",
            },
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.accounts(throwaway_tenant))
        assert "the-smtp-password" not in resp.text

    async def test_list_of_an_unknown_tenant_is_empty(self, api_client):
        resp = await api_client.get(api_routes.accounts("no-such-tenant"))
        assert resp.status_code == 200
        assert resp.json()["accounts"] == []

    async def test_list_requires_tenant_id(self, api_client):
        resp = await api_client.get(api_routes.accounts())
        assert resp.status_code == 422


class TestAccountDeletion:
    """DELETE /account/{account_id} — scoped by the tenant that owns it."""

    async def test_delete_removes_the_account(self, api_client, throwaway_tenant):
        account_id = f"acc-doomed-{throwaway_tenant}"
        resp = await api_client.post(
            api_routes.ACCOUNT,
            json={
                "id": account_id,
                "tenant_id": throwaway_tenant,
                "host": "127.0.0.1",
                "port": 9,
            },
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.delete(
            api_routes.account(account_id, tenant_id=throwaway_tenant)
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = await api_client.get(api_routes.accounts(throwaway_tenant))
        assert account_id not in [a["id"] for a in resp.json()["accounts"]]

    async def test_delete_of_another_tenant_account_is_refused(
        self, api_client, local_tenants
    ):
        resp = await api_client.delete(
            api_routes.account(
                local_tenants["account1"], tenant_id=local_tenants["tenant2"]
            )
        )
        assert resp.status_code == 400

        # The account is still there: the refusal deleted nothing.
        resp = await api_client.get(api_routes.accounts(local_tenants["tenant1"]))
        assert local_tenants["account1"] in [a["id"] for a in resp.json()["accounts"]]

    async def test_delete_requires_tenant_id(self, api_client):
        resp = await api_client.delete(api_routes.account("acc-123"))
        assert resp.status_code == 422
