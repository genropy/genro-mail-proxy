# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for StoragesTable - CE table methods."""

import pytest

from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with schema only (no init logic)."""
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
    await proxy.db.connect()
    await proxy.db.check_structure()
    # Create a tenant for storage tests
    await proxy.db.table("tenants").insert({"id": "t1", "name": "Test Tenant", "active": 1})
    yield proxy.db
    await proxy.close()


class TestStoragesTableAdd:
    """Tests for StoragesTable.add() method."""

    async def test_add_local_storage(self, db):
        """add() creates a local storage configuration."""
        storages = db.table("storages")
        pk = await storages.add({
            "tenant_id": "t1",
            "name": "HOME",
            "protocol": "local",
            "config": {"base_path": "/data/attachments"},
        })
        assert pk is not None

        storage = await storages.get("t1", "HOME")
        assert storage["name"] == "HOME"
        assert storage["protocol"] == "local"
        assert storage["config"]["base_path"] == "/data/attachments"

    async def test_add_multiple_storages(self, db):
        """add() can create multiple storages for a tenant."""
        storages = db.table("storages")

        await storages.add({
            "tenant_id": "t1",
            "name": "HOME",
            "protocol": "local",
            "config": {"base_path": "/data/home"},
        })
        await storages.add({
            "tenant_id": "t1",
            "name": "ARCHIVE",
            "protocol": "local",
            "config": {"base_path": "/data/archive"},
        })

        all_storages = await storages.list_all("t1")
        assert len(all_storages) == 2
        names = [s["name"] for s in all_storages]
        assert "HOME" in names
        assert "ARCHIVE" in names

    async def test_add_upsert_existing(self, db):
        """add() updates existing storage with same tenant+name."""
        storages = db.table("storages")

        # Create initial storage
        await storages.add({
            "tenant_id": "t1",
            "name": "HOME",
            "protocol": "local",
            "config": {"base_path": "/old/path"},
        })

        # Upsert with new config
        await storages.add({
            "tenant_id": "t1",
            "name": "HOME",
            "protocol": "local",
            "config": {"base_path": "/new/path"},
        })

        # Should only have one storage
        all_storages = await storages.list_all("t1")
        assert len(all_storages) == 1
        assert all_storages[0]["config"]["base_path"] == "/new/path"

    async def test_add_cloud_protocol_behavior(self, db):
        """add() rejects cloud protocols in CE, allows in EE."""
        storages = db.table("storages")

        # Check if EE is available
        try:
            from enterprise.mail_proxy import is_ee_enabled
            ee_available = is_ee_enabled()
        except ImportError:
            ee_available = False

        if ee_available:
            # In EE mode, cloud protocols are allowed (but may fail at runtime
            # without actual cloud credentials)
            pk = await storages.add({
                "tenant_id": "t1",
                "name": "S3",
                "protocol": "s3",
                "config": {"bucket": "my-bucket"},
            })
            assert pk is not None
        else:
            # In CE mode, cloud protocols are rejected
            with pytest.raises(ValueError, match="Enterprise Edition"):
                await storages.add({
                    "tenant_id": "t1",
                    "name": "S3",
                    "protocol": "s3",
                    "config": {"bucket": "my-bucket"},
                })


class TestStoragesTableGet:
    """Tests for StoragesTable.get() method."""

    async def test_get_existing(self, db):
        """get() returns storage configuration."""
        storages = db.table("storages")
        await storages.add({
            "tenant_id": "t1",
            "name": "HOME",
            "protocol": "local",
            "config": {"base_path": "/data"},
        })

        storage = await storages.get("t1", "HOME")
        assert storage["tenant_id"] == "t1"
        assert storage["name"] == "HOME"

    async def test_get_nonexistent_raises(self, db):
        """get() raises ValueError for non-existent storage."""
        storages = db.table("storages")

        with pytest.raises(ValueError, match="not found"):
            await storages.get("t1", "NONEXISTENT")


class TestStoragesTableListAll:
    """Tests for StoragesTable.list_all() method."""

    async def test_list_all_empty(self, db):
        """list_all() returns empty list when no storages."""
        storages = db.table("storages")
        result = await storages.list_all("t1")
        assert result == []

    async def test_list_all_for_tenant(self, db):
        """list_all() returns only storages for specified tenant."""
        storages = db.table("storages")
        tenants = db.table("tenants")

        # Create second tenant
        await tenants.insert({"id": "t2", "name": "Other", "active": 1})

        # Add storages for both tenants
        await storages.add({"tenant_id": "t1", "name": "A", "protocol": "local", "config": {}})
        await storages.add({"tenant_id": "t2", "name": "B", "protocol": "local", "config": {}})

        t1_storages = await storages.list_all("t1")
        assert len(t1_storages) == 1
        assert t1_storages[0]["name"] == "A"


class TestStoragesTableRemove:
    """Tests for StoragesTable.remove() method."""

    async def test_remove_existing(self, db):
        """remove() deletes storage and returns True."""
        storages = db.table("storages")
        await storages.add({
            "tenant_id": "t1",
            "name": "HOME",
            "protocol": "local",
            "config": {},
        })

        result = await storages.remove("t1", "HOME")
        assert result is True

        all_storages = await storages.list_all("t1")
        assert len(all_storages) == 0

    async def test_remove_nonexistent(self, db):
        """remove() returns False for non-existent storage."""
        storages = db.table("storages")
        result = await storages.remove("t1", "NONEXISTENT")
        assert result is False


class TestStoragesTableStorageManager:
    """Tests for StoragesTable.get_storage_manager() method."""

    async def test_get_storage_manager(self, db, tmp_path):
        """get_storage_manager() returns configured StorageManager."""
        storages = db.table("storages")

        # Add storages
        await storages.add({
            "tenant_id": "t1",
            "name": "HOME",
            "protocol": "local",
            "config": {"base_path": str(tmp_path / "home")},
        })
        await storages.add({
            "tenant_id": "t1",
            "name": "TEMP",
            "protocol": "local",
            "config": {"base_path": str(tmp_path / "temp")},
        })

        manager = await storages.get_storage_manager("t1")

        assert manager.has_mount("HOME")
        assert manager.has_mount("TEMP")
        assert not manager.has_mount("OTHER")

    async def test_get_storage_manager_empty(self, db):
        """get_storage_manager() works with no storages."""
        storages = db.table("storages")
        manager = await storages.get_storage_manager("t1")
        assert manager.get_mount_names() == []
