# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for api_base: endpoint introspection and route generation.

These tests verify that api_base correctly generates FastAPI routes from
endpoint classes, and that calling these routes exercises the full stack
(endpoint -> table -> DB).
"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.mail_proxy.interface.api_base import register_endpoint
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
