# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for InstanceEndpoint - direct endpoint tests for coverage.

These tests directly exercise InstanceEndpoint methods to cover
edge cases and paths not reached by HTTP client tests.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from core.mail_proxy.entities.instance.endpoint import InstanceEndpoint


@pytest.fixture
def mock_table():
    """Create mock InstanceTable."""
    table = MagicMock()
    table.get_instance = AsyncMock(return_value={"id": 1, "name": "test-proxy", "edition": "ce"})
    table.update_instance = AsyncMock()
    table.is_enterprise = AsyncMock(return_value=False)
    table.set_edition = AsyncMock()
    return table


@pytest.fixture
def mock_proxy():
    """Create mock MailProxy."""
    proxy = MagicMock()
    proxy._active = True
    proxy.handle_command = AsyncMock(return_value={"ok": True})
    proxy.db = MagicMock()
    return proxy


@pytest.fixture
def endpoint(mock_table):
    """Create InstanceEndpoint without proxy."""
    return InstanceEndpoint(mock_table)


@pytest.fixture
def endpoint_with_proxy(mock_table, mock_proxy):
    """Create InstanceEndpoint with proxy."""
    return InstanceEndpoint(mock_table, mock_proxy)


class TestInstanceEndpointHealth:
    """Tests for InstanceEndpoint.health() method."""

    async def test_health_returns_ok(self, endpoint):
        """health() returns status ok."""
        result = await endpoint.health()
        assert result == {"status": "ok"}


class TestInstanceEndpointStatus:
    """Tests for InstanceEndpoint.status() method."""

    async def test_status_without_proxy(self, endpoint):
        """status() returns active=True when no proxy."""
        result = await endpoint.status()
        assert result["ok"] is True
        assert result["active"] is True

    async def test_status_with_active_proxy(self, endpoint_with_proxy, mock_proxy):
        """status() returns proxy's active state."""
        mock_proxy._active = True
        result = await endpoint_with_proxy.status()
        assert result["active"] is True

    async def test_status_with_inactive_proxy(self, endpoint_with_proxy, mock_proxy):
        """status() returns false when proxy inactive."""
        mock_proxy._active = False
        result = await endpoint_with_proxy.status()
        assert result["active"] is False


class TestInstanceEndpointRunNow:
    """Tests for InstanceEndpoint.run_now() method."""

    async def test_run_now_without_proxy(self, endpoint):
        """run_now() returns ok when no proxy."""
        result = await endpoint.run_now()
        assert result == {"ok": True}

    async def test_run_now_with_proxy(self, endpoint_with_proxy, mock_proxy):
        """run_now() calls proxy handle_command."""
        mock_proxy.handle_command = AsyncMock(return_value={"ok": True, "triggered": True})
        result = await endpoint_with_proxy.run_now(tenant_id="t1")

        mock_proxy.handle_command.assert_called_once_with("run now", {"tenant_id": "t1"})
        assert result == {"ok": True, "triggered": True}


class TestInstanceEndpointSuspend:
    """Tests for InstanceEndpoint.suspend() method."""

    async def test_suspend_without_proxy(self, endpoint):
        """suspend() returns basic response when no proxy."""
        result = await endpoint.suspend("t1", "batch-001")
        assert result["ok"] is True
        assert result["tenant_id"] == "t1"
        assert result["batch_code"] == "batch-001"

    async def test_suspend_with_proxy(self, endpoint_with_proxy, mock_proxy):
        """suspend() calls proxy handle_command."""
        mock_proxy.handle_command = AsyncMock(return_value={"ok": True, "suspended": ["batch-001"]})
        result = await endpoint_with_proxy.suspend("t1", "batch-001")

        mock_proxy.handle_command.assert_called_once_with(
            "suspend",
            {"tenant_id": "t1", "batch_code": "batch-001"},
        )
        assert result["ok"] is True


class TestInstanceEndpointActivate:
    """Tests for InstanceEndpoint.activate() method."""

    async def test_activate_without_proxy(self, endpoint):
        """activate() returns basic response when no proxy."""
        result = await endpoint.activate("t1", "batch-001")
        assert result["ok"] is True
        assert result["tenant_id"] == "t1"

    async def test_activate_with_proxy(self, endpoint_with_proxy, mock_proxy):
        """activate() calls proxy handle_command."""
        mock_proxy.handle_command = AsyncMock(return_value={"ok": True, "activated": True})
        result = await endpoint_with_proxy.activate("t1")

        mock_proxy.handle_command.assert_called_once_with(
            "activate",
            {"tenant_id": "t1", "batch_code": None},
        )
        assert result["ok"] is True


class TestInstanceEndpointGet:
    """Tests for InstanceEndpoint.get() method."""

    async def test_get_returns_instance(self, endpoint, mock_table):
        """get() returns instance configuration."""
        result = await endpoint.get()
        assert result["ok"] is True
        assert result["name"] == "test-proxy"

    async def test_get_returns_defaults_when_none(self, endpoint, mock_table):
        """get() returns defaults when no instance."""
        mock_table.get_instance = AsyncMock(return_value=None)
        result = await endpoint.get()
        assert result["ok"] is True
        assert result["id"] == 1
        assert result["name"] == "mail-proxy"
        assert result["edition"] == "ce"


