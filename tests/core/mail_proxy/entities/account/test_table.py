# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for AccountsTable - all table methods."""

import pytest

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


class TestAccountsTableAdd:
    """Tests for AccountsTable.add() method."""

    async def test_add_new_account(self, db):
        """Add a new SMTP account."""
        accounts = db.table("accounts")
        pk = await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })
        assert pk is not None
        assert len(pk) == 22  # Short UUID format

    async def test_add_account_with_all_fields(self, db):
        """Add account with all optional fields."""
        accounts = db.table("accounts")
        pk = await accounts.add({
            "id": "smtp2",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 465,
            "user": "testuser",
            "password": "secret",
            "use_tls": True,
            "ttl": 600,
            "batch_size": 100,
            "limit_per_minute": 10,
            "limit_per_hour": 100,
            "limit_per_day": 1000,
            "limit_behavior": "reject",
        })
        account = await accounts.get("t1", "smtp2")
        assert account["user"] == "testuser"
        assert account["use_tls"] is True
        assert account["ttl"] == 600
        assert account["limit_behavior"] == "reject"

    async def test_add_updates_existing_account(self, db):
        """Adding same account_id updates it (upsert)."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.old.com",
            "port": 25,
        })
        await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.new.com",
            "port": 587,
        })
        account = await accounts.get("t1", "smtp1")
        assert account["host"] == "smtp.new.com"
        assert account["port"] == 587

    async def test_add_use_tls_false(self, db):
        """use_tls=False is stored correctly."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "notls",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 25,
            "use_tls": False,
        })
        account = await accounts.get("t1", "notls")
        assert account["use_tls"] is False

    async def test_add_use_tls_none(self, db):
        """use_tls=None is stored as None."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "notls",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 25,
            "use_tls": None,
        })
        account = await accounts.get("t1", "notls")
        assert account["use_tls"] is None


class TestAccountsTableGet:
    """Tests for AccountsTable.get() method."""

    async def test_get_existing_account(self, db):
        """Get an existing account."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })
        account = await accounts.get("t1", "smtp1")
        assert account["id"] == "smtp1"
        assert account["tenant_id"] == "t1"
        assert account["host"] == "smtp.example.com"

    async def test_get_nonexistent_account_raises(self, db):
        """Get non-existent account raises ValueError."""
        accounts = db.table("accounts")
        with pytest.raises(ValueError, match="not found"):
            await accounts.get("t1", "nonexistent")

    async def test_get_wrong_tenant_raises(self, db):
        """Get account with wrong tenant_id raises."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })
        with pytest.raises(ValueError, match="not found"):
            await accounts.get("wrong_tenant", "smtp1")


class TestAccountsTableListAll:
    """Tests for AccountsTable.list_all() method."""

    async def test_list_all_empty(self, db):
        """List returns empty when no accounts."""
        accounts = db.table("accounts")
        result = await accounts.list_all(tenant_id="t1")
        assert result == []

    async def test_list_all_by_tenant(self, db):
        """List accounts filtered by tenant."""
        accounts = db.table("accounts")
        await accounts.add({"id": "a1", "tenant_id": "t1", "host": "h1", "port": 25})
        await accounts.add({"id": "a2", "tenant_id": "t1", "host": "h2", "port": 25})
        # Create another tenant
        await db.table("tenants").insert({"id": "t2", "name": "Tenant 2", "active": 1})
        await accounts.add({"id": "a3", "tenant_id": "t2", "host": "h3", "port": 25})

        result = await accounts.list_all(tenant_id="t1")
        assert len(result) == 2
        ids = [a["id"] for a in result]
        assert "a1" in ids
        assert "a2" in ids
        assert "a3" not in ids

    async def test_list_all_no_filter(self, db):
        """List all accounts without tenant filter."""
        accounts = db.table("accounts")
        await accounts.add({"id": "a1", "tenant_id": "t1", "host": "h1", "port": 25})
        await db.table("tenants").insert({"id": "t2", "name": "Tenant 2", "active": 1})
        await accounts.add({"id": "a2", "tenant_id": "t2", "host": "h2", "port": 25})

        result = await accounts.list_all()
        assert len(result) == 2

    async def test_list_all_ordered_by_id(self, db):
        """List returns accounts ordered by id."""
        accounts = db.table("accounts")
        await accounts.add({"id": "z", "tenant_id": "t1", "host": "h", "port": 25})
        await accounts.add({"id": "a", "tenant_id": "t1", "host": "h", "port": 25})
        await accounts.add({"id": "m", "tenant_id": "t1", "host": "h", "port": 25})

        result = await accounts.list_all(tenant_id="t1")
        ids = [a["id"] for a in result]
        assert ids == ["a", "m", "z"]


class TestAccountsTableRemove:
    """Tests for AccountsTable.remove() method."""

    async def test_remove_existing_account(self, db):
        """Remove an existing account."""
        accounts = db.table("accounts")
        await accounts.add({"id": "smtp1", "tenant_id": "t1", "host": "h", "port": 25})
        await accounts.remove("t1", "smtp1")

        with pytest.raises(ValueError, match="not found"):
            await accounts.get("t1", "smtp1")

    async def test_remove_nonexistent_no_error(self, db):
        """Remove non-existent account doesn't raise."""
        accounts = db.table("accounts")
        # Should not raise
        await accounts.remove("t1", "nonexistent")

    async def test_remove_wrong_tenant_no_effect(self, db):
        """Remove with wrong tenant doesn't affect account."""
        accounts = db.table("accounts")
        await accounts.add({"id": "smtp1", "tenant_id": "t1", "host": "h", "port": 25})
        await accounts.remove("wrong_tenant", "smtp1")

        # Account still exists
        account = await accounts.get("t1", "smtp1")
        assert account["id"] == "smtp1"


class TestAccountsTableSyncSchema:
    """Tests for AccountsTable.sync_schema() method."""

    async def test_sync_schema_creates_index(self, db):
        """sync_schema creates unique index."""
        accounts = db.table("accounts")
        await accounts.sync_schema()
        # Should not raise on duplicate check
        await accounts.add({"id": "a1", "tenant_id": "t1", "host": "h", "port": 25})

        # Try to insert duplicate via raw SQL - should fail
        with pytest.raises(Exception):
            await db.adapter.execute(
                "INSERT INTO accounts (pk, id, tenant_id, host, port) VALUES ('pk2', 'a1', 't1', 'h', 25)"
            )
