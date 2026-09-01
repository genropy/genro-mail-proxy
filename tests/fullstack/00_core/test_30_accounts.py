# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Account management tests."""

from __future__ import annotations

import time

import pytest

from tests import api_routes
from tests.fullstack.helpers import MAILHOG_TENANT1_SMTP

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestAccountManagement:
    """Test SMTP account operations."""

    async def test_list_accounts(self, api_client, setup_test_tenants):
        """Can list all accounts."""
        resp = await api_client.get(api_routes.accounts(tenant_id="test-tenant-1"))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 2

    async def test_create_account_with_rate_limits(self, api_client, setup_test_tenants):
        """Can create account with rate limits."""
        account_data = {
            "id": f"rate-limited-account-{int(time.time())}",
            "tenant_id": "test-tenant-1",
            "host": "mailhog-tenant1",
            "port": 1025,
            "use_tls": False,
            "limit_per_minute": 10,
            "limit_per_hour": 100,
            "limit_per_day": 500,
        }
        resp = await api_client.post(api_routes.ACCOUNT, json=account_data)
        assert resp.status_code in (200, 201)


class TestAccountDeletion:
    """DELETE /account/{account_id} — the deletion that succeeds.

    The refusals live in ``50_security/test_30_tenant_scoping.py``: 422 with no
    ``tenant_id``, 400 on an account belonging to another tenant. What was left
    unexercised is the call the Genropy client actually makes.
    """

    async def test_delete_account_removes_it_from_the_tenant(
        self, api_client, setup_test_tenants
    ):
        ts = int(time.time())
        account_id = f"delete-account-{ts}"
        resp = await api_client.post(
            api_routes.ACCOUNT,
            json={
                "id": account_id,
                "tenant_id": "test-tenant-1",
                "host": MAILHOG_TENANT1_SMTP[0],
                "port": MAILHOG_TENANT1_SMTP[1],
                "use_tls": False,
            },
        )
        assert resp.status_code in (200, 201), resp.text

        resp = await api_client.get(api_routes.accounts(tenant_id="test-tenant-1"))
        assert account_id in [a["id"] for a in resp.json()["accounts"]], (
            "the account to delete was not created"
        )

        resp = await api_client.delete(
            api_routes.account(account_id, tenant_id="test-tenant-1")
        )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        resp = await api_client.get(api_routes.accounts(tenant_id="test-tenant-1"))
        assert account_id not in [a["id"] for a in resp.json()["accounts"]]

    async def test_delete_unknown_account_is_refused(
        self, api_client, setup_test_tenants
    ):
        """An id nobody owns is answered like an id owned by somebody else: 400."""
        resp = await api_client.delete(
            api_routes.account(
                f"no-such-account-{int(time.time())}", tenant_id="test-tenant-1"
            )
        )
        assert resp.status_code == 400
        assert "not owned by tenant" in resp.json()["detail"]
