# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for MailProxyClient and related functions."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from tools.http_client.client import (
    MailProxyClient,
    Message,
    Tenant,
    CommandLogEntry,
    connect,
    register_connection,
    _connections,
)


class TestMailProxyClientInit:
    """Tests for MailProxyClient initialization."""

    def test_init_basic(self):
        """Initialize client with basic parameters."""
        client = MailProxyClient(url="http://localhost:8000")

        assert client.url == "http://localhost:8000"
        assert client.name == "http://localhost:8000"
        assert client.token is None
        assert client.tenant_id is None

    def test_init_with_token(self):
        """Initialize client with API token."""
        client = MailProxyClient(url="http://localhost:8000", token="secret")

        assert client.token == "secret"

    def test_init_with_all_options(self):
        """Initialize client with all options."""
        client = MailProxyClient(
            url="http://localhost:8000",
            token="secret",
            name="test-server",
            tenant_id="t1",
        )

        assert client.url == "http://localhost:8000"
        assert client.token == "secret"
        assert client.name == "test-server"
        assert client.tenant_id == "t1"

    def test_repr(self):
        """Client has meaningful repr."""
        client = MailProxyClient(url="http://localhost:8000", name="prod")
        assert "prod" in repr(client)


class TestMailProxyClientHeaders:
    """Tests for client HTTP headers."""

    def test_headers_without_token(self):
        """Headers without token include Content-Type only."""
        client = MailProxyClient(url="http://localhost:8000")
        headers = client._headers()

        assert headers["Content-Type"] == "application/json"
        assert "X-API-Token" not in headers

    def test_headers_with_token(self):
        """Headers with token include X-API-Token."""
        client = MailProxyClient(url="http://localhost:8000", token="secret")
        headers = client._headers()

        assert headers["Content-Type"] == "application/json"
        assert headers["X-API-Token"] == "secret"


class TestMailProxyClientSubApis:
    """Tests for sub-API access."""

    def test_messages_api(self):
        """Client has messages sub-API."""
        client = MailProxyClient(url="http://localhost:8000")
        assert client.messages is not None

    def test_tenants_api(self):
        """Client has tenants sub-API."""
        client = MailProxyClient(url="http://localhost:8000")
        assert client.tenants is not None

    def test_accounts_api(self):
        """Client has accounts sub-API."""
        client = MailProxyClient(url="http://localhost:8000")
        assert client.accounts is not None

    def test_command_log_api(self):
        """Client has command_log sub-API."""
        client = MailProxyClient(url="http://localhost:8000")
        assert client.command_log is not None

    def test_instance_api(self):
        """Client has instance sub-API."""
        client = MailProxyClient(url="http://localhost:8000")
        assert client.instance is not None


class TestMailProxyClientConvenienceMethods:
    """Tests for convenience methods on client."""

    @pytest.fixture
    def client(self):
        return MailProxyClient(url="http://localhost:8000", token="token")

    async def test_status_delegates_to_instance(self, client):
        """status() delegates to instance.status()."""
        client.instance.status = AsyncMock(return_value={"active": True})

        result = await client.status()

        assert result["active"] is True
        client.instance.status.assert_called_once()

    async def test_health_returns_true_on_ok(self, client):
        """health() returns True when status is ok."""
        client.instance.health = AsyncMock(return_value={"status": "ok"})

        result = await client.health()

        assert result is True

    async def test_health_returns_false_on_exception(self, client):
        """health() returns False on exception."""
        client.instance.health = AsyncMock(side_effect=Exception("Connection failed"))

        result = await client.health()

        assert result is False

    async def test_run_now_delegates_to_instance(self, client):
        """run_now() delegates to instance.run_now()."""
        client.instance.run_now = AsyncMock(return_value={"ok": True})

        result = await client.run_now(tenant_id="t1")

        assert result["ok"] is True
        client.instance.run_now.assert_called_once_with("t1")

    async def test_suspend_delegates_to_instance(self, client):
        """suspend() delegates to instance.suspend()."""
        client.instance.suspend = AsyncMock(return_value={"ok": True})

        result = await client.suspend("t1", "batch-001")

        assert result["ok"] is True
        client.instance.suspend.assert_called_once_with("t1", "batch-001")

    async def test_activate_delegates_to_instance(self, client):
        """activate() delegates to instance.activate()."""
        client.instance.activate = AsyncMock(return_value={"ok": True})

        result = await client.activate("t1", "batch-001")

        assert result["ok"] is True
        client.instance.activate.assert_called_once_with("t1", "batch-001")


class TestMessageDataclass:
    """Tests for Message dataclass."""

    def test_from_dict_basic(self):
        """Message.from_dict parses basic fields."""
        data = {
            "id": "msg-1",
            "payload": {
                "subject": "Test",
                "from": "sender@example.com",
                "to": ["recipient@example.com"],
            },
            "status": "pending",
            "created_at": "2025-01-20T10:00:00Z",
        }

        msg = Message.from_dict(data)

        assert msg.id == "msg-1"
        assert msg.subject == "Test"
        assert msg.status == "pending"
        assert msg.from_addr == "sender@example.com"

    def test_from_dict_with_optional_fields(self):
        """Message.from_dict handles optional fields."""
        data = {
            "id": "msg-2",
            "payload": {"subject": "Test"},
            "status": "sent",
            "smtp_ts": 1234567900,
            "error": None,
        }

        msg = Message.from_dict(data)

        assert msg.smtp_ts == 1234567900
        assert msg.error is None

    def test_from_dict_with_message_key(self):
        """Message.from_dict uses 'message' key as fallback."""
        data = {
            "id": "msg-3",
            "message": {
                "subject": "Fallback",
                "from": "sender@test.com",
            },
            "status": "pending",
        }

        msg = Message.from_dict(data)

        assert msg.subject == "Fallback"

    def test_repr(self):
        """Message has meaningful repr."""
        data = {
            "id": "msg-repr",
            "payload": {"subject": "Test"},
            "status": "pending",
        }
        msg = Message.from_dict(data)
        assert "msg-repr" in repr(msg)


