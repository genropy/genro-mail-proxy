# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Account management tests."""

from __future__ import annotations

import time

import pytest

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestAccountManagement:
    """Test SMTP account operations."""

    async def test_list_accounts(self, api_client, setup_test_tenants):
        """Can list all accounts."""
        resp = await api_client.get("/accounts?tenant_id=test-tenant-1")
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
        resp = await api_client.post("/account", json=account_data)
        assert resp.status_code in (200, 201)
