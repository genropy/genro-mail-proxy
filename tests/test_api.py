import types

import pytest
from fastapi.testclient import TestClient

from mail_proxy import api
from mail_proxy.api import API_TOKEN_HEADER_NAME, create_app

API_TOKEN = "secret-token"


class DummyService:
    def __init__(self):
        self.calls = []
        self.metrics = types.SimpleNamespace(generate_latest=lambda: b"metrics-data")
        self.messages = []

    async def handle_command(self, cmd, payload):
        self.calls.append((cmd, payload))
        if cmd == "addMessages":
            return {"ok": True, "queued": len(payload.get("messages", [])), "rejected": []}
        if cmd == "deleteMessages":
            ids = payload.get("ids", []) if isinstance(payload, dict) else []
            return {"ok": True, "removed": len(ids), "not_found": []}
        if cmd == "listMessages":
            return {"ok": True, "messages": list(self.messages)}
        if cmd == "listAccounts":
            return {"ok": True, "accounts": []}
        return {"ok": True, "cmd": cmd, "payload": payload}


@pytest.fixture(autouse=True)
def reset_service():
    original = api.service
    original_token = getattr(api.app.state, "api_token", None)
    api.service = None
    api.app.state.api_token = None
    try:
        yield
    finally:
        api.service = original
        api.app.state.api_token = original_token


@pytest.fixture
def client_and_service():
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})
    return client, svc


def test_returns_500_when_service_missing():
    create_app(DummyService(), api_token=API_TOKEN)
    api.service = None
    client = TestClient(api.app)
    response = client.post("/commands/run-now", headers={API_TOKEN_HEADER_NAME: API_TOKEN})
    assert response.status_code == 500
    assert response.json()["detail"] == "Service not initialized"


def test_rejects_missing_token():
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    response = client.get("/status")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API token"


def test_basic_endpoints_dispatch_to_service(client_and_service):
    client, svc = client_and_service

    assert client.get("/status").json() == {"ok": True}

    assert client.post("/commands/run-now").json()["ok"] is True
    assert client.post("/commands/suspend").json()["ok"] is True
    assert client.post("/commands/activate").json()["ok"] is True

    bulk_payload = {
        "messages": [
            {
                "id": "msg-bulk",
                "from": "sender@example.com",
                "to": "dest@example.com, other@example.com",
                "bcc": "hidden@example.com",
                "subject": "Bulk",
                "body": "Bulk body",
            }
        ]
    }
    bulk_resp = client.post("/commands/add-messages", json=bulk_payload)
    assert bulk_resp.status_code == 200
    bulk_response_json = bulk_resp.json()
    assert isinstance(bulk_response_json, dict)
    assert bulk_response_json["queued"] == 1
    assert bulk_response_json["rejected"] == []

    delete_payload = {"ids": ["msg-bulk"]}
    delete_resp = client.post("/commands/delete-messages", json=delete_payload)
    assert delete_resp.status_code == 200
    assert delete_resp.json()["removed"] == 1

    account = {"id": "acc", "host": "smtp.local", "port": 25}
    assert client.post("/account", json=account).json()["ok"] is True
    # tenant_id is now required for /accounts and /messages
    assert client.get("/accounts?tenant_id=test-tenant").json()["ok"] is True
    assert client.delete("/account/acc").json()["ok"] is True
    assert client.get("/messages?tenant_id=test-tenant").json()["ok"] is True

    expected_calls = [
        ("run now", {}),
        ("suspend", {}),
        ("activate", {}),
        (
            "addMessages",
            {
                "messages": [
                    {
                        "id": "msg-bulk",
                        "from": "sender@example.com",
                        "to": "dest@example.com, other@example.com",
                        "bcc": "hidden@example.com",
                        "subject": "Bulk",
                        "body": "Bulk body",
                        "content_type": "plain",
                    }
                ]
            },
        ),
        ("deleteMessages", {"ids": ["msg-bulk"]}),
        ("addAccount", {"id": "acc", "tenant_id": None, "host": "smtp.local", "port": 25, "user": None, "password": None, "ttl": 300, "limit_per_minute": None, "limit_per_hour": None, "limit_per_day": None, "limit_behavior": "defer", "use_tls": None, "batch_size": None}),
        ("listAccounts", {"tenant_id": "test-tenant"}),
        ("deleteAccount", {"id": "acc"}),
        ("listMessages", {"tenant_id": "test-tenant", "active_only": False}),
    ]
    assert svc.calls == expected_calls


