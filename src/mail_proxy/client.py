# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Python client for interacting with mail-proxy instances.

This module provides a Pythonic interface for connecting to running
mail-proxy servers and managing messages, accounts, and tenants.

Usage in REPL:
    >>> from mail_proxy.client import MailProxyClient
    >>> proxy = MailProxyClient("http://localhost:8000", token="secret")
    >>> proxy.status()
    {'ok': True, 'active': True}
    >>> proxy.messages.list()
    [...]
    >>> proxy.messages.pending()
    [...]

Example:
    Interactive session::

        $ mail-proxy connect myproxy
        Connected to myproxy at http://localhost:8000

        proxy> proxy.status()
        {'ok': True, 'active': True, 'queue_size': 42}

        proxy> proxy.messages.pending()
        [Message(id='msg-1', subject='Hello', status='pending'), ...]

        proxy> proxy.run_now()
        {'ok': True}
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from typing import Any

import aiohttp


@dataclass
class Message:
    """Email message representation for the client API.

    Attributes:
        id: Unique message identifier.
        account_id: SMTP account used for delivery.
        subject: Email subject line.
        from_addr: Sender email address.
        to: List of recipient addresses.
        status: Current status (pending, sent, error, deferred).
        priority: Delivery priority (0=immediate, 1=high, 2=medium, 3=low).
        created_at: ISO timestamp when message was queued.
        sent_ts: Unix timestamp when sent (if delivered).
        error_ts: Unix timestamp when error occurred (if failed).
        error: Error message (if failed).
    """

    id: str
    account_id: str | None = None
    subject: str = ""
    from_addr: str = ""
    to: list[str] = field(default_factory=list)
    status: str = "pending"
    priority: int = 2
    created_at: str | None = None
    sent_ts: int | None = None
    error_ts: int | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Create a Message from API response dictionary.

        Args:
            data: Message data from API response including nested ``message`` payload.

        Returns:
            Message: Populated instance with status derived from timestamps.
        """
        msg = data.get("message", {})
        status = "pending"
        if data.get("sent_ts"):
            status = "sent"
        elif data.get("error_ts"):
            status = "error"
        elif data.get("deferred_ts"):
            status = "deferred"

        return cls(
            id=data["id"],
            account_id=data.get("account_id"),
            subject=msg.get("subject", ""),
            from_addr=msg.get("from", ""),
            to=msg.get("to", []),
            status=status,
            priority=data.get("priority", 2),
            created_at=data.get("created_at"),
            sent_ts=data.get("sent_ts"),
            error_ts=data.get("error_ts"),
            error=data.get("error"),
        )

    def __repr__(self) -> str:
        return f"Message(id='{self.id}', subject='{self.subject[:30]}...', status='{self.status}')"


@dataclass
class Account:
    """SMTP account configuration for the client API.

    Attributes:
        id: Unique account identifier.
        tenant_id: Associated tenant (if multi-tenant).
        host: SMTP server hostname.
        port: SMTP server port.
        user: SMTP username for authentication.
        use_tls: Whether to use TLS (STARTTLS on 587, implicit on 465).
        ttl: Connection TTL in seconds.
        limit_per_minute: Rate limit per minute.
        limit_per_hour: Rate limit per hour.
        limit_per_day: Rate limit per day.
        limit_behavior: What to do when rate limited (defer, reject).
        batch_size: Max messages per dispatch cycle.
    """

    id: str
    tenant_id: str | None = None
    host: str = ""
    port: int = 587
    user: str | None = None
    use_tls: bool = True
    ttl: int = 300
    limit_per_minute: int | None = None
    limit_per_hour: int | None = None
    limit_per_day: int | None = None
    limit_behavior: str | None = "defer"
    batch_size: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        """Create an Account from API response dictionary.

        Args:
            data: Account data from API response.

        Returns:
            Account: Populated instance.
        """
        return cls(
            id=data["id"],
            tenant_id=data.get("tenant_id"),
            host=data.get("host", ""),
            port=data.get("port", 587),
            user=data.get("user"),
            use_tls=bool(data.get("use_tls", True)),
            ttl=data.get("ttl", 300),
            limit_per_minute=data.get("limit_per_minute"),
            limit_per_hour=data.get("limit_per_hour"),
            limit_per_day=data.get("limit_per_day"),
            limit_behavior=data.get("limit_behavior", "defer"),
            batch_size=data.get("batch_size"),
        )

    def __repr__(self) -> str:
        return f"Account(id='{self.id}', host='{self.host}:{self.port}')"


@dataclass
class Tenant:
    """Multi-tenant configuration for the client API.

    Attributes:
        id: Unique tenant identifier.
        name: Human-readable tenant name.
        active: Whether tenant is active for message processing.
        client_base_url: Base URL for client sync/attachment endpoints.
        client_sync_path: Path for delivery report sync endpoint.
        client_attachment_path: Path for attachment fetch endpoint.
    """

    id: str
    name: str | None = None
    active: bool = True
    client_base_url: str | None = None
    client_sync_path: str | None = None
    client_attachment_path: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tenant:
        """Create a Tenant from API response dictionary.

        Args:
            data: Tenant data from API response.

        Returns:
            Tenant: Populated instance.
        """
        return cls(
            id=data["id"],
            name=data.get("name"),
            active=bool(data.get("active", True)),
            client_base_url=data.get("client_base_url"),
            client_sync_path=data.get("client_sync_path"),
            client_attachment_path=data.get("client_attachment_path"),
        )

    def __repr__(self) -> str:
        status = "active" if self.active else "inactive"
        return f"Tenant(id='{self.id}', name='{self.name}', {status})"


class MessagesAPI:
    """Sub-API for managing email messages in the queue.

    Access via ``client.messages``.
    """

    def __init__(self, client: MailProxyClient):
        self._client = client

    def list(self, limit: int = 100, active_only: bool = False) -> builtins.list[Message]:
        """List messages in the queue.

        Args:
            limit: Maximum number of messages to return.
            active_only: If True, only return non-sent messages.

        Returns:
            List of Message objects.
        """
        params: dict[str, Any] = {"limit": limit}
        if active_only:
            params["active_only"] = "true"
        if self._client.tenant_id:
            params["tenant_id"] = self._client.tenant_id
        data = self._client._get("/messages", params=params)
        return [Message.from_dict(m) for m in data.get("messages", [])]

    def pending(self, limit: int = 100) -> builtins.list[Message]:
        """List pending (not yet sent) messages."""
        return [m for m in self.list(limit=limit) if m.status == "pending"]

    def sent(self, limit: int = 100) -> builtins.list[Message]:
        """List sent messages."""
        return [m for m in self.list(limit=limit) if m.status == "sent"]

    def errors(self, limit: int = 100) -> builtins.list[Message]:
        """List messages with errors."""
        return [m for m in self.list(limit=limit) if m.status == "error"]

    def get(self, message_id: str) -> Message | None:
        """Get a specific message by ID."""
        for m in self.list():
            if m.id == message_id:
                return m
        return None

    def add(self, messages: builtins.list[dict[str, Any]]) -> dict[str, Any]:
        """Add messages to the queue."""
        return self._client._post("/commands/add-messages", {"messages": messages})

    def delete(self, message_ids: str | builtins.list[str], tenant_id: str | None = None) -> dict[str, Any]:
        """Delete messages from the queue.

        Args:
            message_ids: Single ID or list of message IDs to delete.
            tenant_id: Tenant ID (required for multi-tenant mode).

        Returns:
            dict with 'ok', 'removed' count, 'not_found' and 'unauthorized' lists.
        """
        if isinstance(message_ids, str):
            message_ids = [message_ids]
        params = {"tenant_id": tenant_id} if tenant_id else None
        return self._client._post("/commands/delete-messages", {"ids": message_ids}, params=params)

    def cleanup(self, tenant_id: str, older_than_seconds: int | None = None) -> dict[str, Any]:
        """Remove reported messages older than retention period.

        Args:
            tenant_id: Tenant ID (required).
            older_than_seconds: Override retention period (optional).

        Returns:
            dict with 'ok' and 'removed' count.
        """
        params = {"tenant_id": tenant_id}
        payload: dict[str, Any] = {}
        if older_than_seconds is not None:
            payload["older_than_seconds"] = older_than_seconds
        return self._client._post("/commands/cleanup-messages", payload, params=params)

    def __repr__(self) -> str:
        return f"<MessagesAPI: {len(self.list())} messages>"


class AccountsAPI:
    """Sub-API for managing SMTP accounts.

    Access via ``client.accounts``.
    """

    def __init__(self, client: MailProxyClient):
        self._client = client

    def list(self) -> builtins.list[Account]:
        """List all configured SMTP accounts.

        Returns:
            List of Account objects.
        """
        params: dict[str, Any] = {}
        if self._client.tenant_id:
            params["tenant_id"] = self._client.tenant_id
        data = self._client._get("/accounts", params=params if params else None)
        return [Account.from_dict(a) for a in data.get("accounts", [])]

    def get(self, account_id: str) -> Account | None:
        """Get a specific account by ID."""
        for a in self.list():
            if a.id == account_id:
                return a
        return None

    def add(self, account: dict[str, Any]) -> dict[str, Any]:
        """Add a new account."""
        return self._client._post("/account", account)

    def delete(self, account_id: str) -> dict[str, Any]:
        """Delete an account."""
        return self._client._delete(f"/account/{account_id}")

    def __repr__(self) -> str:
        return f"<AccountsAPI: {len(self.list())} accounts>"


class TenantsAPI:
    """Sub-API for managing multi-tenant configurations.

    Access via ``client.tenants``.
    """

    def __init__(self, client: MailProxyClient):
        self._client = client

    def list(self) -> builtins.list[Tenant]:
        """List all tenants."""
        data = self._client._get("/tenants")
        return [Tenant.from_dict(t) for t in data.get("tenants", [])]

    def get(self, tenant_id: str) -> Tenant | None:
        """Get a specific tenant by ID."""
        data = self._client._get(f"/tenant/{tenant_id}")
        if data:
            return Tenant.from_dict(data)
        return None

    def add(self, tenant: dict[str, Any]) -> dict[str, Any]:
        """Add a new tenant."""
        return self._client._post("/tenant", tenant)

    def delete(self, tenant_id: str) -> dict[str, Any]:
        """Delete a tenant."""
        return self._client._delete(f"/tenant/{tenant_id}")

    def __repr__(self) -> str:
        return f"<TenantsAPI: {len(self.list())} tenants>"


class MailProxyClient:
    """Client for interacting with a mail-proxy server.

    Attributes:
        url: Base URL of the mail-proxy server.
        name: Optional name for this connection.
        messages: API for managing messages.
        accounts: API for managing SMTP accounts.
        tenants: API for managing tenants.

    Example:
        >>> proxy = MailProxyClient("http://localhost:8000", token="secret")
        >>> proxy.status()
        {'ok': True, 'active': True}
        >>> proxy.messages.pending()
        [Message(...), ...]
    """

    def __init__(
        self,
        url: str = "http://localhost:8000",
        token: str | None = None,
        name: str | None = None,
        tenant_id: str | None = None,
    ):
        """Initialize the client.

        Args:
            url: Base URL of the mail-proxy server.
            token: API token for authentication.
            name: Optional name for this connection.
            tenant_id: Default tenant ID for multi-tenant operations.
        """
        self.url = url.rstrip("/")
        self.token = token
        self.name = name or url
        self.tenant_id = tenant_id
        self._session: aiohttp.ClientSession | None = None

        # Sub-APIs
        self.messages = MessagesAPI(self)
        self.accounts = AccountsAPI(self)
        self.tenants = TenantsAPI(self)

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-API-Token"] = self.token
        return headers

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request."""
        import requests
        url = f"{self.url}{path}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data: dict[str, Any] | None = None, params: dict[str, Any] | None = None) -> Any:
        """Make a POST request."""
        import requests
        url = f"{self.url}{path}"
        resp = requests.post(url, headers=self._headers(), json=data or {}, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> Any:
        """Make a DELETE request."""
        import requests
        url = f"{self.url}{path}"
        resp = requests.delete(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def status(self) -> dict[str, Any]:
        """Get server status."""
        return self._get("/status")

    def health(self) -> bool:
        """Check if server is healthy."""
        try:
            result = self.status()
            return result.get("ok", False)
        except Exception:
            return False

    def run_now(self) -> dict[str, Any]:
        """Trigger immediate dispatch cycle."""
        return self._post("/commands/run-now")

    def suspend(self, tenant_id: str, batch_code: str | None = None) -> dict[str, Any]:
        """Suspend message sending for a tenant, optionally for a specific batch.

        Args:
            tenant_id: Tenant ID to suspend.
            batch_code: Optional batch code. If provided, only suspends this batch.
                If None, suspends all message sending for the tenant.

        Returns:
            Response with suspended_batches list and pending_messages count.
        """
        params: dict[str, Any] = {"tenant_id": tenant_id}
        if batch_code:
            params["batch_code"] = batch_code
        return self._post("/commands/suspend", params=params)

    def activate(self, tenant_id: str, batch_code: str | None = None) -> dict[str, Any]:
        """Resume message sending for a tenant, optionally for a specific batch.

        Args:
            tenant_id: Tenant ID to activate.
            batch_code: Optional batch code. If provided, only activates this batch.
                If None, clears all suspensions for the tenant.

        Returns:
            Response with suspended_batches list and pending_messages count.
        """
        params: dict[str, Any] = {"tenant_id": tenant_id}
        if batch_code:
            params["batch_code"] = batch_code
        return self._post("/commands/activate", params=params)

    def stats(self) -> dict[str, Any]:
        """Get queue statistics."""
        messages = self.messages.list()
        pending = sum(1 for m in messages if m.status == "pending")
        sent = sum(1 for m in messages if m.status == "sent")
        errors = sum(1 for m in messages if m.status == "error")

        return {
            "total": len(messages),
            "pending": pending,
            "sent": sent,
            "errors": errors,
            "accounts": len(self.accounts.list()),
        }

    def __repr__(self) -> str:
        status = "connected" if self.health() else "disconnected"
        return f"<MailProxyClient '{self.name}' ({status})>"


# Registry for named connections
_connections: dict[str, dict[str, Any]] = {}


def _load_connections_from_file() -> dict[str, dict[str, Any]]:
    """Load connections from ~/.mail-proxy/connections.json."""
    import json as _json
    from pathlib import Path

    connections_file = Path.home() / ".mail-proxy" / "connections.json"
    if connections_file.exists():
        try:
            return _json.loads(connections_file.read_text())
        except _json.JSONDecodeError:
            pass
    return {}


def register_connection(
    name: str,
    url: str,
    token: str | None = None,
) -> None:
    """Register a named connection for later use.

    Args:
        name: Connection name.
        url: Server URL.
        token: API token.
    """
    _connections[name] = {"url": url, "token": token}


def connect(
    name_or_url: str,
    token: str | None = None,
    name: str | None = None,
) -> MailProxyClient:
    """Connect to a mail-proxy server.

    Args:
        name_or_url: Either a registered connection name or a URL.
        token: API token (optional if using registered connection).
        name: Display name for the connection (optional).

    Returns:
        MailProxyClient instance.

    Example:
        >>> register_connection("prod", "https://mail.example.com", "secret")
        >>> proxy = connect("prod")
        >>> proxy.status()
    """
    # Check in-memory registry first
    if name_or_url in _connections:
        conn = _connections[name_or_url]
        return MailProxyClient(
            url=conn["url"],
            token=token or conn.get("token"),
            name=name or name_or_url,
        )

    # Check file-based registry
    file_connections = _load_connections_from_file()
    if name_or_url in file_connections:
        conn = file_connections[name_or_url]
        return MailProxyClient(
            url=conn["url"],
            token=token or conn.get("token"),
            name=name or name_or_url,
        )

    # Treat as URL
    return MailProxyClient(url=name_or_url, token=token, name=name or name_or_url)
