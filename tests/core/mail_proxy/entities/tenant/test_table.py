# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for TenantsTable - CE table methods."""

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


class TestTenantsTableGet:
    """Tests for TenantsTable.get() method (CE)."""

    async def test_get_nonexistent_returns_none(self, db):
        """get() returns None for non-existent tenant."""
        tenants = db.table("tenants")
        result = await tenants.get("nonexistent")
        assert result is None

    async def test_get_existing_tenant(self, db):
        """get() returns tenant dict for existing tenant."""
        tenants = db.table("tenants")
        await tenants.insert({"id": "t1", "name": "Test Tenant", "active": 1})
        result = await tenants.get("t1")
        assert result is not None
        assert result["id"] == "t1"
        assert result["name"] == "Test Tenant"

    async def test_get_decodes_active_to_bool(self, db):
        """get() converts active INTEGER to bool."""
        tenants = db.table("tenants")
        await tenants.insert({"id": "t1", "name": "Test", "active": 1})
        result = await tenants.get("t1")
        assert result["active"] is True

    async def test_get_inactive_tenant(self, db):
        """get() returns active=False for inactive tenant."""
        tenants = db.table("tenants")
        await tenants.insert({"id": "t1", "name": "Test", "active": 0})
        result = await tenants.get("t1")
        assert result["active"] is False


class TestTenantsTableIsBatchSuspended:
    """Tests for TenantsTable.is_batch_suspended() method (CE)."""

    async def test_no_suspension(self, db):
        """No suspended_batches means not suspended."""
        tenants = db.table("tenants")
        assert tenants.is_batch_suspended(None, "batch1") is False
        assert tenants.is_batch_suspended("", "batch1") is False

    async def test_all_suspended(self, db):
        """suspended_batches='*' suspends everything."""
        tenants = db.table("tenants")
        assert tenants.is_batch_suspended("*", "batch1") is True
        assert tenants.is_batch_suspended("*", "batch2") is True
        assert tenants.is_batch_suspended("*", None) is True

    async def test_specific_batch_suspended(self, db):
        """Specific batch codes are suspended."""
        tenants = db.table("tenants")
        assert tenants.is_batch_suspended("batch1,batch2", "batch1") is True
        assert tenants.is_batch_suspended("batch1,batch2", "batch2") is True
        assert tenants.is_batch_suspended("batch1,batch2", "batch3") is False

    async def test_message_without_batch_code(self, db):
        """Messages without batch_code only suspended by '*'."""
        tenants = db.table("tenants")
        assert tenants.is_batch_suspended("batch1,batch2", None) is False
        assert tenants.is_batch_suspended("*", None) is True


class TestTenantsTableEnsureDefault:
    """Tests for TenantsTable.ensure_default() method (CE)."""

    async def test_ensure_default_creates_tenant(self, db):
        """ensure_default creates 'default' tenant."""
        tenants = db.table("tenants")
        await tenants.ensure_default()
        result = await tenants.get("default")
        assert result is not None
        assert result["id"] == "default"
        assert result["name"] == "Default Tenant"
        assert result["active"] is True

    async def test_ensure_default_idempotent(self, db):
        """Multiple calls to ensure_default don't create duplicates."""
        tenants = db.table("tenants")
        await tenants.ensure_default()
        await tenants.ensure_default()
        await tenants.ensure_default()
        # Should still have only one default tenant
        result = await tenants.get("default")
        assert result["id"] == "default"

    async def test_ensure_default_preserves_existing(self, db):
        """ensure_default doesn't overwrite existing default tenant."""
        tenants = db.table("tenants")
        await tenants.insert({"id": "default", "name": "Custom Name", "active": 1})
        await tenants.ensure_default()
        result = await tenants.get("default")
        assert result["name"] == "Custom Name"
