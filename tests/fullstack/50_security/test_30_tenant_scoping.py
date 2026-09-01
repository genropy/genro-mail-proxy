# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Tenant scoping of the read and write routes, from outside the proxy.

The behaviours these tests carry came from tests/unit/10_api/test_00_api.py,
which asserted them against a FastAPI TestClient and a dummy service. Here
they are asserted over HTTP against the running proxy and a real database,
so the 0.7.7 transport swap cannot change them unnoticed.

Two halves:

* ``tenant_id`` is a required parameter, not an optional filter — omitting it
  is a 422, never a query that quietly spans every tenant (issues #28, #31).
* what a tenant asks for is what a tenant gets: no row of another tenant in
  the answer, and no write reaching another tenant's data.
"""

from __future__ import annotations

import time

import pytest

from tests import api_routes

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestTenantIdIsRequired:
    """Every scoped route rejects the request that omits tenant_id."""

    async def test_accounts_requires_tenant_id(self, api_client):
        resp = await api_client.get(api_routes.accounts())
        assert resp.status_code == 422

    async def test_messages_requires_tenant_id(self, api_client):
        resp = await api_client.get(api_routes.messages())
        assert resp.status_code == 422

    async def test_delete_messages_requires_tenant_id(self, api_client):
        resp = await api_client.post(
            api_routes.delete_messages(), json={"ids": ["msg-1"]}
        )
        assert resp.status_code == 422

    async def test_cleanup_messages_requires_tenant_id(self, api_client):
        resp = await api_client.post(
            api_routes.cleanup_messages(), json={"older_than_seconds": 3600}
        )
        assert resp.status_code == 422

    async def test_delete_account_requires_tenant_id(self, api_client):
        resp = await api_client.delete(api_routes.account("acc-123"))
        assert resp.status_code == 422


class TestCrossTenantReads:
    """A tenant's own lists carry nothing belonging to the other tenant."""

    async def test_accounts_carry_no_other_tenant(
        self, api_client, setup_test_tenants
    ):
        resp = await api_client.get(api_routes.accounts("test-tenant-1"))
        assert resp.status_code == 200
        accounts = resp.json()["accounts"]

        assert accounts, "test-tenant-1 has no account, the assertion proves nothing"
        foreign = [a for a in accounts if a.get("tenant_id") != "test-tenant-1"]
        assert foreign == [], f"cross-tenant leak in /accounts: {foreign}"

    async def test_messages_carry_no_other_tenant(
        self, api_client, setup_test_tenants
    ):
        ts = int(time.time())
        message = {
            "id": f"scoping-msg-{ts}",
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Tenant scoping",
            "body": "Body",
        }
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": [message]}
        )
        assert resp.status_code == 200

        resp = await api_client.get(
            api_routes.messages("test-tenant-1", include_history=True)
        )
        assert resp.status_code == 200
        messages = resp.json()["messages"]

        assert messages, "test-tenant-1 has no message, the assertion proves nothing"
        foreign = [m for m in messages if m.get("tenant_id") != "test-tenant-1"]
        assert foreign == [], f"cross-tenant leak in /messages: {foreign}"

    async def test_unknown_tenant_reads_empty(self, api_client):
        unknown = f"no-such-tenant-{int(time.time())}"

        resp = await api_client.get(api_routes.accounts(unknown))
        assert resp.status_code == 200
        assert resp.json()["accounts"] == []

        resp = await api_client.get(api_routes.messages(unknown))
        assert resp.status_code == 200
        assert resp.json()["messages"] == []


class TestCrossTenantWrites:
    """A write scoped to one tenant does not reach the other tenant's data."""

    async def test_delete_account_of_other_tenant_refused(
        self, api_client, setup_test_tenants
    ):
        resp = await api_client.delete(
            api_routes.account("test-account-2", tenant_id="test-tenant-1")
        )
        assert resp.status_code == 400
        assert "not owned by tenant" in resp.json()["detail"]

        # The account is still there, on its own tenant.
        resp = await api_client.get(api_routes.accounts("test-tenant-2"))
        assert resp.status_code == 200
        ids = [a["id"] for a in resp.json()["accounts"]]
        assert "test-account-2" in ids

    async def test_delete_messages_of_other_tenant_reports_unauthorized(
        self, api_client, setup_test_tenants
    ):
        ts = int(time.time())
        foreign_id = f"scoping-foreign-{ts}"
        message = {
            "id": foreign_id,
            "tenant_id": "test-tenant-2",
            "account_id": "test-account-2",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Owned by tenant 2",
            "body": "Body",
        }
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": [message]}
        )
        assert resp.status_code == 200

        resp = await api_client.get(
            api_routes.messages("test-tenant-2", include_history=True)
        )
        pk = next(
            m["pk"] for m in resp.json()["messages"] if m.get("id") == foreign_id
        )

        # test-tenant-1 asks for a message that belongs to test-tenant-2.
        resp = await api_client.post(
            api_routes.delete_messages(tenant_id="test-tenant-1"),
            json={"ids": [str(pk)]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["removed"] == 0
        assert str(pk) in [str(x) for x in data.get("unauthorized", [])]

        # And the message survives on its own tenant.
        resp = await api_client.get(
            api_routes.messages("test-tenant-2", include_history=True)
        )
        assert foreign_id in [m.get("id") for m in resp.json()["messages"]]
