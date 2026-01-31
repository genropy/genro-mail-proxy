# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for api_base: endpoint introspection and route generation.

These tests verify that api_base correctly generates FastAPI routes from
endpoint classes, and that calling these routes exercises the full stack
(endpoint -> table -> DB).
"""

from __future__ import annotations

import secrets
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from core.mail_proxy.interface.api_base import (
    _count_params_fallback,
    _create_model_fallback,
    _get_http_method_fallback,
    register_endpoint,
    require_admin_token,
    require_token,
    verify_tenant_token,
)
from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with schema."""
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
    await proxy.db.connect()
    await proxy.db.check_structure()
    yield proxy.db
    await proxy.close()


@pytest.fixture
def app():
    """Create FastAPI app."""
    return FastAPI()


# =============================================================================
# Account Endpoint Tests via api_base
# =============================================================================

class TestAccountEndpointViaApi:
    """Test AccountEndpoint through generated API routes."""

    @pytest.fixture
    async def client(self, app, db):
        """Create test client with account endpoint registered."""
        from core.mail_proxy.entities.account import AccountEndpoint

        accounts_table = db.table("accounts")
        endpoint = AccountEndpoint(accounts_table)
        register_endpoint(app, endpoint)
        return TestClient(app)

    def test_add_account_creates_route(self, client):
        """POST /accounts/add creates account."""
        response = client.post("/accounts/add", json={
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "smtp1"
        assert data["host"] == "smtp.example.com"

    def test_add_account_with_all_fields(self, client):
        """POST /accounts/add with all optional fields."""
        response = client.post("/accounts/add", json={
            "id": "smtp2",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 465,
            "user": "user@example.com",
            "password": "secret",
            "use_tls": True,
            "batch_size": 50,
            "ttl": 600,
            "limit_per_minute": 10,
            "limit_per_hour": 100,
            "limit_per_day": 1000,
            "limit_behavior": "reject",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["user"] == "user@example.com"
        assert data["batch_size"] == 50

    def test_get_account_returns_data(self, client):
        """GET /accounts/get returns account data."""
        # First create
        client.post("/accounts/add", json={
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })

        # Then get
        response = client.get("/accounts/get", params={
            "tenant_id": "t1",
            "account_id": "smtp1",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "smtp1"

    def test_list_accounts_empty(self, client):
        """GET /accounts/list returns empty list."""
        response = client.get("/accounts/list", params={"tenant_id": "t1"})
        assert response.status_code == 200
        assert response.json() == []

    def test_list_accounts_returns_all(self, client):
        """GET /accounts/list returns all accounts for tenant."""
        # Create two accounts
        client.post("/accounts/add", json={
            "id": "smtp1", "tenant_id": "t1", "host": "a.com", "port": 25
        })
        client.post("/accounts/add", json={
            "id": "smtp2", "tenant_id": "t1", "host": "b.com", "port": 25
        })

        response = client.get("/accounts/list", params={"tenant_id": "t1"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 2

    def test_delete_account(self, client):
        """POST /accounts/delete removes account."""
        # Create
        client.post("/accounts/add", json={
            "id": "smtp1", "tenant_id": "t1", "host": "a.com", "port": 25
        })

        # Delete via POST
        response = client.post("/accounts/delete", json={
            "tenant_id": "t1",
            "account_id": "smtp1",
        })
        assert response.status_code == 200

        # Verify gone
        response = client.get("/accounts/list", params={"tenant_id": "t1"})
        assert response.json() == []


# =============================================================================
# Tenant Endpoint Tests via api_base
# =============================================================================

class TestTenantEndpointViaApi:
    """Test TenantEndpoint through generated API routes."""

    @pytest.fixture
    async def client(self, app, db):
        """Create test client with tenant endpoint registered."""
        from core.mail_proxy.entities.tenant import TenantEndpoint

        tenants_table = db.table("tenants")
        endpoint = TenantEndpoint(tenants_table)
        register_endpoint(app, endpoint)
        return TestClient(app)

    def test_add_tenant_minimal(self, client):
        """POST /tenants/add creates tenant with minimal data."""
        response = client.post("/tenants/add", json={
            "id": "acme",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "acme"

    def test_add_tenant_with_name(self, client):
        """POST /tenants/add creates tenant with name."""
        response = client.post("/tenants/add", json={
            "id": "acme",
            "name": "ACME Corporation",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "ACME Corporation"

    def test_add_tenant_with_client_config(self, client):
        """POST /tenants/add with client configuration."""
        response = client.post("/tenants/add", json={
            "id": "acme",
            "client_base_url": "https://api.acme.com",
            "client_sync_path": "/webhooks/mail",
            "client_attachment_path": "/files",
            "client_auth": {"method": "bearer", "token": "secret"},
        })
        assert response.status_code == 200
        data = response.json()
        assert data["client_base_url"] == "https://api.acme.com"

    def test_get_tenant(self, client):
        """GET /tenants/get returns tenant data."""
        client.post("/tenants/add", json={"id": "acme", "name": "ACME"})

        response = client.get("/tenants/get", params={"tenant_id": "acme"})
        assert response.status_code == 200
        assert response.json()["name"] == "ACME"

    def test_list_tenants(self, client):
        """GET /tenants/list returns all tenants."""
        client.post("/tenants/add", json={"id": "t1"})
        client.post("/tenants/add", json={"id": "t2"})

        response = client.get("/tenants/list")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 2

    def test_delete_tenant(self, client):
        """POST /tenants/delete removes tenant."""
        client.post("/tenants/add", json={"id": "temp"})

        response = client.post("/tenants/delete", json={"tenant_id": "temp"})
        assert response.status_code == 200

    def test_update_tenant(self, client):
        """POST /tenants/update modifies tenant."""
        client.post("/tenants/add", json={"id": "acme", "name": "Old Name"})

        response = client.post("/tenants/update", json={
            "tenant_id": "acme",
            "name": "New Name",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "New Name"


# =============================================================================
# Message Endpoint Tests via api_base
# =============================================================================

class TestMessageEndpointViaApi:
    """Test MessageEndpoint through generated API routes."""

    @pytest.fixture
    async def client(self, app, db):
        """Create test client with message endpoint registered."""
        from core.mail_proxy.entities.message import MessageEndpoint
        from core.mail_proxy.entities.account import AccountEndpoint
        from core.mail_proxy.entities.tenant import TenantEndpoint

        # Register all needed endpoints
        tenants_table = db.table("tenants")
        accounts_table = db.table("accounts")
        messages_table = db.table("messages")

        register_endpoint(app, TenantEndpoint(tenants_table))
        register_endpoint(app, AccountEndpoint(accounts_table))
        register_endpoint(app, MessageEndpoint(messages_table))

        client = TestClient(app)

        # Setup: create tenant and account
        client.post("/tenants/add", json={"id": "t1"})
        client.post("/accounts/add", json={
            "id": "smtp1", "tenant_id": "t1", "host": "smtp.test.com", "port": 25
        })

        return client

    def test_add_message(self, client):
        """POST /messages/add creates message."""
        response = client.post("/messages/add", json={
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "smtp1",
            "from_addr": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test",
            "body": "Hello",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "msg1"

    def test_add_message_with_all_fields(self, client):
        """POST /messages/add with all optional fields."""
        response = client.post("/messages/add", json={
            "id": "msg2",
            "tenant_id": "t1",
            "account_id": "smtp1",
            "from_addr": "sender@test.com",
            "to": ["to@test.com"],
            "subject": "Full Test",
            "body": "<html>Hello</html>",
            "cc": ["cc@test.com"],
            "bcc": ["bcc@test.com"],
            "reply_to": "reply@test.com",
            "content_type": "html",
            "priority": 1,
            "batch_code": "campaign-001",
            "headers": {"X-Custom": "value"},
        })
        assert response.status_code == 200

    def test_get_message(self, client):
        """GET /messages/get returns message data."""
        client.post("/messages/add", json={
            "id": "msg1", "tenant_id": "t1", "account_id": "smtp1",
            "from_addr": "a@b.com", "to": ["c@d.com"],
            "subject": "Test", "body": "Hi",
        })

        response = client.get("/messages/get", params={
            "message_id": "msg1",
            "tenant_id": "t1",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == "msg1"
        assert "status" in data

    def test_list_messages(self, client):
        """GET /messages/list returns messages."""
        client.post("/messages/add", json={
            "id": "msg1", "tenant_id": "t1", "account_id": "smtp1",
            "from_addr": "a@b.com", "to": ["c@d.com"],
            "subject": "Test", "body": "Hi",
        })

        response = client.get("/messages/list", params={"tenant_id": "t1"})
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1

    def test_list_messages_active_only(self, client):
        """GET /messages/list with active_only filter."""
        response = client.get("/messages/list", params={
            "tenant_id": "t1",
            "active_only": True,
        })
        assert response.status_code == 200

    def test_count_active(self, client):
        """GET /messages/count_active returns count."""
        response = client.get("/messages/count_active")
        assert response.status_code == 200
        assert isinstance(response.json(), int)

    def test_count_pending_for_tenant(self, client):
        """GET /messages/count_pending_for_tenant returns count."""
        response = client.get("/messages/count_pending_for_tenant", params={
            "tenant_id": "t1",
        })
        assert response.status_code == 200
        assert isinstance(response.json(), int)


# =============================================================================
# Instance Endpoint Tests via api_base
# =============================================================================

class TestInstanceEndpointViaApi:
    """Test InstanceEndpoint through generated API routes."""

    @pytest.fixture
    def app(self, db):
        """Create FastAPI app with instance endpoint registered."""
        from core.mail_proxy.entities.instance import InstanceEndpoint

        instance_table = db.table("instance")
        endpoint = InstanceEndpoint(instance_table)

        app = FastAPI()
        register_endpoint(app, endpoint)
        return app

    @pytest.fixture
    def client(self, app):
        """Create test client."""
        return TestClient(app)

    def test_health_route(self, client):
        """GET /instance/health returns status ok."""
        response = client.get("/instance/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_status_route(self, client):
        """GET /instance/status returns ok and active."""
        response = client.get("/instance/status")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert "active" in data

    def test_get_route(self, client):
        """GET /instance/get returns instance configuration."""
        response = client.get("/instance/get")
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    def test_update_route(self, client):
        """POST /instance/update modifies configuration."""
        response = client.post("/instance/update", json={"name": "test-instance"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    def test_run_now_route(self, client):
        """POST /instance/run_now triggers dispatch."""
        response = client.post("/instance/run_now", json={})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True

    def test_suspend_route(self, client):
        """POST /instance/suspend pauses sending."""
        response = client.post("/instance/suspend", json={"tenant_id": "t1"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["tenant_id"] == "t1"

    def test_activate_route(self, client):
        """POST /instance/activate resumes sending."""
        response = client.post("/instance/activate", json={"tenant_id": "t1"})
        assert response.status_code == 200
        data = response.json()
        assert data["ok"] is True
        assert data["tenant_id"] == "t1"


# =============================================================================
# Route Generation Tests
# =============================================================================

class TestRouteGeneration:
    """Test that api_base generates correct routes."""

    def test_routes_created_for_all_methods(self, app, db):
        """All endpoint methods become routes."""
        from core.mail_proxy.entities.account import AccountEndpoint

        accounts_table = db.table("accounts")
        endpoint = AccountEndpoint(accounts_table)
        register_endpoint(app, endpoint)

        routes = [r.path for r in app.routes]
        assert "/accounts/add" in routes
        assert "/accounts/get" in routes
        assert "/accounts/list" in routes
        assert "/accounts/delete" in routes

    def test_correct_http_methods(self, app, db):
        """Routes use correct HTTP methods."""
        from core.mail_proxy.entities.account import AccountEndpoint

        accounts_table = db.table("accounts")
        endpoint = AccountEndpoint(accounts_table)
        register_endpoint(app, endpoint)

        route_methods = {r.path: list(r.methods) for r in app.routes if hasattr(r, 'methods')}

        assert "POST" in route_methods.get("/accounts/add", [])
        assert "GET" in route_methods.get("/accounts/get", [])
        assert "GET" in route_methods.get("/accounts/list", [])
        assert "POST" in route_methods.get("/accounts/delete", [])

    def test_custom_prefix(self, app, db):
        """Custom prefix changes route paths."""
        from core.mail_proxy.entities.account import AccountEndpoint

        accounts_table = db.table("accounts")
        endpoint = AccountEndpoint(accounts_table)
        register_endpoint(app, endpoint, prefix="/api/v1/smtp")

        routes = [r.path for r in app.routes]
        assert "/api/v1/smtp/add" in routes


# =============================================================================
# Fallback Functions Tests (for duck-typed endpoints)
# =============================================================================


class TestHttpMethodFallback:
    """Tests for _get_http_method_fallback function."""

    def test_add_prefix_returns_post(self):
        """Methods starting with 'add' should return POST."""
        assert _get_http_method_fallback("add_item") == "POST"
        assert _get_http_method_fallback("addUser") == "POST"

    def test_create_prefix_returns_post(self):
        """Methods starting with 'create' should return POST."""
        assert _get_http_method_fallback("create_record") == "POST"
        assert _get_http_method_fallback("createNew") == "POST"

    def test_post_prefix_returns_post(self):
        """Methods starting with 'post' should return POST."""
        assert _get_http_method_fallback("post_data") == "POST"

    def test_run_prefix_returns_post(self):
        """Methods starting with 'run' should return POST."""
        assert _get_http_method_fallback("run_now") == "POST"

    def test_suspend_prefix_returns_post(self):
        """Methods starting with 'suspend' should return POST."""
        assert _get_http_method_fallback("suspend_account") == "POST"

    def test_activate_prefix_returns_post(self):
        """Methods starting with 'activate' should return POST."""
        assert _get_http_method_fallback("activate_user") == "POST"

    def test_delete_prefix_returns_delete(self):
        """Methods starting with 'delete' should return DELETE."""
        assert _get_http_method_fallback("delete_item") == "DELETE"

    def test_remove_prefix_returns_delete(self):
        """Methods starting with 'remove' should return DELETE."""
        assert _get_http_method_fallback("remove_user") == "DELETE"

    def test_update_prefix_returns_patch(self):
        """Methods starting with 'update' should return PATCH."""
        assert _get_http_method_fallback("update_record") == "PATCH"

    def test_patch_prefix_returns_patch(self):
        """Methods starting with 'patch' should return PATCH."""
        assert _get_http_method_fallback("patch_data") == "PATCH"

    def test_set_prefix_returns_put(self):
        """Methods starting with 'set' should return PUT."""
        assert _get_http_method_fallback("set_value") == "PUT"

    def test_put_prefix_returns_put(self):
        """Methods starting with 'put' should return PUT."""
        assert _get_http_method_fallback("put_item") == "PUT"

    def test_other_prefix_returns_get(self):
        """Methods without recognized prefix should return GET."""
        assert _get_http_method_fallback("list_items") == "GET"
        assert _get_http_method_fallback("get_data") == "GET"
        assert _get_http_method_fallback("fetch_records") == "GET"
        assert _get_http_method_fallback("unknown") == "GET"


class TestCountParamsFallback:
    """Tests for _count_params_fallback function."""

    def test_no_params_no_self(self):
        """Function with no params should return 0."""

        async def method():
            pass

        assert _count_params_fallback(method) == 0

    def test_only_self_returns_zero(self):
        """Method with only self should return 0 (self excluded)."""

        async def method(self):
            pass

        assert _count_params_fallback(method) == 0

    def test_regular_function_params(self):
        """Regular function params should be counted."""

        async def method(a, b, c):
            pass

        assert _count_params_fallback(method) == 3

    def test_excludes_self_when_present(self):
        """self parameter should be excluded."""

        async def method(self, a, b):
            pass

        assert _count_params_fallback(method) == 2


class TestCreateModelFallback:
    """Tests for _create_model_fallback function."""

    def test_creates_model_with_params(self):
        """Should create Pydantic model from method params."""

        async def my_method(self, name: str, value: int):
            pass

        model = _create_model_fallback(my_method, "my_method")
        assert model.__name__ == "MyMethodRequest"
        assert "name" in model.model_fields
        assert "value" in model.model_fields

    def test_required_and_optional_params(self):
        """Required params have no default, optional have default."""

        async def my_method(self, required: str, optional: int = 10):
            pass

        model = _create_model_fallback(my_method, "my_method")
        fields = model.model_fields
        assert fields["required"].is_required()
        assert not fields["optional"].is_required()

    def test_missing_annotation_uses_any(self):
        """Params without annotation should use Any."""

        async def my_method(self, unknown):
            pass

        model = _create_model_fallback(my_method, "my_method")
        # Should not raise
        assert "unknown" in model.model_fields

    def test_model_name_title_case(self):
        """Model name should be TitleCase without underscores."""

        async def list_all_items(self):
            pass

        model = _create_model_fallback(list_all_items, "list_all_items")
        assert model.__name__ == "ListAllItemsRequest"


# =============================================================================
# Duck-Typed Endpoint Tests
# =============================================================================


class TestDuckTypedEndpoint:
    """Tests for non-BaseEndpoint classes (duck-typed)."""

    @pytest.fixture
    def duck_endpoint(self):
        """Create a duck-typed endpoint (not inheriting BaseEndpoint)."""

        class DuckEndpoint:
            name = "ducks"

            async def list(self) -> list:
                return [{"id": "1"}]

            async def get(self, duck_id: str) -> dict:
                return {"id": duck_id}

            async def add(self, id: str, name: str) -> dict:
                return {"id": id, "name": name}

            async def delete(self, duck_id: str) -> bool:
                return True

            async def update(self, duck_id: str, name: str) -> dict:
                return {"id": duck_id, "name": name}

            async def set_default(self, duck_id: str) -> dict:
                return {"id": duck_id, "default": True}

            def _private(self):
                pass

            def sync_method(self):
                pass

        return DuckEndpoint()

    def test_registers_duck_typed_endpoint(self, duck_endpoint):
        """Should register duck-typed endpoint methods as routes."""
        app = FastAPI()
        register_endpoint(app, duck_endpoint)

        routes = [r.path for r in app.routes]
        assert "/ducks/list" in routes
        assert "/ducks/get" in routes
        assert "/ducks/add" in routes

    def test_excludes_private_and_sync_methods(self, duck_endpoint):
        """Private and sync methods should not become routes."""
        app = FastAPI()
        register_endpoint(app, duck_endpoint)

        routes = [r.path for r in app.routes]
        assert "/ducks/_private" not in routes
        assert "/ducks/sync_method" not in routes

    def test_duck_http_methods_inferred(self, duck_endpoint):
        """HTTP methods should be inferred from method names."""
        app = FastAPI()
        register_endpoint(app, duck_endpoint)

        route_methods = {r.path: list(r.methods) for r in app.routes if hasattr(r, "methods")}

        assert "GET" in route_methods.get("/ducks/list", [])
        assert "GET" in route_methods.get("/ducks/get", [])
        assert "POST" in route_methods.get("/ducks/add", [])
        # delete with <=3 params should be DELETE
        assert "DELETE" in route_methods.get("/ducks/delete", [])
        # update should be PATCH
        assert "PATCH" in route_methods.get("/ducks/update", [])
        # set_ should be PUT
        assert "PUT" in route_methods.get("/ducks/set_default", [])

    def test_duck_endpoint_calls_work(self, duck_endpoint):
        """Calling duck-typed endpoint routes should work."""
        app = FastAPI()
        register_endpoint(app, duck_endpoint)
        client = TestClient(app)

        # GET list
        response = client.get("/ducks/list")
        assert response.status_code == 200
        assert response.json() == [{"id": "1"}]

        # GET with params
        response = client.get("/ducks/get", params={"duck_id": "d1"})
        assert response.status_code == 200
        assert response.json()["id"] == "d1"

        # POST add
        response = client.post("/ducks/add", json={"id": "d2", "name": "Donald"})
        assert response.status_code == 200
        assert response.json()["name"] == "Donald"


# =============================================================================
# Authentication Tests
# =============================================================================


class TestVerifyTenantToken:
    """Tests for verify_tenant_token function."""

    @pytest.mark.asyncio
    async def test_no_token_no_global_allows(self):
        """No token with no global token configured should allow access."""
        # Should not raise
        await verify_tenant_token(
            tenant_id="t1",
            api_token=None,
            global_token=None,
        )

    @pytest.mark.asyncio
    async def test_no_token_with_global_raises(self):
        """No token with global configured should raise 401."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await verify_tenant_token(
                tenant_id="t1",
                api_token=None,
                global_token="admin-secret",
            )
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_global_token_allows(self):
        """Global token should allow access to any tenant."""
        # Should not raise
        await verify_tenant_token(
            tenant_id="t1",
            api_token="admin-secret",
            global_token="admin-secret",
        )

    @pytest.mark.asyncio
    async def test_wrong_token_raises(self):
        """Wrong token should raise 401."""
        from fastapi import HTTPException

        # Clear _service to test without tenant lookup
        import core.mail_proxy.interface.api_base as api_base

        original_service = api_base._service
        api_base._service = None

        try:
            with pytest.raises(HTTPException) as exc_info:
                await verify_tenant_token(
                    tenant_id="t1",
                    api_token="wrong-token",
                    global_token="admin-secret",
                )
            assert exc_info.value.status_code == 401
        finally:
            api_base._service = original_service

    @pytest.mark.asyncio
    async def test_tenant_token_own_tenant(self):
        """Tenant token should allow access to own tenant."""
        import core.mail_proxy.interface.api_base as api_base

        # Mock _service with tenant lookup
        mock_service = MagicMock()
        mock_table = MagicMock()
        mock_table.get_tenant_by_token = AsyncMock(
            return_value={"id": "t1", "name": "Tenant 1"}
        )
        mock_service.db.table.return_value = mock_table
        original_service = api_base._service
        api_base._service = mock_service

        try:
            # Should not raise
            await verify_tenant_token(
                tenant_id="t1",
                api_token="tenant-token",
                global_token="admin-secret",
            )
        finally:
            api_base._service = original_service

    @pytest.mark.asyncio
    async def test_tenant_token_wrong_tenant_raises(self):
        """Tenant token for wrong tenant should raise 401."""
        from fastapi import HTTPException

        import core.mail_proxy.interface.api_base as api_base

        # Mock _service with tenant lookup
        mock_service = MagicMock()
        mock_table = MagicMock()
        mock_table.get_tenant_by_token = AsyncMock(
            return_value={"id": "t2", "name": "Tenant 2"}  # Different tenant!
        )
        mock_service.db.table.return_value = mock_table
        original_service = api_base._service
        api_base._service = mock_service

        try:
            with pytest.raises(HTTPException) as exc_info:
                await verify_tenant_token(
                    tenant_id="t1",  # Requesting t1
                    api_token="tenant-token",  # But token is for t2
                    global_token="admin-secret",
                )
            assert exc_info.value.status_code == 401
            assert "not authorized for this tenant" in str(exc_info.value.detail)
        finally:
            api_base._service = original_service


class TestRequireAdminToken:
    """Tests for require_admin_token dependency."""

    @pytest.fixture
    def mock_request(self):
        """Create mock request with app state."""
        request = MagicMock(spec=Request)
        request.app.state.api_token = "admin-secret"
        return request

    @pytest.mark.asyncio
    async def test_no_token_no_config_allows(self):
        """No token with no config should allow."""
        request = MagicMock(spec=Request)
        request.app.state.api_token = None

        # Should not raise
        await require_admin_token(request, api_token=None)

    @pytest.mark.asyncio
    async def test_no_token_with_config_raises(self, mock_request):
        """No token with config should raise 401."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await require_admin_token(mock_request, api_token=None)
        assert exc_info.value.status_code == 401
        assert "Admin token required" in str(exc_info.value.detail)

    @pytest.mark.asyncio
    async def test_correct_admin_token_allows(self, mock_request):
        """Correct admin token should allow."""
        # Should not raise
        await require_admin_token(mock_request, api_token="admin-secret")

    @pytest.mark.asyncio
    async def test_wrong_token_raises(self, mock_request):
        """Wrong token should raise 401."""
        from fastapi import HTTPException

        import core.mail_proxy.interface.api_base as api_base

        original_service = api_base._service
        api_base._service = None

        try:
            with pytest.raises(HTTPException) as exc_info:
                await require_admin_token(mock_request, api_token="wrong")
            assert exc_info.value.status_code == 401
        finally:
            api_base._service = original_service

    @pytest.mark.asyncio
    async def test_tenant_token_raises_403(self, mock_request):
        """Tenant token on admin endpoint should raise 403."""
        from fastapi import HTTPException

        import core.mail_proxy.interface.api_base as api_base

        mock_service = MagicMock()
        mock_table = MagicMock()
        mock_table.get_tenant_by_token = AsyncMock(return_value={"id": "t1"})
        mock_service.db.table.return_value = mock_table
        original_service = api_base._service
        api_base._service = mock_service

        try:
            with pytest.raises(HTTPException) as exc_info:
                await require_admin_token(mock_request, api_token="tenant-token")
            assert exc_info.value.status_code == 403
            assert "Admin token required" in str(exc_info.value.detail)
        finally:
            api_base._service = original_service


class TestRequireToken:
    """Tests for require_token dependency."""

    @pytest.fixture
    def mock_request(self):
        """Create mock request with app state."""
        request = MagicMock(spec=Request)
        request.app.state.api_token = "admin-secret"
        request.state = MagicMock()
        return request

    @pytest.mark.asyncio
    async def test_no_token_no_config_allows(self):
        """No token with no config should allow."""
        request = MagicMock(spec=Request)
        request.app.state.api_token = None
        request.state = MagicMock()

        await require_token(request, api_token=None)

    @pytest.mark.asyncio
    async def test_no_token_with_config_raises(self, mock_request):
        """No token with config should raise 401."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await require_token(mock_request, api_token=None)
        assert exc_info.value.status_code == 401

    @pytest.mark.asyncio
    async def test_admin_token_sets_is_admin(self, mock_request):
        """Admin token should set is_admin=True."""
        await require_token(mock_request, api_token="admin-secret")
        assert mock_request.state.is_admin is True

    @pytest.mark.asyncio
    async def test_tenant_token_sets_tenant_id(self, mock_request):
        """Tenant token should set token_tenant_id."""
        import core.mail_proxy.interface.api_base as api_base

        mock_service = MagicMock()
        mock_table = MagicMock()
        mock_table.get_tenant_by_token = AsyncMock(return_value={"id": "t1"})
        mock_service.db.table.return_value = mock_table
        original_service = api_base._service
        api_base._service = mock_service

        try:
            await require_token(mock_request, api_token="tenant-token")
            assert mock_request.state.token_tenant_id == "t1"
            assert mock_request.state.is_admin is False
        finally:
            api_base._service = original_service

    @pytest.mark.asyncio
    async def test_invalid_token_raises(self, mock_request):
        """Invalid token should raise 401."""
        from fastapi import HTTPException

        import core.mail_proxy.interface.api_base as api_base

        original_service = api_base._service
        api_base._service = None

        try:
            with pytest.raises(HTTPException) as exc_info:
                await require_token(mock_request, api_token="invalid")
            assert exc_info.value.status_code == 401
        finally:
            api_base._service = original_service


# =============================================================================
# Create App Tests
# =============================================================================


class TestCreateApp:
    """Tests for create_app function."""

    @pytest.fixture
    async def proxy(self, tmp_path):
        """Create a MailProxy instance."""
        proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
        await proxy.db.connect()
        await proxy.db.check_structure()
        yield proxy
        await proxy.close()

    def test_create_app_returns_fastapi(self, proxy):
        """create_app should return FastAPI instance."""
        from core.mail_proxy.interface.api_base import create_app

        app = create_app(proxy)
        assert isinstance(app, FastAPI)

    def test_create_app_sets_api_token(self, proxy):
        """create_app should store api_token in app.state."""
        from core.mail_proxy.interface.api_base import create_app

        app = create_app(proxy, api_token="my-secret")
        assert app.state.api_token == "my-secret"

    def test_create_app_sets_tenant_tokens_enabled(self, proxy):
        """create_app should store tenant_tokens_enabled in app.state."""
        from core.mail_proxy.interface.api_base import create_app

        app = create_app(proxy, tenant_tokens_enabled=True)
        assert app.state.tenant_tokens_enabled is True

    def test_create_app_registers_routes(self, proxy):
        """create_app should register entity endpoints."""
        from core.mail_proxy.interface.api_base import create_app

        app = create_app(proxy)
        routes = [r.path for r in app.routes]

        # Should have health and metrics
        assert "/health" in routes
        assert "/metrics" in routes

        # Should have entity routes
        assert any("/tenants/" in r for r in routes)
        assert any("/accounts/" in r for r in routes)
        assert any("/messages/" in r for r in routes)

    def test_create_app_custom_lifespan(self, proxy):
        """create_app should accept custom lifespan."""
        from collections.abc import AsyncGenerator
        from contextlib import asynccontextmanager

        from core.mail_proxy.interface.api_base import create_app

        custom_called = []

        @asynccontextmanager
        async def custom_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
            custom_called.append("start")
            yield
            custom_called.append("stop")

        app = create_app(proxy, lifespan=custom_lifespan)
        assert isinstance(app, FastAPI)
        # Lifespan is not called until app runs

    def test_health_endpoint(self, proxy):
        """Health endpoint should return status."""
        from core.mail_proxy.interface.api_base import create_app

        app = create_app(proxy)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert "status" in data

    def test_metrics_endpoint(self, proxy):
        """Metrics endpoint should return Prometheus format."""
        from tools.prometheus.metrics import MailMetrics

        from core.mail_proxy.interface.api_base import create_app

        # Ensure proxy has metrics initialized
        if not hasattr(proxy, "metrics") or proxy.metrics is None:
            proxy.metrics = MailMetrics()

        app = create_app(proxy)
        client = TestClient(app, raise_server_exceptions=False)

        response = client.get("/metrics")
        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]


# =============================================================================
# Validation Error Handler Test
# =============================================================================


class TestValidationErrorHandler:
    """Tests for validation error handling."""

    def test_validation_error_returns_422(self, app, db):
        """Invalid request should return 422 with details."""
        from core.mail_proxy.entities.account import AccountEndpoint

        accounts_table = db.table("accounts")
        endpoint = AccountEndpoint(accounts_table)
        register_endpoint(app, endpoint)
        client = TestClient(app)

        # Missing required fields
        response = client.post("/accounts/add", json={})
        assert response.status_code == 422
        assert "detail" in response.json()
