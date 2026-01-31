# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Tests for TenantsTable EE methods."""

import hashlib
import time

import pytest

from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with schema only (no init logic)."""
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
    await proxy.db.connect()
    await proxy.db.check_structure()
    yield proxy.db
    await proxy.close()


class TestTenantsTableAdd:
    """Tests for TenantsTable.add() method (EE)."""

    async def test_add_new_tenant_returns_api_key(self, db):
        """add() for new tenant returns API key."""
        tenants = db.table("tenants")
        api_key = await tenants.add({"id": "t1", "name": "Tenant 1"})
        assert api_key  # Non-empty string
        assert len(api_key) > 20  # urlsafe token

    async def test_add_new_tenant_stores_hash(self, db):
        """add() stores API key hash, not raw key."""
        tenants = db.table("tenants")
        api_key = await tenants.add({"id": "t1", "name": "Tenant 1"})
        tenant = await tenants.get("t1")
        expected_hash = hashlib.sha256(api_key.encode()).hexdigest()
        assert tenant["api_key_hash"] == expected_hash

    async def test_add_existing_tenant_returns_empty(self, db):
        """add() for existing tenant returns empty string (key unchanged)."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Original"})
        result = await tenants.add({"id": "t1", "name": "Updated"})
        assert result == ""

    async def test_add_existing_tenant_updates_fields(self, db):
        """add() for existing tenant updates fields."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Original"})
        await tenants.add({"id": "t1", "name": "Updated", "client_base_url": "http://x"})
        tenant = await tenants.get("t1")
        assert tenant["name"] == "Updated"
        assert tenant["client_base_url"] == "http://x"

    async def test_add_tenant_with_all_fields(self, db):
        """add() stores all optional fields."""
        tenants = db.table("tenants")
        await tenants.add({
            "id": "t1",
            "name": "Full Tenant",
            "client_auth": {"type": "bearer", "token": "xxx"},
            "client_base_url": "http://api.example.com",
            "client_sync_path": "/sync",
            "client_attachment_path": "/attach",
            "rate_limits": {"per_minute": 10},
            "large_file_config": {"provider": "s3"},
            "active": False,
        })
        tenant = await tenants.get("t1")
        assert tenant["name"] == "Full Tenant"
        assert tenant["client_auth"] == {"type": "bearer", "token": "xxx"}
        assert tenant["client_base_url"] == "http://api.example.com"
        assert tenant["rate_limits"] == {"per_minute": 10}
        assert tenant["active"] is False


class TestTenantsTableListAll:
    """Tests for TenantsTable.list_all() method (EE)."""

    async def test_list_all_empty(self, db):
        """list_all() returns empty list when no tenants."""
        tenants = db.table("tenants")
        result = await tenants.list_all()
        assert result == []

    async def test_list_all_returns_all(self, db):
        """list_all() returns all tenants."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Tenant 1"})
        await tenants.add({"id": "t2", "name": "Tenant 2"})
        await tenants.add({"id": "t3", "name": "Tenant 3"})
        result = await tenants.list_all()
        assert len(result) == 3
        ids = [t["id"] for t in result]
        assert set(ids) == {"t1", "t2", "t3"}

    async def test_list_all_active_only(self, db):
        """list_all(active_only=True) filters inactive."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Active", "active": True})
        await tenants.add({"id": "t2", "name": "Inactive", "active": False})
        result = await tenants.list_all(active_only=True)
        assert len(result) == 1
        assert result[0]["id"] == "t1"

    async def test_list_all_ordered_by_id(self, db):
        """list_all() returns tenants ordered by id."""
        tenants = db.table("tenants")
        await tenants.add({"id": "z", "name": "Z"})
        await tenants.add({"id": "a", "name": "A"})
        await tenants.add({"id": "m", "name": "M"})
        result = await tenants.list_all()
        ids = [t["id"] for t in result]
        assert ids == ["a", "m", "z"]


class TestTenantsTableUpdateFields:
    """Tests for TenantsTable.update_fields() method (EE)."""

    async def test_update_fields_name(self, db):
        """update_fields() can update name."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Original"})
        result = await tenants.update_fields("t1", {"name": "Updated"})
        assert result is True
        tenant = await tenants.get("t1")
        assert tenant["name"] == "Updated"

    async def test_update_fields_active(self, db):
        """update_fields() can update active status."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test", "active": True})
        await tenants.update_fields("t1", {"active": False})
        tenant = await tenants.get("t1")
        assert tenant["active"] is False

    async def test_update_fields_json(self, db):
        """update_fields() can update JSON fields."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        await tenants.update_fields("t1", {"rate_limits": {"per_hour": 100}})
        tenant = await tenants.get("t1")
        assert tenant["rate_limits"] == {"per_hour": 100}

    async def test_update_fields_nonexistent_returns_false(self, db):
        """update_fields() returns False for non-existent tenant."""
        tenants = db.table("tenants")
        result = await tenants.update_fields("nonexistent", {"name": "X"})
        assert result is False

    async def test_update_fields_empty_returns_false(self, db):
        """update_fields() with empty dict returns False."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        result = await tenants.update_fields("t1", {})
        assert result is False


class TestTenantsTableRemove:
    """Tests for TenantsTable.remove() method (EE)."""

    async def test_remove_existing_tenant(self, db):
        """remove() deletes tenant."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        result = await tenants.remove("t1")
        assert result is True
        assert await tenants.get("t1") is None

    async def test_remove_nonexistent_returns_false(self, db):
        """remove() returns False for non-existent tenant."""
        tenants = db.table("tenants")
        result = await tenants.remove("nonexistent")
        assert result is False

    async def test_remove_cascades_to_accounts(self, db):
        """remove() deletes associated accounts."""
        tenants = db.table("tenants")
        accounts = db.table("accounts")
        await tenants.add({"id": "t1", "name": "Test"})
        await accounts.add({"id": "a1", "tenant_id": "t1", "host": "h", "port": 25})
        await tenants.remove("t1")
        # Account should be gone
        with pytest.raises(ValueError):
            await accounts.get("t1", "a1")

    async def test_remove_cascades_to_messages(self, db):
        """remove() deletes associated messages."""
        tenants = db.table("tenants")
        messages = db.table("messages")
        await tenants.add({"id": "t1", "name": "Test"})
        await accounts_table_add_for_test(db, "t1", "a1")
        await messages.insert({
            "pk": "msg-pk-1",
            "id": "m1",
            "tenant_id": "t1",
            "account_id": "a1",
            "payload": '{"to": "x@x.com"}',
        })
        await tenants.remove("t1")
        # Message should be gone
        result = await messages.select_one(where={"id": "m1"})
        assert result is None