def test_metrics_endpoint_uses_service_metrics():
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.text == "metrics-data"


def test_health_endpoint_no_auth_required():
    """Test that /health endpoint works without authentication."""
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    # Do not set API token header
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# ============================================================================
# Additional Error Handling Tests
# ============================================================================

def test_invalid_token_rejected():
    """Test that invalid API token is rejected."""
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: "wrong-token"})

    response = client.get("/status")
    assert response.status_code == 401
    assert "Invalid or missing API token" in response.json()["detail"]


def test_no_token_configured_allows_access():
    """Test that when no token is configured, all requests are allowed."""
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=None))  # No token configured
    # Don't send any token header

    response = client.get("/status")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_validation_error_on_invalid_message_payload(client_and_service):
    """Test validation error handling for invalid message payload."""
    client, svc = client_and_service

    # Missing required fields
    invalid_payload = {
        "messages": [
            {
                "id": "msg-1",
                # missing 'from', 'to', 'subject', 'body'
            }
        ]
    }

    response = client.post("/commands/add-messages", json=invalid_payload)
    assert response.status_code == 422
    assert "detail" in response.json()


def test_validation_error_on_invalid_account_payload(client_and_service):
    """Test validation error handling for invalid account payload."""
    client, svc = client_and_service

    # Missing required fields
    invalid_account = {
        "id": "acc",
        # missing 'host' and 'port'
    }

    response = client.post("/account", json=invalid_account)
    assert response.status_code == 422