class TestTenantDataclass:
    """Tests for Tenant dataclass."""

    def test_from_dict_basic(self):
        """Tenant.from_dict parses basic fields."""
        data = {
            "id": "t1",
            "name": "Test Tenant",
            "active": True,
        }

        tenant = Tenant.from_dict(data)

        assert tenant.id == "t1"
        assert tenant.name == "Test Tenant"
        assert tenant.active is True

    def test_from_dict_with_suspended_batches_string(self):
        """Tenant.from_dict handles suspended_batches as string."""
        data = {
            "id": "t1",
            "name": "Test",
            "suspended_batches": "batch1,batch2,batch3",
        }

        tenant = Tenant.from_dict(data)

        assert tenant.suspended_batches == {"batch1", "batch2", "batch3"}

    def test_from_dict_with_suspended_batches_list(self):
        """Tenant.from_dict handles suspended_batches as list."""
        data = {
            "id": "t1",
            "name": "Test",
            "suspended_batches": ["batch1", "batch2"],
        }

        tenant = Tenant.from_dict(data)

        assert tenant.suspended_batches == {"batch1", "batch2"}

    def test_from_dict_empty_suspended_batches(self):
        """Tenant.from_dict handles empty suspended_batches."""
        data = {
            "id": "t1",
            "name": "Test",
            "suspended_batches": "",
        }

        tenant = Tenant.from_dict(data)

        assert tenant.suspended_batches == set()

    def test_repr(self):
        """Tenant has meaningful repr."""
        data = {
            "id": "t1",
            "name": "Test",
            "active": False,
        }
        tenant = Tenant.from_dict(data)
        assert "t1" in repr(tenant)
        assert "inactive" in repr(tenant)


class TestCommandLogEntryDataclass:
    """Tests for CommandLogEntry dataclass."""

    def test_from_dict(self):
        """CommandLogEntry.from_dict parses fields."""
        data = {
            "id": 1,
            "command_ts": 1234567890,
            "endpoint": "POST /messages/add",
            "tenant_id": "t1",
            "payload": {"key": "value"},
            "response_status": 200,
            "response_body": {"ok": True},
        }

        entry = CommandLogEntry.from_dict(data)

        assert entry.id == 1
        assert entry.command_ts == 1234567890
        assert entry.endpoint == "POST /messages/add"
        assert entry.tenant_id == "t1"
        assert entry.payload == {"key": "value"}
        assert entry.response_status == 200

    def test_repr(self):
        """CommandLogEntry has meaningful repr."""
        data = {
            "id": 42,
            "command_ts": 1234567890,
            "endpoint": "GET /status",
        }
        entry = CommandLogEntry.from_dict(data)
        assert "42" in repr(entry)
        assert "GET /status" in repr(entry)


class TestConnectionRegistry:
    """Tests for connection registry functions."""

    def setup_method(self):
        """Clear connections before each test."""
        _connections.clear()

    def test_register_connection(self):
        """register_connection adds to registry."""
        register_connection("test", "http://test.local", "token123")

        assert "test" in _connections
        assert _connections["test"]["url"] == "http://test.local"
        assert _connections["test"]["token"] == "token123"

    def test_connect_from_registry(self):
        """connect() uses registered connection."""
        register_connection("prod", "http://prod.example.com", "prod-token")

        client = connect("prod")

        assert client.url == "http://prod.example.com"
        assert client.token == "prod-token"
        assert client.name == "prod"

    def test_connect_override_token(self):
        """connect() can override token from registry."""
        register_connection("prod", "http://prod.example.com", "default-token")

        client = connect("prod", token="override-token")

        assert client.token == "override-token"

    def test_connect_as_url(self):
        """connect() treats unknown name as URL."""
        client = connect("http://custom.example.com", token="custom-token")

        assert client.url == "http://custom.example.com"
        assert client.token == "custom-token"

    def test_connect_with_tenant_id(self):
        """connect() passes tenant_id to client."""
        client = connect("http://example.com", tenant_id="t1")

        assert client.tenant_id == "t1"


class TestConnectionFromFile:
    """Tests for file-based connection registry."""

    def setup_method(self):
        """Clear connections before each test."""
        _connections.clear()

    def test_connect_from_file(self, tmp_path):
        """connect() loads connections from file."""
        import json

        # Create mock connections file
        connections_dir = tmp_path / ".mail-proxy"
        connections_dir.mkdir()
        connections_file = connections_dir / "connections.json"
        connections_file.write_text(json.dumps({
            "file-conn": {"url": "http://file.example.com", "token": "file-token"}
        }))

        with patch("pathlib.Path.home", return_value=tmp_path):
            client = connect("file-conn")

        assert client.url == "http://file.example.com"
        assert client.token == "file-token"

    def test_connect_file_invalid_json(self, tmp_path):
        """connect() handles invalid JSON in connections file."""
        # Create mock connections file with invalid JSON
        connections_dir = tmp_path / ".mail-proxy"
        connections_dir.mkdir()
        connections_file = connections_dir / "connections.json"
        connections_file.write_text("invalid json {}")

        with patch("pathlib.Path.home", return_value=tmp_path):
            # Should fall through to treating as URL
            client = connect("unknown-conn")

        assert client.url == "unknown-conn"
