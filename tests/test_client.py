"""Tests for the MailProxyClient library."""

import json
from unittest.mock import MagicMock, patch

from async_mail_service.client import (
    Account,
    AccountsAPI,
    MailProxyClient,
    Message,
    MessagesAPI,
    Tenant,
    TenantsAPI,
    _connections,
    _load_connections_from_file,
    connect,
    register_connection,
)

# --- Message dataclass tests ---

class TestMessageDataclass:
    """Tests for Message dataclass."""

    def test_message_from_dict_pending(self):
        """Test creating Message from API response - pending status."""
        data = {
            "id": "msg-123",
            "account_id": "smtp-1",
            "priority": 2,
            "created_at": "2024-01-01T00:00:00",
            "message": {
                "subject": "Test Subject",
                "from": "sender@test.com",
                "to": ["recipient@test.com"]
            }
        }
        msg = Message.from_dict(data)

        assert msg.id == "msg-123"
        assert msg.account_id == "smtp-1"
        assert msg.subject == "Test Subject"
        assert msg.from_addr == "sender@test.com"
        assert msg.to == ["recipient@test.com"]
        assert msg.status == "pending"
        assert msg.priority == 2

    def test_message_from_dict_sent(self):
        """Test creating Message from API response - sent status."""
        data = {
            "id": "msg-123",
            "sent_ts": 1704067200,
            "message": {"subject": "Sent", "from": "a@b.com", "to": []}
        }
        msg = Message.from_dict(data)
        assert msg.status == "sent"
        assert msg.sent_ts == 1704067200

    def test_message_from_dict_error(self):
        """Test creating Message from API response - error status."""
        data = {
            "id": "msg-123",
            "error_ts": 1704067200,
            "error": "Connection refused",
            "message": {"subject": "Failed", "from": "a@b.com", "to": []}
        }
        msg = Message.from_dict(data)
        assert msg.status == "error"
        assert msg.error == "Connection refused"

    def test_message_from_dict_deferred(self):
        """Test creating Message from API response - deferred status."""
        data = {
            "id": "msg-123",
            "deferred_ts": 1704070800,
            "message": {"subject": "Deferred", "from": "a@b.com", "to": []}
        }
        msg = Message.from_dict(data)
        assert msg.status == "deferred"

    def test_message_repr(self):
        """Test Message repr."""
        msg = Message(id="msg-1", subject="Hello World", status="pending")
        repr_str = repr(msg)
        assert "msg-1" in repr_str
        assert "Hello World" in repr_str
        assert "pending" in repr_str


# --- Account dataclass tests ---

class TestAccountDataclass:
    """Tests for Account dataclass."""

    def test_account_from_dict(self):
        """Test creating Account from API response."""
        data = {
            "id": "smtp-1",
            "tenant_id": "tenant-1",
            "host": "smtp.example.com",
            "port": 587,
            "user": "user@example.com",
            "use_tls": True
        }
        account = Account.from_dict(data)

        assert account.id == "smtp-1"
        assert account.tenant_id == "tenant-1"
        assert account.host == "smtp.example.com"
        assert account.port == 587
        assert account.use_tls is True

    def test_account_from_dict_defaults(self):
        """Test Account defaults when fields missing."""
        data = {"id": "smtp-1"}
        account = Account.from_dict(data)

        assert account.id == "smtp-1"
        assert account.tenant_id is None
        assert account.host == ""
        assert account.port == 587
        assert account.use_tls is True

    def test_account_repr(self):
        """Test Account repr."""
        account = Account(id="smtp-1", host="smtp.example.com", port=587)
        repr_str = repr(account)
        assert "smtp-1" in repr_str
        assert "smtp.example.com:587" in repr_str


# --- Tenant dataclass tests ---