def test_add_messages_returns_400_on_service_error():
    """Test that add-messages returns 400 when service reports error."""
    class ErrorService(DummyService):
        async def handle_command(self, cmd, payload):
            if cmd == "addMessages":
                return {"ok": False, "error": "Validation failed", "rejected": ["msg-1"]}
            return await super().handle_command(cmd, payload)

    svc = ErrorService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    payload = {
        "messages": [
            {
                "id": "msg-1",
                "from": "sender@example.com",
                "to": "dest@example.com",
                "subject": "Test",
                "body": "Body",
            }
        ]
    }

    response = client.post("/commands/add-messages", json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert detail["error"] == "Validation failed"
    assert detail["rejected"] == ["msg-1"]


def test_service_not_initialized_all_commands():
    """Test that all command endpoints return 500 when service is not initialized."""
    create_app(DummyService(), api_token=API_TOKEN)
    api.service = None
    client = TestClient(api.app)
    headers = {API_TOKEN_HEADER_NAME: API_TOKEN}

    # Test all command endpoints
    endpoints_to_test = [
        ("POST", "/commands/run-now", None),
        ("POST", "/commands/suspend", None),
        ("POST", "/commands/activate", None),
        ("POST", "/commands/add-messages", {"messages": []}),
        ("POST", "/commands/delete-messages", {"ids": []}),
        ("POST", "/commands/cleanup-messages", {}),
        ("POST", "/account", {"id": "a", "host": "h", "port": 25}),
        ("GET", "/accounts?tenant_id=test-tenant", None),
        ("DELETE", "/account/test", None),
        ("GET", "/messages?tenant_id=test-tenant", None),
        ("GET", "/metrics", None),
    ]

    for method, path, body in endpoints_to_test:
        if method == "GET":
            response = client.get(path, headers=headers)
        elif method == "POST":
            response = client.post(path, json=body, headers=headers)
        elif method == "DELETE":
            response = client.delete(path, headers=headers)

        assert response.status_code == 500, f"Expected 500 for {method} {path}, got {response.status_code}"
        assert response.json()["detail"] == "Service not initialized"


def test_message_with_attachments(client_and_service):
    """Test adding message with attachments."""
    client, svc = client_and_service

    payload = {
        "messages": [
            {
                "id": "msg-attach",
                "from": "sender@example.com",
                "to": "dest@example.com",
                "subject": "With Attachment",
                "body": "Body with attachment",
                "attachments": [
                    {
                        "filename": "doc.pdf",
                        "storage_path": "/path/to/doc.pdf"
                    },
                    {
                        "filename": "image.png",
                        "storage_path": "base64:iVBORw...",
                        "mime_type": "image/png"
                    }
                ]
            }
        ]
    }

    response = client.post("/commands/add-messages", json=payload)
    assert response.status_code == 200
    assert response.json()["queued"] == 1

    # Verify attachments were passed to service
    cmd, data = svc.calls[-1]
    assert cmd == "addMessages"
    msg = data["messages"][0]
    assert len(msg["attachments"]) == 2
    assert msg["attachments"][0]["filename"] == "doc.pdf"
    assert msg["attachments"][1]["mime_type"] == "image/png"


def test_message_with_priority(client_and_service):
    """Test adding message with priority."""
    client, svc = client_and_service

    payload = {
        "messages": [
            {
                "id": "msg-priority",
                "from": "sender@example.com",
                "to": "dest@example.com",
                "subject": "High Priority",
                "body": "Urgent message",
                "priority": 0  # immediate
            }
        ],
        "default_priority": 2  # medium
    }

    response = client.post("/commands/add-messages", json=payload)
    assert response.status_code == 200

    # Verify priority was passed
    cmd, data = svc.calls[-1]
    assert data["default_priority"] == 2
    assert data["messages"][0]["priority"] == 0


def test_message_with_html_content(client_and_service):
    """Test adding message with HTML content type."""
    client, svc = client_and_service

    payload = {
        "messages": [
            {
                "id": "msg-html",
                "from": "sender@example.com",
                "to": "dest@example.com",
                "subject": "HTML Email",
                "body": "<html><body><h1>Hello</h1></body></html>",
                "content_type": "html"
            }
        ]
    }

    response = client.post("/commands/add-messages", json=payload)
    assert response.status_code == 200

    cmd, data = svc.calls[-1]
    assert data["messages"][0]["content_type"] == "html"


def test_message_with_cc_and_bcc(client_and_service):
    """Test adding message with CC and BCC recipients."""
    client, svc = client_and_service

    payload = {
        "messages": [
            {
                "id": "msg-cc-bcc",
                "from": "sender@example.com",
                "to": ["primary@example.com"],
                "cc": ["copy1@example.com", "copy2@example.com"],
                "bcc": ["hidden@example.com"],
                "subject": "With CC/BCC",
                "body": "Body"
            }
        ]
    }

    response = client.post("/commands/add-messages", json=payload)
    assert response.status_code == 200

    cmd, data = svc.calls[-1]
    msg = data["messages"][0]
    assert msg["to"] == ["primary@example.com"]
    assert msg["cc"] == ["copy1@example.com", "copy2@example.com"]
    assert msg["bcc"] == ["hidden@example.com"]


def test_cleanup_messages_with_custom_retention():
    """Test cleanup messages with custom retention period."""
    class CleanupService(DummyService):
        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "cleanupMessages":
                return {"ok": True, "removed": 5}
            return {"ok": True}

    svc = CleanupService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    # With custom older_than_seconds
    response = client.post("/commands/cleanup-messages", json={"older_than_seconds": 3600})
    assert response.status_code == 200

    cmd, data = svc.calls[-1]
    assert cmd == "cleanupMessages"
    assert data["older_than_seconds"] == 3600


def test_cleanup_messages_default_retention():
    """Test cleanup messages with default retention period."""
    class CleanupService(DummyService):
        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "cleanupMessages":
                return {"ok": True, "removed": 0}
            return {"ok": True}

    svc = CleanupService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    # With default (no body or empty body)
    response = client.post("/commands/cleanup-messages", json={})
    assert response.status_code == 200

    cmd, data = svc.calls[-1]
    assert cmd == "cleanupMessages"
    assert data.get("older_than_seconds") is None


def test_empty_message_list_accepted(client_and_service):
    """Test that empty message list is accepted."""
    client, svc = client_and_service

    payload = {"messages": []}

    response = client.post("/commands/add-messages", json=payload)
    assert response.status_code == 200
    assert response.json()["queued"] == 0


def test_delete_messages_empty_list(client_and_service):
    """Test deleting with empty ID list."""
    client, svc = client_and_service

    response = client.post("/commands/delete-messages", json={"ids": []})
    assert response.status_code == 200
    assert response.json()["removed"] == 0


# ============================================================================
# Tenant API Tests
# ============================================================================

def test_create_tenant_without_token_rejected():
    """Test that creating a tenant without API token is rejected when token is configured."""
    class TenantService(DummyService):
        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "addTenant":
                return {"ok": True}
            return await super().handle_command(cmd, payload)

    svc = TenantService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    # Do NOT set API token header

    tenant_payload = {
        "id": "test-tenant",
        "name": "Test Tenant",
        "active": True
    }

    response = client.post("/tenant", json=tenant_payload)
    assert response.status_code == 401
    assert "Invalid or missing API token" in response.json()["detail"]
    # Verify the service was never called
    assert len(svc.calls) == 0


def test_create_tenant_with_wrong_token_rejected():
    """Test that creating a tenant with wrong API token is rejected."""
    class TenantService(DummyService):
        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "addTenant":
                return {"ok": True}
            return await super().handle_command(cmd, payload)

    svc = TenantService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: "wrong-token"})

    tenant_payload = {
        "id": "test-tenant",
        "name": "Test Tenant",
        "active": True
    }

    response = client.post("/tenant", json=tenant_payload)
    assert response.status_code == 401
    assert "Invalid or missing API token" in response.json()["detail"]
    # Verify the service was never called
    assert len(svc.calls) == 0


