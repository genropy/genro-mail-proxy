# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for StorageEndpoint - direct endpoint tests for coverage."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from core.mail_proxy.entities.storage.endpoint import StorageEndpoint


@pytest.fixture
def mock_table():
    """Create mock StoragesTable."""
    table = MagicMock()
    table.add = AsyncMock()
    table.get = AsyncMock(return_value={"tenant_id": "t1", "name": "HOME", "protocol": "local"})
    table.list_all = AsyncMock(return_value=[])
    table.remove = AsyncMock(return_value=True)
    return table


@pytest.fixture
def endpoint(mock_table):
    """Create StorageEndpoint with mock table."""
    return StorageEndpoint(mock_table)


class TestStorageEndpointAdd:
    """Tests for StorageEndpoint.add() method."""

    async def test_add_storage(self, endpoint, mock_table):
        """add() creates storage and returns it."""
        result = await endpoint.add(
            tenant_id="t1",
            name="HOME",
            protocol="local",
            config={"base_path": "/data"},
        )
        mock_table.add.assert_called_once()
        assert result["tenant_id"] == "t1"
        assert result["name"] == "HOME"

    async def test_add_storage_without_config(self, endpoint, mock_table):
        """add() uses empty config when not provided."""
        await endpoint.add(tenant_id="t1", name="SALES", protocol="s3")
        call_args = mock_table.add.call_args[0][0]
        assert call_args["config"] == {}


class TestStorageEndpointGet:
    """Tests for StorageEndpoint.get() method."""

    async def test_get_storage(self, endpoint, mock_table):
        """get() returns storage configuration."""
        result = await endpoint.get("t1", "HOME")
        mock_table.get.assert_called_once_with("t1", "HOME")
        assert result["name"] == "HOME"


class TestStorageEndpointList:
    """Tests for StorageEndpoint.list() method."""

    async def test_list_storages(self, endpoint, mock_table):
        """list() returns all storages for tenant."""
        mock_table.list_all = AsyncMock(return_value=[
            {"name": "HOME", "protocol": "local"},
            {"name": "SALES", "protocol": "s3"},
        ])
        result = await endpoint.list("t1")
        mock_table.list_all.assert_called_once_with(tenant_id="t1")
        assert len(result) == 2


class TestStorageEndpointDelete:
    """Tests for StorageEndpoint.delete() method."""

    async def test_delete_storage(self, endpoint, mock_table):
        """delete() removes storage and returns status."""
        result = await endpoint.delete("t1", "HOME")
        mock_table.remove.assert_called_once_with("t1", "HOME")
        assert result["ok"] is True
        assert result["tenant_id"] == "t1"
        assert result["name"] == "HOME"

    async def test_delete_not_found(self, endpoint, mock_table):
        """delete() returns ok=False when not found."""
        mock_table.remove = AsyncMock(return_value=False)
        result = await endpoint.delete("t1", "NONEXISTENT")
        assert result["ok"] is False
