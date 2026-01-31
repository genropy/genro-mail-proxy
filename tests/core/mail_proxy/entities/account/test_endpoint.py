# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for AccountEndpoint - all endpoint methods."""

import pytest

from core.mail_proxy.entities.account import AccountEndpoint
from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with all tables initialized."""
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
    await proxy.init()
    # Create tenant for FK constraint
    await proxy.db.table("tenants").add({"id": "t1", "name": "Test Tenant"})
    yield proxy.db
    await proxy.close()


@pytest.fixture
async def endpoint(db):
    """Create AccountEndpoint with real table."""
    return AccountEndpoint(db.table("accounts"))


class TestAccountEndpointAdd:
    """Tests for AccountEndpoint.add() method."""

    async def test_add_minimal(self, db, endpoint):
        """Add account with minimal required fields."""
        result = await endpoint.add(
            id="smtp1",
            tenant_id="t1",
            host="smtp.example.com",
            port=587,
        )
        assert result["id"] == "smtp1"
        assert result["tenant_id"] == "t1"
        assert result["host"] == "smtp.example.com"
        assert result["port"] == 587

    async def test_add_with_credentials(self, db, endpoint):
        """Add account with user/password."""
        result = await endpoint.add(
            id="smtp2",
            tenant_id="t1",
            host="smtp.example.com",
            port=587,
            user="myuser",
            password="mypass",
        )
        assert result["user"] == "myuser"

    async def test_add_with_tls(self, db, endpoint):
        """Add account with use_tls setting."""
        result = await endpoint.add(
            id="smtp3",
            tenant_id="t1",
            host="smtp.example.com",
            port=465,
            use_tls=True,
        )
        assert result["use_tls"] is True

    async def test_add_with_rate_limits(self, db, endpoint):
        """Add account with rate limits."""
        result = await endpoint.add(
            id="smtp4",
            tenant_id="t1",
            host="smtp.example.com",
            port=587,
            limit_per_minute=10,
            limit_per_hour=100,
            limit_per_day=1000,
            limit_behavior="reject",
        )
        assert result["limit_per_minute"] == 10
        assert result["limit_per_hour"] == 100
        assert result["limit_per_day"] == 1000
        assert result["limit_behavior"] == "reject"

    async def test_add_with_ttl_and_batch(self, db, endpoint):
        """Add account with ttl and batch_size."""
        result = await endpoint.add(
            id="smtp5",
            tenant_id="t1",
            host="smtp.example.com",
            port=587,
            ttl=600,
            batch_size=50,
        )
        assert result["ttl"] == 600
        assert result["batch_size"] == 50

    async def test_add_updates_existing(self, db, endpoint):
        """Add with same id updates existing account."""
        await endpoint.add(
            id="smtp1",
            tenant_id="t1",
            host="old.example.com",
            port=25,
        )
        result = await endpoint.add(
            id="smtp1",
            tenant_id="t1",
            host="new.example.com",
            port=587,
        )
        assert result["host"] == "new.example.com"
        assert result["port"] == 587


class TestAccountEndpointGet:
    """Tests for AccountEndpoint.get() method."""

    async def test_get_existing(self, db, endpoint):
        """Get an existing account."""
        await endpoint.add(
            id="smtp1",
            tenant_id="t1",
            host="smtp.example.com",
            port=587,
        )
        result = await endpoint.get(tenant_id="t1", account_id="smtp1")
        assert result["id"] == "smtp1"
        assert result["host"] == "smtp.example.com"

    async def test_get_nonexistent_raises(self, db, endpoint):
        """Get non-existent account raises ValueError."""
        with pytest.raises(ValueError, match="not found"):
            await endpoint.get(tenant_id="t1", account_id="nonexistent")

    async def test_get_wrong_tenant_raises(self, db, endpoint):
        """Get account with wrong tenant raises."""
        await endpoint.add(
            id="smtp1",
            tenant_id="t1",
            host="smtp.example.com",
            port=587,
        )
        with pytest.raises(ValueError, match="not found"):
            await endpoint.get(tenant_id="wrong", account_id="smtp1")


class TestAccountEndpointList:
    """Tests for AccountEndpoint.list() method."""

    async def test_list_empty(self, db, endpoint):
        """List returns empty when no accounts."""
        result = await endpoint.list(tenant_id="t1")
        assert result == []

    async def test_list_multiple(self, db, endpoint):
        """List returns all accounts for tenant."""
        await endpoint.add(id="a1", tenant_id="t1", host="h1", port=25)
        await endpoint.add(id="a2", tenant_id="t1", host="h2", port=25)
        await endpoint.add(id="a3", tenant_id="t1", host="h3", port=25)

        result = await endpoint.list(tenant_id="t1")
        assert len(result) == 3
        ids = [a["id"] for a in result]
        assert set(ids) == {"a1", "a2", "a3"}

    async def test_list_filters_by_tenant(self, db, endpoint):
        """List only returns accounts for specified tenant."""
        await endpoint.add(id="a1", tenant_id="t1", host="h1", port=25)
        # Create another tenant
        await db.table("tenants").insert({"id": "t2", "name": "Tenant 2", "active": 1})
        await endpoint.add(id="a2", tenant_id="t2", host="h2", port=25)

        result = await endpoint.list(tenant_id="t1")
        assert len(result) == 1
        assert result[0]["id"] == "a1"


class TestAccountEndpointDelete:
    """Tests for AccountEndpoint.delete() method."""

    async def test_delete_existing(self, db, endpoint):
        """Delete an existing account."""
        await endpoint.add(id="smtp1", tenant_id="t1", host="h", port=25)
        await endpoint.delete(tenant_id="t1", account_id="smtp1")

        with pytest.raises(ValueError, match="not found"):
            await endpoint.get(tenant_id="t1", account_id="smtp1")

    async def test_delete_nonexistent_no_error(self, db, endpoint):
        """Delete non-existent account doesn't raise."""
        # Should not raise
        await endpoint.delete(tenant_id="t1", account_id="nonexistent")

    async def test_delete_wrong_tenant_no_effect(self, db, endpoint):
        """Delete with wrong tenant doesn't affect account."""
        await endpoint.add(id="smtp1", tenant_id="t1", host="h", port=25)
        await endpoint.delete(tenant_id="wrong", account_id="smtp1")

        # Account still exists
        result = await endpoint.get(tenant_id="t1", account_id="smtp1")
        assert result["id"] == "smtp1"


class TestAccountEndpointDefaults:
    """Tests for default values in AccountEndpoint.add()."""

    async def test_default_use_tls(self, db, endpoint):
        """Default use_tls is True."""
        result = await endpoint.add(
            id="smtp1",
            tenant_id="t1",
            host="smtp.example.com",
            port=587,
        )
        assert result["use_tls"] is True

    async def test_default_ttl(self, db, endpoint):
        """Default ttl is 300."""
        result = await endpoint.add(
            id="smtp1",
            tenant_id="t1",
            host="smtp.example.com",
            port=587,
        )
        assert result["ttl"] == 300

    async def test_default_limit_behavior(self, db, endpoint):
        """Default limit_behavior is 'defer'."""
        result = await endpoint.add(
            id="smtp1",
            tenant_id="t1",
            host="smtp.example.com",
            port=587,
        )
        assert result["limit_behavior"] == "defer"