def test_create_tenant_with_valid_token_succeeds():
    """Test that creating a tenant with valid API token succeeds."""
    class TenantService(DummyService):
        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "addTenant":
                return {"ok": True}
            return await super().handle_command(cmd, payload)

    svc = TenantService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    tenant_payload = {
        "id": "test-tenant",
        "name": "Test Tenant",
        "active": True
    }

    response = client.post("/tenant", json=tenant_payload)
    assert response.status_code == 200
    assert response.json()["ok"] is True
    # Verify the service was called
    assert len(svc.calls) == 1
    assert svc.calls[0][0] == "addTenant"


# ============================================================================
# Cross-Tenant Isolation Security Tests (Issue #28)
# ============================================================================

def test_accounts_endpoint_requires_tenant_id():
    """Test that /accounts endpoint requires tenant_id parameter."""
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    # Request without tenant_id should fail
    response = client.get("/accounts")
    assert response.status_code == 422  # Validation error - missing required param


def test_messages_endpoint_requires_tenant_id():
    """Test that /messages endpoint requires tenant_id parameter."""
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    # Request without tenant_id should fail
    response = client.get("/messages")
    assert response.status_code == 422  # Validation error - missing required param


def test_accounts_filtered_by_tenant_id():
    """Test that /accounts returns only accounts for the specified tenant."""
    class MultiTenantService(DummyService):
        def __init__(self):
            super().__init__()
            self.accounts_by_tenant = {
                "tenant-a": [
                    {"id": "acc-a1", "tenant_id": "tenant-a", "host": "smtp-a.com", "port": 587, "ttl": 300},
                    {"id": "acc-a2", "tenant_id": "tenant-a", "host": "smtp-a2.com", "port": 587, "ttl": 300},
                ],
                "tenant-b": [
                    {"id": "acc-b1", "tenant_id": "tenant-b", "host": "smtp-b.com", "port": 587, "ttl": 300},
                ],
            }

        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "listAccounts":
                tenant_id = payload.get("tenant_id")
                accounts = self.accounts_by_tenant.get(tenant_id, [])
                return {"ok": True, "accounts": accounts}
            return await super().handle_command(cmd, payload)

    svc = MultiTenantService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    # Request accounts for tenant-a
    response_a = client.get("/accounts?tenant_id=tenant-a")
    assert response_a.status_code == 200
    data_a = response_a.json()
    assert data_a["ok"] is True
    assert len(data_a["accounts"]) == 2
    assert all(acc["tenant_id"] == "tenant-a" for acc in data_a["accounts"])

    # Request accounts for tenant-b
    response_b = client.get("/accounts?tenant_id=tenant-b")
    assert response_b.status_code == 200
    data_b = response_b.json()
    assert data_b["ok"] is True
    assert len(data_b["accounts"]) == 1
    assert data_b["accounts"][0]["tenant_id"] == "tenant-b"

    # Verify tenant_id was passed correctly to service
    assert svc.calls[0] == ("listAccounts", {"tenant_id": "tenant-a"})
    assert svc.calls[1] == ("listAccounts", {"tenant_id": "tenant-b"})