class TestInstanceEndpointUpdate:
    """Tests for InstanceEndpoint.update() method."""

    async def test_update_name(self, endpoint, mock_table):
        """update() updates name."""
        result = await endpoint.update(name="new-name")
        mock_table.update_instance.assert_called_once_with({"name": "new-name"})
        assert result["ok"] is True

    async def test_update_api_token(self, endpoint, mock_table):
        """update() updates api_token."""
        result = await endpoint.update(api_token="secret-token")
        mock_table.update_instance.assert_called_once_with({"api_token": "secret-token"})
        assert result["ok"] is True

    async def test_update_no_changes(self, endpoint, mock_table):
        """update() does nothing when no values provided."""
        result = await endpoint.update()
        mock_table.update_instance.assert_not_called()
        assert result["ok"] is True


class TestInstanceEndpointGetSyncStatus:
    """Tests for InstanceEndpoint.get_sync_status() method."""

    async def test_get_sync_status_without_proxy(self, endpoint):
        """get_sync_status() returns empty list when no proxy."""
        result = await endpoint.get_sync_status()
        assert result["ok"] is True
        assert result["tenants"] == []

    async def test_get_sync_status_with_proxy(self, endpoint_with_proxy, mock_proxy):
        """get_sync_status() calls proxy handle_command."""
        mock_proxy.handle_command = AsyncMock(return_value={
            "ok": True,
            "tenants": [{"id": "t1", "last_sync_ts": 12345}],
        })
        result = await endpoint_with_proxy.get_sync_status()

        mock_proxy.handle_command.assert_called_once_with("listTenantsSyncStatus", {})
        assert len(result["tenants"]) == 1


class TestInstanceEndpointUpgradeToEE:
    """Tests for InstanceEndpoint.upgrade_to_ee() method."""

    async def test_upgrade_without_enterprise_raises(self, endpoint):
        """upgrade_to_ee() raises ValueError when EE not installed."""
        with patch("core.mail_proxy.HAS_ENTERPRISE", False):
            with pytest.raises(ValueError, match="Enterprise modules not installed"):
                await endpoint.upgrade_to_ee()

    async def test_upgrade_already_ee(self, endpoint, mock_table):
        """upgrade_to_ee() returns message when already EE."""
        mock_table.is_enterprise = AsyncMock(return_value=True)
        with patch("core.mail_proxy.HAS_ENTERPRISE", True):
            result = await endpoint.upgrade_to_ee()
            assert result["edition"] == "ee"
            assert "Already" in result["message"]

    async def test_upgrade_to_ee_with_default_tenant(self, mock_table, mock_proxy):
        """upgrade_to_ee() generates token for default tenant."""
        mock_table.is_enterprise = AsyncMock(return_value=False)

        # Mock tenants table
        mock_tenants_table = MagicMock()
        mock_tenants_table.get = AsyncMock(return_value={"id": "default", "api_key_hash": None})
        mock_tenants_table.create_api_key = AsyncMock(return_value="new-token-123")
        mock_proxy.db.table = MagicMock(return_value=mock_tenants_table)

        endpoint = InstanceEndpoint(mock_table, mock_proxy)

        with patch("core.mail_proxy.HAS_ENTERPRISE", True):
            result = await endpoint.upgrade_to_ee()

            assert result["ok"] is True
            assert result["edition"] == "ee"
            assert result["default_tenant_token"] == "new-token-123"
            assert "Save the default tenant token" in result["message"]

    async def test_upgrade_to_ee_without_default_tenant(self, mock_table, mock_proxy):
        """upgrade_to_ee() works when no default tenant."""
        mock_table.is_enterprise = AsyncMock(return_value=False)

        # Mock tenants table without default tenant
        mock_tenants_table = MagicMock()
        mock_tenants_table.get = AsyncMock(return_value=None)
        mock_proxy.db.table = MagicMock(return_value=mock_tenants_table)

        endpoint = InstanceEndpoint(mock_table, mock_proxy)

        with patch("core.mail_proxy.HAS_ENTERPRISE", True):
            result = await endpoint.upgrade_to_ee()

            assert result["ok"] is True
            assert result["edition"] == "ee"
            assert "default_tenant_token" not in result
            assert "Upgraded to Enterprise Edition" in result["message"]

    async def test_upgrade_to_ee_without_proxy(self, endpoint, mock_table):
        """upgrade_to_ee() works without proxy."""
        mock_table.is_enterprise = AsyncMock(return_value=False)

        with patch("core.mail_proxy.HAS_ENTERPRISE", True):
            result = await endpoint.upgrade_to_ee()

            assert result["ok"] is True
            assert result["edition"] == "ee"
            mock_table.set_edition.assert_called_once_with("ee")