class TestTenantDataclass:
    """Tests for Tenant dataclass."""

    def test_tenant_from_dict(self):
        """Test creating Tenant from API response."""
        data = {
            "id": "tenant-1",
            "name": "My Tenant",
            "active": True,
            "client_base_url": "https://example.com",
            "client_sync_path": "/webhook",
            "client_attachment_path": "/files"
        }
        tenant = Tenant.from_dict(data)

        assert tenant.id == "tenant-1"
        assert tenant.name == "My Tenant"
        assert tenant.active is True
        assert tenant.client_base_url == "https://example.com"
        assert tenant.client_sync_path == "/webhook"
        assert tenant.client_attachment_path == "/files"

    def test_tenant_from_dict_defaults(self):
        """Test Tenant defaults when fields missing."""
        data = {"id": "tenant-1"}
        tenant = Tenant.from_dict(data)

        assert tenant.id == "tenant-1"
        assert tenant.name is None
        assert tenant.active is True

    def test_tenant_repr_active(self):
        """Test Tenant repr for active tenant."""
        tenant = Tenant(id="tenant-1", name="Test", active=True)
        repr_str = repr(tenant)
        assert "tenant-1" in repr_str
        assert "active" in repr_str

    def test_tenant_repr_inactive(self):
        """Test Tenant repr for inactive tenant."""
        tenant = Tenant(id="tenant-1", name="Test", active=False)
        repr_str = repr(tenant)
        assert "inactive" in repr_str


# --- MailProxyClient tests ---