async def accounts_table_add_for_test(db, tenant_id, account_id):
    """Helper to add account for tests."""
    await db.table("accounts").add({
        "id": account_id,
        "tenant_id": tenant_id,
        "host": "smtp.example.com",
        "port": 587,
    })


class TestTenantsTableApiKeys:
    """Tests for TenantsTable API key methods (EE)."""

    async def test_create_api_key(self, db):
        """create_api_key() generates new key."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        old_key = await tenants.create_api_key("t1")
        assert old_key
        assert len(old_key) > 20

    async def test_create_api_key_replaces_existing(self, db):
        """create_api_key() replaces existing key."""
        tenants = db.table("tenants")
        original_key = await tenants.add({"id": "t1", "name": "Test"})
        new_key = await tenants.create_api_key("t1")
        assert new_key != original_key

    async def test_create_api_key_nonexistent_returns_none(self, db):
        """create_api_key() returns None for non-existent tenant."""
        tenants = db.table("tenants")
        result = await tenants.create_api_key("nonexistent")
        assert result is None

    async def test_get_tenant_by_token(self, db):
        """get_tenant_by_token() finds tenant by API key."""
        tenants = db.table("tenants")
        api_key = await tenants.add({"id": "t1", "name": "Test"})
        tenant = await tenants.get_tenant_by_token(api_key)
        assert tenant is not None
        assert tenant["id"] == "t1"

    async def test_get_tenant_by_token_invalid(self, db):
        """get_tenant_by_token() returns None for invalid key."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        result = await tenants.get_tenant_by_token("invalid-key")
        assert result is None

    async def test_get_tenant_by_token_expired(self, db):
        """get_tenant_by_token() returns None for expired key."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        # Create key that expired in the past
        api_key = await tenants.create_api_key("t1", expires_at=int(time.time()) - 100)
        result = await tenants.get_tenant_by_token(api_key)
        assert result is None

    async def test_revoke_api_key(self, db):
        """revoke_api_key() removes API key."""
        tenants = db.table("tenants")
        api_key = await tenants.add({"id": "t1", "name": "Test"})
        await tenants.revoke_api_key("t1")
        # Key should no longer work
        result = await tenants.get_tenant_by_token(api_key)
        assert result is None

    async def test_revoke_api_key_nonexistent(self, db):
        """revoke_api_key() returns False for non-existent tenant."""
        tenants = db.table("tenants")
        result = await tenants.revoke_api_key("nonexistent")
        assert result is False


class TestTenantsTableBatchSuspension:
    """Tests for TenantsTable batch suspension methods (EE)."""

    async def test_suspend_batch_all(self, db):
        """suspend_batch(None) suspends all sending."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        result = await tenants.suspend_batch("t1", None)
        assert result is True
        suspended = await tenants.get_suspended_batches("t1")
        assert suspended == {"*"}

    async def test_suspend_batch_specific(self, db):
        """suspend_batch(code) suspends specific batch."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        await tenants.suspend_batch("t1", "campaign1")
        suspended = await tenants.get_suspended_batches("t1")
        assert suspended == {"campaign1"}

    async def test_suspend_batch_multiple(self, db):
        """Multiple suspend_batch() calls accumulate."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        await tenants.suspend_batch("t1", "batch1")
        await tenants.suspend_batch("t1", "batch2")
        suspended = await tenants.get_suspended_batches("t1")
        assert suspended == {"batch1", "batch2"}

    async def test_suspend_batch_nonexistent_returns_false(self, db):
        """suspend_batch() returns False for non-existent tenant."""
        tenants = db.table("tenants")
        result = await tenants.suspend_batch("nonexistent", "batch1")
        assert result is False

    async def test_activate_batch_all(self, db):
        """activate_batch(None) clears all suspensions."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        await tenants.suspend_batch("t1", None)  # Suspend all
        await tenants.activate_batch("t1", None)  # Clear all
        suspended = await tenants.get_suspended_batches("t1")
        assert suspended == set()

    async def test_activate_batch_specific(self, db):
        """activate_batch(code) removes specific batch."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        await tenants.suspend_batch("t1", "batch1")
        await tenants.suspend_batch("t1", "batch2")
        await tenants.activate_batch("t1", "batch1")
        suspended = await tenants.get_suspended_batches("t1")
        assert suspended == {"batch2"}

    async def test_activate_batch_from_full_suspension_fails(self, db):
        """Cannot remove single batch when '*' is active."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        await tenants.suspend_batch("t1", None)  # Suspend all
        result = await tenants.activate_batch("t1", "batch1")  # Try to remove one
        assert result is False
        # Still fully suspended
        suspended = await tenants.get_suspended_batches("t1")
        assert suspended == {"*"}

    async def test_get_suspended_batches_empty(self, db):
        """get_suspended_batches() returns empty set when none suspended."""
        tenants = db.table("tenants")
        await tenants.add({"id": "t1", "name": "Test"})
        suspended = await tenants.get_suspended_batches("t1")
        assert suspended == set()

    async def test_get_suspended_batches_nonexistent(self, db):
        """get_suspended_batches() returns empty set for non-existent tenant."""
        tenants = db.table("tenants")
        suspended = await tenants.get_suspended_batches("nonexistent")
        assert suspended == set()
