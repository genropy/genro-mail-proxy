# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1

"""Unit tests for MailProxy command handling."""

from __future__ import annotations

import pytest
import pytest_asyncio

from src.mail_proxy.core import MailProxy


@pytest_asyncio.fixture
async def proxy(tmp_path):
    """Create a MailProxy for testing."""
    proxy = MailProxy(
        db_path=str(tmp_path / "test.db"),
        test_mode=True,
    )
    await proxy.start()
    yield proxy
    await proxy.stop()


# ---------------------------------------------------------------------------
# Tenant Commands
# ---------------------------------------------------------------------------


class TestGetTenantCommand:
    """Test getTenant command."""

    @pytest.mark.asyncio
    async def test_get_existing_tenant(self, proxy: MailProxy):
        """Should return tenant data when found."""
        # Setup: create tenant via DB
        await proxy.db.add_tenant({
            "id": "acme",
            "name": "ACME Corp",
            "active": True,
        })

        result = await proxy.handle_command("getTenant", {"id": "acme"})

        assert result["ok"] is True
        assert result["id"] == "acme"
        assert result["name"] == "ACME Corp"

    @pytest.mark.asyncio
    async def test_get_nonexistent_tenant(self, proxy: MailProxy):
        """Should return error when tenant not found."""
        result = await proxy.handle_command("getTenant", {"id": "nonexistent"})

        assert result["ok"] is False
        assert "not found" in result["error"]


class TestListTenantsCommand:
    """Test listTenants command."""

    @pytest.mark.asyncio
    async def test_list_all_tenants(self, proxy: MailProxy):
        """Should return all tenants."""
        await proxy.db.add_tenant({"id": "t1", "name": "Tenant 1", "active": True})
        await proxy.db.add_tenant({"id": "t2", "name": "Tenant 2", "active": False})

        result = await proxy.handle_command("listTenants", {})

        assert result["ok"] is True
        assert len(result["tenants"]) == 2

    @pytest.mark.asyncio
    async def test_list_active_only(self, proxy: MailProxy):
        """Should filter by active status."""
        await proxy.db.add_tenant({"id": "t1", "name": "Tenant 1", "active": True})
        await proxy.db.add_tenant({"id": "t2", "name": "Tenant 2", "active": False})

        result = await proxy.handle_command("listTenants", {"active_only": True})

        assert result["ok"] is True
        assert len(result["tenants"]) == 1
        assert result["tenants"][0]["id"] == "t1"

    @pytest.mark.asyncio
    async def test_list_tenants_empty(self, proxy: MailProxy):
        """Should return empty list when no tenants."""
        result = await proxy.handle_command("listTenants", {})

        assert result["ok"] is True
        assert result["tenants"] == []


class TestUpdateTenantCommand:
    """Test updateTenant command."""

    @pytest.mark.asyncio
    async def test_update_existing_tenant(self, proxy: MailProxy):
        """Should update tenant and return ok."""
        await proxy.db.add_tenant({"id": "acme", "name": "Old Name"})

        result = await proxy.handle_command("updateTenant", {
            "id": "acme",
            "name": "New Name",
        })

        assert result["ok"] is True

        # Verify update
        tenant = await proxy.db.get_tenant("acme")
        assert tenant["name"] == "New Name"

    @pytest.mark.asyncio
    async def test_update_nonexistent_tenant(self, proxy: MailProxy):
        """Should return error when tenant not found."""
        result = await proxy.handle_command("updateTenant", {
            "id": "nonexistent",
            "name": "New Name",
        })

        assert result["ok"] is False
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_update_tenant_missing_id(self, proxy: MailProxy):
        """Should return error when id is missing."""
        result = await proxy.handle_command("updateTenant", {"name": "New Name"})

        assert result["ok"] is False
        assert "id required" in result["error"]


class TestDeleteTenantCommand:
    """Test deleteTenant command."""

    @pytest.mark.asyncio
    async def test_delete_existing_tenant(self, proxy: MailProxy):
        """Should delete tenant and return ok."""
        await proxy.db.add_tenant({"id": "acme", "name": "ACME"})

        result = await proxy.handle_command("deleteTenant", {"id": "acme"})

        assert result["ok"] is True

        # Verify deletion
        tenant = await proxy.db.get_tenant("acme")
        assert tenant is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_tenant(self, proxy: MailProxy):
        """Should return error when tenant not found."""
        result = await proxy.handle_command("deleteTenant", {"id": "nonexistent"})

        assert result["ok"] is False
        assert "not found" in result["error"]


# ---------------------------------------------------------------------------
# Instance Commands
# ---------------------------------------------------------------------------


class TestGetInstanceCommand:
    """Test getInstance command."""

    @pytest.mark.asyncio
    async def test_get_instance_default(self, proxy: MailProxy):
        """Should return instance created by init_db with edition set."""
        result = await proxy.handle_command("getInstance", {})

        # init_db now creates instance via _init_edition()
        assert result["ok"] is True
        assert result["edition"] in ("ce", "ee")

    @pytest.mark.asyncio
    async def test_get_existing_instance(self, proxy: MailProxy):
        """Should return instance data when configured."""
        await proxy.db.instance.update_instance({
            "name": "mail-proxy-1",
            "api_token": "secret-token",
        })

        result = await proxy.handle_command("getInstance", {})

        assert result["ok"] is True
        assert result["name"] == "mail-proxy-1"
        assert result["api_token"] == "secret-token"


class TestUpdateInstanceCommand:
    """Test updateInstance command."""

    @pytest.mark.asyncio
    async def test_update_instance(self, proxy: MailProxy):
        """Should update instance configuration."""
        result = await proxy.handle_command("updateInstance", {
            "name": "mail-proxy-2",
            "api_token": "new-token",
        })

        assert result["ok"] is True

        # Verify update
        instance = await proxy.db.instance.get_instance()
        assert instance is not None
        assert instance["name"] == "mail-proxy-2"
        assert instance["api_token"] == "new-token"

    @pytest.mark.asyncio
    async def test_update_instance_overwrite(self, proxy: MailProxy):
        """Should overwrite existing instance config."""
        await proxy.db.instance.update_instance({"name": "old-name"})

        result = await proxy.handle_command("updateInstance", {"name": "new-name"})

        assert result["ok"] is True
        instance = await proxy.db.instance.get_instance()
        assert instance["name"] == "new-name"


# ---------------------------------------------------------------------------
# Unknown Command
# ---------------------------------------------------------------------------


class TestUnknownCommand:
    """Test handling of unknown commands."""

    @pytest.mark.asyncio
    async def test_unknown_command_returns_error(self, proxy: MailProxy):
        """Should return error for unknown command."""
        result = await proxy.handle_command("unknownCommand", {})

        assert result["ok"] is False
        assert "unknown command" in result["error"]