def test_messages_filtered_by_tenant_id():
    """Test that /messages returns only messages for the specified tenant."""
    class MultiTenantService(DummyService):
        def __init__(self):
            super().__init__()
            self.messages_by_tenant = {
                "tenant-a": [
                    {"id": "msg-a1", "account_id": "acc-a1", "priority": 2, "message": {}},
                    {"id": "msg-a2", "account_id": "acc-a1", "priority": 2, "message": {}},
                ],
                "tenant-b": [
                    {"id": "msg-b1", "account_id": "acc-b1", "priority": 2, "message": {}},
                ],
            }

        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "listMessages":
                tenant_id = payload.get("tenant_id")
                messages = self.messages_by_tenant.get(tenant_id, [])
                return {"ok": True, "messages": messages}
            return await super().handle_command(cmd, payload)

    svc = MultiTenantService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    # Request messages for tenant-a
    response_a = client.get("/messages?tenant_id=tenant-a")
    assert response_a.status_code == 200
    data_a = response_a.json()
    assert data_a["ok"] is True
    assert len(data_a["messages"]) == 2

    # Request messages for tenant-b
    response_b = client.get("/messages?tenant_id=tenant-b")
    assert response_b.status_code == 200
    data_b = response_b.json()
    assert data_b["ok"] is True
    assert len(data_b["messages"]) == 1

    # Verify tenant_id was passed correctly to service
    assert svc.calls[0] == ("listMessages", {"tenant_id": "tenant-a", "active_only": False})
    assert svc.calls[1] == ("listMessages", {"tenant_id": "tenant-b", "active_only": False})


def test_cross_tenant_isolation_accounts():
    """Test that tenant-a cannot access tenant-b's accounts."""
    class IsolationService(DummyService):
        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "listAccounts":
                tenant_id = payload.get("tenant_id")
                # Simulate proper isolation - only return accounts for requested tenant
                if tenant_id == "tenant-a":
                    return {"ok": True, "accounts": [{"id": "acc-a", "tenant_id": "tenant-a", "host": "smtp-a.com", "port": 587, "ttl": 300}]}
                elif tenant_id == "tenant-b":
                    return {"ok": True, "accounts": [{"id": "acc-b", "tenant_id": "tenant-b", "host": "smtp-b.com", "port": 587, "ttl": 300}]}
                return {"ok": True, "accounts": []}
            return await super().handle_command(cmd, payload)

    svc = IsolationService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    # Tenant-a requests their accounts
    response = client.get("/accounts?tenant_id=tenant-a")
    assert response.status_code == 200
    accounts = response.json()["accounts"]

    # Verify no tenant-b accounts are returned
    tenant_b_accounts = [a for a in accounts if a.get("tenant_id") == "tenant-b"]
    assert len(tenant_b_accounts) == 0, "Cross-tenant data leak: tenant-b accounts visible to tenant-a"


def test_cross_tenant_isolation_messages():
    """Test that tenant-a cannot access tenant-b's messages."""
    class IsolationService(DummyService):
        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "listMessages":
                tenant_id = payload.get("tenant_id")
                # Simulate proper isolation - only return messages for requested tenant
                if tenant_id == "tenant-a":
                    return {"ok": True, "messages": [{"id": "msg-a", "account_id": "acc-a", "priority": 2, "message": {}}]}
                elif tenant_id == "tenant-b":
                    return {"ok": True, "messages": [{"id": "msg-b", "account_id": "acc-b", "priority": 2, "message": {}}]}
                return {"ok": True, "messages": []}
            return await super().handle_command(cmd, payload)

    svc = IsolationService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    # Tenant-a requests their messages
    response = client.get("/messages?tenant_id=tenant-a")
    assert response.status_code == 200
    messages = response.json()["messages"]

    # Should only have tenant-a messages
    assert len(messages) == 1
    assert messages[0]["id"] == "msg-a"


def test_nonexistent_tenant_returns_empty():
    """Test that requesting data for non-existent tenant returns empty results."""
    class EmptyService(DummyService):
        async def handle_command(self, cmd, payload):
            self.calls.append((cmd, payload))
            if cmd == "listAccounts":
                return {"ok": True, "accounts": []}
            if cmd == "listMessages":
                return {"ok": True, "messages": []}
            return await super().handle_command(cmd, payload)

    svc = EmptyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    # Request for non-existent tenant
    response_accounts = client.get("/accounts?tenant_id=nonexistent")
    assert response_accounts.status_code == 200
    assert response_accounts.json()["accounts"] == []

    response_messages = client.get("/messages?tenant_id=nonexistent")
    assert response_messages.status_code == 200
    assert response_messages.json()["messages"] == []
