# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Test endpoint introspection for API and CLI generation."""

import inspect

import pytest
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.mail_proxy.api_base import register_endpoint as register_api
from core.mail_proxy.cli_base import register_endpoint as register_cli


# --- Mock Endpoint for Testing ---

class MockTable:
    """Mock table for testing."""

    def __init__(self):
        self.data = {}

    async def add(self, record: dict) -> str:
        key = f"{record['tenant_id']}:{record['id']}"
        self.data[key] = record
        return key

    async def get(self, tenant_id: str, id: str) -> dict:
        key = f"{tenant_id}:{id}"
        if key not in self.data:
            raise ValueError(f"Not found: {key}")
        return self.data[key]

    async def list_all(self, tenant_id: str) -> list[dict]:
        return [v for k, v in self.data.items() if k.startswith(f"{tenant_id}:")]

    async def remove(self, tenant_id: str, id: str) -> None:
        key = f"{tenant_id}:{id}"
        self.data.pop(key, None)


class MockEndpoint:
    """Mock endpoint for testing introspection."""

    name = "items"

    def __init__(self, table: MockTable):
        self.table = table

    async def add(self, id: str, tenant_id: str, value: int, enabled: bool = True) -> dict:
        """Add a new item."""
        data = {"id": id, "tenant_id": tenant_id, "value": value, "enabled": enabled}
        await self.table.add(data)
        return await self.table.get(tenant_id, id)

    async def get(self, tenant_id: str, item_id: str) -> dict:
        """Get an item by ID."""
        return await self.table.get(tenant_id, item_id)

    async def list(self, tenant_id: str) -> list[dict]:
        """List all items for a tenant."""
        return await self.table.list_all(tenant_id)

    async def delete(self, tenant_id: str, item_id: str) -> None:
        """Delete an item."""
        await self.table.remove(tenant_id, item_id)


# --- API Tests ---

class TestApiBase:
    """Test FastAPI route generation from endpoints."""

    @pytest.fixture
    def app(self):
        table = MockTable()
        endpoint = MockEndpoint(table)
        app = FastAPI()
        register_api(app, endpoint)
        return app

    @pytest.fixture
    def client(self, app):
        return TestClient(app)

    def test_routes_created(self, app):
        """Verify all expected routes are created."""
        routes = [r.path for r in app.routes]
        assert "/items/add" in routes
        assert "/items/get" in routes
        assert "/items/list" in routes
        assert "/items/delete" in routes

    def test_add_post(self, client):
        """Test POST route for add method."""
        response = client.post("/items/add", json={
            "id": "item1",
            "tenant_id": "tenant1",
            "value": 42,
            "enabled": True
        })
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "item1"
        assert data["value"] == 42

    def test_get_query(self, client):
        """Test GET route with query parameters."""
        # First add an item
        client.post("/items/add", json={
            "id": "item2",
            "tenant_id": "tenant1",
            "value": 100
        })

        # Then get it
        response = client.get("/items/get", params={
            "tenant_id": "tenant1",
            "item_id": "item2"
        })
        assert response.status_code == 200
        assert response.json()["value"] == 100

    def test_list_query(self, client):
        """Test list endpoint."""
        # Add items
        client.post("/items/add", json={"id": "a", "tenant_id": "t1", "value": 1})
        client.post("/items/add", json={"id": "b", "tenant_id": "t1", "value": 2})
        client.post("/items/add", json={"id": "c", "tenant_id": "t2", "value": 3})

        # List for t1
        response = client.get("/items/list", params={"tenant_id": "t1"})
        assert response.status_code == 200
        items = response.json()
        assert len(items) == 2

    def test_delete(self, client):
        """Test DELETE route."""
        # Add then delete
        client.post("/items/add", json={"id": "x", "tenant_id": "t1", "value": 0})
        response = client.delete("/items/delete", params={
            "tenant_id": "t1",
            "item_id": "x"
        })
        assert response.status_code == 200

        # Verify deleted
        response = client.get("/items/list", params={"tenant_id": "t1"})
        assert response.json() == []

    def test_openapi_schema(self, client):
        """Verify OpenAPI schema is generated."""
        response = client.get("/openapi.json")
        assert response.status_code == 200
        schema = response.json()
        assert "/items/add" in schema["paths"]
        assert "post" in schema["paths"]["/items/add"]


# --- CLI Tests ---

class TestCliBase:
    """Test Click command generation from endpoints."""

    @pytest.fixture
    def cli(self):
        import click

        table = MockTable()
        endpoint = MockEndpoint(table)

        @click.group()
        def cli():
            pass

        register_cli(cli, endpoint)
        return cli

    @pytest.fixture
    def runner(self):
        return CliRunner()

    def test_group_created(self, cli, runner):
        """Verify endpoint group is created."""
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "items" in result.output

    def test_commands_created(self, cli, runner):
        """Verify all commands are created."""
        result = runner.invoke(cli, ["items", "--help"])
        assert result.exit_code == 0
        assert "add" in result.output
        assert "get" in result.output
        assert "list" in result.output
        assert "delete" in result.output

    def test_add_command(self, cli, runner):
        """Test add command with arguments and options."""
        # Arguments are added in reverse order by Click decorators
        # So we pass: value, tenant_id, id
        result = runner.invoke(cli, [
            "items", "add",
            "99",  # value (argument)
            "mytenant",  # tenant_id (argument)
            "myid",  # id (argument)
            "--enabled"  # bool flag
        ])
        assert result.exit_code == 0, f"Failed with: {result.output}"
        assert "myid" in result.output

    def test_list_command(self, cli, runner):
        """Test list command."""
        # Add first
        runner.invoke(cli, ["items", "add", "i1", "t1", "10"])

        # List
        result = runner.invoke(cli, ["items", "list", "t1"])
        assert result.exit_code == 0


# --- Signature Introspection Tests ---

class TestSignatureIntrospection:
    """Test that signature introspection works correctly."""

    def test_extract_parameters(self):
        """Test parameter extraction from method signature."""
        endpoint = MockEndpoint(MockTable())
        sig = inspect.signature(endpoint.add)

        # Bound methods don't include 'self' in signature
        params = list(sig.parameters.items())
        assert params[0][0] == "id"
        assert params[1][0] == "tenant_id"
        assert params[2][0] == "value"
        assert params[3][0] == "enabled"

        # Check defaults
        assert params[3][1].default is True

    def test_docstring_preserved(self):
        """Test that docstrings are preserved."""
        endpoint = MockEndpoint(MockTable())
        assert endpoint.add.__doc__ == "Add a new item."
        assert endpoint.get.__doc__ == "Get an item by ID."