class TestMailProxyClient:
    """Tests for MailProxyClient."""

    def test_client_initialization(self):
        """Test client initialization."""
        client = MailProxyClient(
            url="http://localhost:8000",
            token="secret-token",
            name="test-proxy"
        )

        assert client.url == "http://localhost:8000"
        assert client.token == "secret-token"
        assert client.name == "test-proxy"
        assert isinstance(client.messages, MessagesAPI)
        assert isinstance(client.accounts, AccountsAPI)
        assert isinstance(client.tenants, TenantsAPI)

    def test_client_url_trailing_slash_stripped(self):
        """Test that trailing slash is stripped from URL."""
        client = MailProxyClient(url="http://localhost:8000/")
        assert client.url == "http://localhost:8000"

    def test_client_default_name_is_url(self):
        """Test that default name is URL."""
        client = MailProxyClient(url="http://localhost:8000")
        assert client.name == "http://localhost:8000"

    def test_client_headers_with_token(self):
        """Test headers include token when provided."""
        client = MailProxyClient(token="my-token")
        headers = client._headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["X-API-Token"] == "my-token"

    def test_client_headers_without_token(self):
        """Test headers without token."""
        client = MailProxyClient()
        headers = client._headers()

        assert headers["Content-Type"] == "application/json"
        assert "X-API-Token" not in headers

    def test_client_get_request(self):
        """Test GET request."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"status": "ok"}
            mock_get.return_value = mock_response

            client = MailProxyClient(url="http://localhost:8000", token="tok")
            result = client._get("/status")

            mock_get.assert_called_once()
            call_args = mock_get.call_args
            assert call_args[0][0] == "http://localhost:8000/status"
            assert result == {"status": "ok"}

    def test_client_post_request(self):
        """Test POST request."""
        import requests
        with patch.object(requests, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_post.return_value = mock_response

            client = MailProxyClient(url="http://localhost:8000", token="tok")
            result = client._post("/commands/run-now", {"data": "value"})

            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0] == "http://localhost:8000/commands/run-now"
            assert result == {"ok": True}

    def test_client_delete_request(self):
        """Test DELETE request."""
        import requests
        with patch.object(requests, 'delete') as mock_delete:
            mock_response = MagicMock()
            mock_response.json.return_value = {"deleted": True}
            mock_delete.return_value = mock_response

            client = MailProxyClient(url="http://localhost:8000", token="tok")
            result = client._delete("/account/smtp-1")

            mock_delete.assert_called_once()
            assert result == {"deleted": True}

    def test_client_status(self):
        """Test status() method."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True, "active": True}
            mock_get.return_value = mock_response

            client = MailProxyClient()
            result = client.status()

            assert result == {"ok": True, "active": True}

    def test_client_health_true(self):
        """Test health() returns True when server is ok."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_get.return_value = mock_response

            client = MailProxyClient()
            assert client.health() is True

    def test_client_health_false_on_error(self):
        """Test health() returns False on exception."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_get.side_effect = Exception("Connection refused")

            client = MailProxyClient()
            assert client.health() is False

    def test_client_run_now(self):
        """Test run_now() method."""
        import requests
        with patch.object(requests, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_post.return_value = mock_response

            client = MailProxyClient()
            result = client.run_now()

            call_args = mock_post.call_args
            assert "/commands/run-now" in call_args[0][0]

    def test_client_suspend(self):
        """Test suspend() method."""
        import requests
        with patch.object(requests, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_post.return_value = mock_response

            client = MailProxyClient()
            client.suspend()

            call_args = mock_post.call_args
            assert "/commands/suspend" in call_args[0][0]

    def test_client_activate(self):
        """Test activate() method."""
        import requests
        with patch.object(requests, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_post.return_value = mock_response

            client = MailProxyClient()
            client.activate()

            call_args = mock_post.call_args
            assert "/commands/activate" in call_args[0][0]

    def test_client_stats(self):
        """Test stats() method."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            # Mock messages response
            mock_response.json.side_effect = [
                {"messages": [
                    {"id": "1", "message": {}, "sent_ts": 123},
                    {"id": "2", "message": {}},
                    {"id": "3", "message": {}, "error_ts": 456}
                ]},
                {"accounts": [{"id": "a1"}, {"id": "a2"}]}
            ]
            mock_get.return_value = mock_response

            client = MailProxyClient()
            stats = client.stats()

            assert stats["total"] == 3
            assert stats["sent"] == 1
            assert stats["pending"] == 1
            assert stats["errors"] == 1
            assert stats["accounts"] == 2


# --- MessagesAPI tests ---

class TestMessagesAPI:
    """Tests for MessagesAPI."""

    def test_messages_list(self):
        """Test listing messages."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "messages": [
                    {"id": "msg-1", "message": {"subject": "Test", "from": "a@b.com", "to": []}},
                    {"id": "msg-2", "message": {"subject": "Test 2", "from": "a@b.com", "to": []}}
                ]
            }
            mock_get.return_value = mock_response

            client = MailProxyClient()
            messages = client.messages.list()

            assert len(messages) == 2
            assert all(isinstance(m, Message) for m in messages)

    def test_messages_pending(self):
        """Test filtering pending messages."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "messages": [
                    {"id": "msg-1", "message": {}},  # pending
                    {"id": "msg-2", "message": {}, "sent_ts": 123}  # sent
                ]
            }
            mock_get.return_value = mock_response

            client = MailProxyClient()
            pending = client.messages.pending()

            assert len(pending) == 1
            assert pending[0].id == "msg-1"

    def test_messages_sent(self):
        """Test filtering sent messages."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "messages": [
                    {"id": "msg-1", "message": {}},  # pending
                    {"id": "msg-2", "message": {}, "sent_ts": 123}  # sent
                ]
            }
            mock_get.return_value = mock_response

            client = MailProxyClient()
            sent = client.messages.sent()

            assert len(sent) == 1
            assert sent[0].id == "msg-2"

    def test_messages_errors(self):
        """Test filtering error messages."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "messages": [
                    {"id": "msg-1", "message": {}},  # pending
                    {"id": "msg-2", "message": {}, "error_ts": 123, "error": "Failed"}
                ]
            }
            mock_get.return_value = mock_response

            client = MailProxyClient()
            errors = client.messages.errors()

            assert len(errors) == 1
            assert errors[0].id == "msg-2"

    def test_messages_get_found(self):
        """Test getting a specific message."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "messages": [
                    {"id": "msg-1", "message": {}},
                    {"id": "msg-2", "message": {}}
                ]
            }
            mock_get.return_value = mock_response

            client = MailProxyClient()
            msg = client.messages.get("msg-2")

            assert msg is not None
            assert msg.id == "msg-2"

    def test_messages_get_not_found(self):
        """Test getting a non-existent message."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"messages": [{"id": "msg-1", "message": {}}]}
            mock_get.return_value = mock_response

            client = MailProxyClient()
            msg = client.messages.get("msg-999")

            assert msg is None

    def test_messages_add(self):
        """Test adding messages."""
        import requests
        with patch.object(requests, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True, "added": 2}
            mock_post.return_value = mock_response

            client = MailProxyClient()
            result = client.messages.add([
                {"id": "msg-1", "subject": "Test"},
                {"id": "msg-2", "subject": "Test 2"}
            ])

            assert result["added"] == 2

    def test_messages_delete_single(self):
        """Test deleting a single message (string ID)."""
        import requests
        with patch.object(requests, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True, "deleted": 1}
            mock_post.return_value = mock_response

            client = MailProxyClient()
            result = client.messages.delete("msg-1")

            call_args = mock_post.call_args
            assert call_args[1]["json"] == {"ids": ["msg-1"]}

    def test_messages_delete_multiple(self):
        """Test deleting multiple messages."""
        import requests
        with patch.object(requests, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True, "deleted": 2}
            mock_post.return_value = mock_response

            client = MailProxyClient()
            result = client.messages.delete(["msg-1", "msg-2"])

            call_args = mock_post.call_args
            assert call_args[1]["json"] == {"ids": ["msg-1", "msg-2"]}


# --- AccountsAPI tests ---

class TestAccountsAPI:
    """Tests for AccountsAPI."""

    def test_accounts_list(self):
        """Test listing accounts."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "accounts": [
                    {"id": "smtp-1", "host": "smtp1.example.com", "port": 587},
                    {"id": "smtp-2", "host": "smtp2.example.com", "port": 465}
                ]
            }
            mock_get.return_value = mock_response

            client = MailProxyClient()
            accounts = client.accounts.list()

            assert len(accounts) == 2
            assert all(isinstance(a, Account) for a in accounts)

    def test_accounts_get_found(self):
        """Test getting a specific account."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "accounts": [{"id": "smtp-1"}, {"id": "smtp-2"}]
            }
            mock_get.return_value = mock_response

            client = MailProxyClient()
            account = client.accounts.get("smtp-2")

            assert account.id == "smtp-2"

    def test_accounts_add(self):
        """Test adding an account."""
        import requests
        with patch.object(requests, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_post.return_value = mock_response

            client = MailProxyClient()
            result = client.accounts.add({
                "id": "smtp-1",
                "host": "smtp.example.com",
                "port": 587
            })

            assert result["ok"] is True

    def test_accounts_delete(self):
        """Test deleting an account."""
        import requests
        with patch.object(requests, 'delete') as mock_delete:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_delete.return_value = mock_response

            client = MailProxyClient()
            result = client.accounts.delete("smtp-1")

            call_args = mock_delete.call_args
            assert "/account/smtp-1" in call_args[0][0]


# --- TenantsAPI tests ---

class TestTenantsAPI:
    """Tests for TenantsAPI."""

    def test_tenants_list(self):
        """Test listing tenants."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "tenants": [
                    {"id": "tenant-1", "name": "Tenant One"},
                    {"id": "tenant-2", "name": "Tenant Two"}
                ]
            }
            mock_get.return_value = mock_response

            client = MailProxyClient()
            tenants = client.tenants.list()

            assert len(tenants) == 2
            assert all(isinstance(t, Tenant) for t in tenants)

    def test_tenants_get(self):
        """Test getting a specific tenant."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = {"id": "tenant-1", "name": "Test"}
            mock_get.return_value = mock_response

            client = MailProxyClient()
            tenant = client.tenants.get("tenant-1")

            assert tenant.id == "tenant-1"

    def test_tenants_get_not_found(self):
        """Test getting non-existent tenant."""
        import requests
        with patch.object(requests, 'get') as mock_get:
            mock_response = MagicMock()
            mock_response.json.return_value = None
            mock_get.return_value = mock_response

            client = MailProxyClient()
            tenant = client.tenants.get("nonexistent")

            assert tenant is None

    def test_tenants_add(self):
        """Test adding a tenant."""
        import requests
        with patch.object(requests, 'post') as mock_post:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_post.return_value = mock_response

            client = MailProxyClient()
            result = client.tenants.add({"id": "tenant-1", "name": "New Tenant"})

            assert result["ok"] is True

    def test_tenants_delete(self):
        """Test deleting a tenant."""
        import requests
        with patch.object(requests, 'delete') as mock_delete:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_delete.return_value = mock_response

            client = MailProxyClient()
            result = client.tenants.delete("tenant-1")

            call_args = mock_delete.call_args
            assert "/tenant/tenant-1" in call_args[0][0]


# --- Connection registry tests ---

class TestConnectionRegistry:
    """Tests for connection registration and lookup."""

    def setup_method(self):
        """Clear connections before each test."""
        _connections.clear()

    def test_register_connection(self):
        """Test registering a connection."""
        register_connection("prod", "https://mail.example.com", "secret-token")

        assert "prod" in _connections
        assert _connections["prod"]["url"] == "https://mail.example.com"
        assert _connections["prod"]["token"] == "secret-token"

    def test_connect_by_registered_name(self):
        """Test connecting by registered name."""
        register_connection("prod", "https://mail.example.com", "secret")

        client = connect("prod")

        assert client.url == "https://mail.example.com"
        assert client.token == "secret"
        assert client.name == "prod"

    def test_connect_by_url(self):
        """Test connecting directly by URL."""
        client = connect("http://localhost:9000", token="tok123")

        assert client.url == "http://localhost:9000"
        assert client.token == "tok123"

    def test_connect_override_token(self):
        """Test that token can be overridden when connecting."""
        register_connection("prod", "https://mail.example.com", "original-token")

        client = connect("prod", token="override-token")

        assert client.token == "override-token"

    def test_connect_custom_name(self):
        """Test connecting with custom display name."""
        client = connect("http://localhost:8000", name="my-proxy")

        assert client.name == "my-proxy"

    def test_load_connections_from_file_not_exists(self, tmp_path):
        """Test loading connections when file doesn't exist."""
        with patch("pathlib.Path.home", return_value=tmp_path):
            connections = _load_connections_from_file()

        assert connections == {}

    def test_load_connections_from_file_valid(self, tmp_path):
        """Test loading connections from valid file."""
        connections_file = tmp_path / ".mail-proxy" / "connections.json"
        connections_file.parent.mkdir(parents=True)
        connections_file.write_text(json.dumps({
            "prod": {"url": "https://prod.example.com", "token": "prod-token"},
            "staging": {"url": "https://staging.example.com", "token": "staging-token"}
        }))

        with patch("pathlib.Path.home", return_value=tmp_path):
            connections = _load_connections_from_file()

        assert "prod" in connections
        assert connections["prod"]["url"] == "https://prod.example.com"

    def test_load_connections_from_file_invalid_json(self, tmp_path):
        """Test loading connections from invalid JSON file."""
        connections_file = tmp_path / ".mail-proxy" / "connections.json"
        connections_file.parent.mkdir(parents=True)
        connections_file.write_text("not valid json {{{")

        with patch("pathlib.Path.home", return_value=tmp_path):
            connections = _load_connections_from_file()

        assert connections == {}

    def test_connect_from_file_registry(self, tmp_path):
        """Test connecting using file-based registry."""
        connections_file = tmp_path / ".mail-proxy" / "connections.json"
        connections_file.parent.mkdir(parents=True)
        connections_file.write_text(json.dumps({
            "file-conn": {"url": "https://file.example.com", "token": "file-token"}
        }))

        with patch("pathlib.Path.home", return_value=tmp_path):
            client = connect("file-conn")

        assert client.url == "https://file.example.com"
        assert client.token == "file-token"
