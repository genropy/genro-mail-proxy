"""Python client for interacting with mail-proxy instances.

This module provides a Pythonic interface for connecting to running
mail-proxy servers and managing messages, accounts, and tenants.

Usage in REPL:
    >>> from async_mail_service.client import MailProxyClient
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

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

import aiohttp


@dataclass
class Message:
    """Represents an email message in the queue."""

    id: str
    account_id: Optional[str] = None
    subject: str = ""
    from_addr: str = ""
    to: List[str] = field(default_factory=list)
    status: str = "pending"
    priority: int = 2
    created_at: Optional[str] = None
    sent_ts: Optional[int] = None
    error_ts: Optional[int] = None
    error: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Message":
        """Create a Message from API response dict."""
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
    """Represents an SMTP account."""

    id: str
    tenant_id: Optional[str] = None
    host: str = ""
    port: int = 587
    user: Optional[str] = None
    use_tls: bool = True
    use_ssl: bool = False
    ttl: int = 300
    limit_per_minute: Optional[int] = None
    limit_per_hour: Optional[int] = None
    limit_per_day: Optional[int] = None
    limit_behavior: Optional[str] = "defer"
    batch_size: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Account":
        """Create an Account from API response dict."""
        return cls(
            id=data["id"],
            tenant_id=data.get("tenant_id"),
            host=data.get("host", ""),
            port=data.get("port", 587),
            user=data.get("user"),
            use_tls=bool(data.get("use_tls", True)),
            use_ssl=bool(data.get("use_ssl", False)),
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
    """Represents a tenant configuration."""

    id: str
    name: Optional[str] = None
    active: bool = True
    client_base_url: Optional[str] = None
    client_sync_path: Optional[str] = None
    client_attachment_path: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Tenant":
        """Create a Tenant from API response dict."""
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
    """API for managing messages."""

    def __init__(self, client: "MailProxyClient"):
        self._client = client

    def list(self, limit: int = 100, active_only: bool = False) -> List[Message]:
        """List all messages in the queue."""
        params = {"limit": limit}
        if active_only:
            params["active_only"] = "true"
        data = self._client._get("/messages", params=params)
        return [Message.from_dict(m) for m in data.get("messages", [])]

    def pending(self, limit: int = 100) -> List[Message]:
        """List pending (not yet sent) messages."""
        return [m for m in self.list(limit=limit) if m.status == "pending"]

    def sent(self, limit: int = 100) -> List[Message]:
        """List sent messages."""
        return [m for m in self.list(limit=limit) if m.status == "sent"]

    def errors(self, limit: int = 100) -> List[Message]:
        """List messages with errors."""
        return [m for m in self.list(limit=limit) if m.status == "error"]

    def get(self, message_id: str) -> Optional[Message]:
        """Get a specific message by ID."""
        for m in self.list():
            if m.id == message_id:
                return m
        return None

    def add(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Add messages to the queue."""
        return self._client._post("/commands/add-messages", {"messages": messages})

    def delete(self, message_ids: Union[str, List[str]]) -> Dict[str, Any]:
        """Delete messages from the queue."""
        if isinstance(message_ids, str):
            message_ids = [message_ids]
        return self._client._post("/commands/delete-messages", {"ids": message_ids})

    def __repr__(self) -> str:
        return f"<MessagesAPI: {len(self.list())} messages>"


class AccountsAPI:
    """API for managing SMTP accounts."""

    def __init__(self, client: "MailProxyClient"):
        self._client = client

    def list(self) -> List[Account]:
        """List all accounts."""
        data = self._client._get("/accounts")
        return [Account.from_dict(a) for a in data.get("accounts", [])]

    def get(self, account_id: str) -> Optional[Account]:
        """Get a specific account by ID."""
        for a in self.list():
            if a.id == account_id:
                return a
        return None

    def add(self, account: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new account."""
        return self._client._post("/account", account)

    def delete(self, account_id: str) -> Dict[str, Any]:
        """Delete an account."""
        return self._client._delete(f"/account/{account_id}")

    def __repr__(self) -> str:
        return f"<AccountsAPI: {len(self.list())} accounts>"


class TenantsAPI:
    """API for managing tenants."""

    def __init__(self, client: "MailProxyClient"):
        self._client = client

    def list(self) -> List[Tenant]:
        """List all tenants."""
        data = self._client._get("/tenants")
        return [Tenant.from_dict(t) for t in data.get("tenants", [])]

    def get(self, tenant_id: str) -> Optional[Tenant]:
        """Get a specific tenant by ID."""
        data = self._client._get(f"/tenant/{tenant_id}")
        if data:
            return Tenant.from_dict(data)
        return None

    def add(self, tenant: Dict[str, Any]) -> Dict[str, Any]:
        """Add a new tenant."""
        return self._client._post("/tenant", tenant)

    def delete(self, tenant_id: str) -> Dict[str, Any]:
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
        token: Optional[str] = None,
        name: Optional[str] = None,
    ):
        """Initialize the client.

        Args:
            url: Base URL of the mail-proxy server.
            token: API token for authentication.
            name: Optional name for this connection.
        """
        self.url = url.rstrip("/")
        self.token = token
        self.name = name or url
        self._session: Optional[aiohttp.ClientSession] = None

        # Sub-APIs
        self.messages = MessagesAPI(self)
        self.accounts = AccountsAPI(self)
        self.tenants = TenantsAPI(self)

    def _headers(self) -> Dict[str, str]:
        """Build request headers."""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-API-Token"] = self.token
        return headers

    def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """Make a GET request."""
        import requests
        url = f"{self.url}{path}"
        resp = requests.get(url, headers=self._headers(), params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, data: Optional[Dict[str, Any]] = None) -> Any:
        """Make a POST request."""
        import requests
        url = f"{self.url}{path}"
        resp = requests.post(url, headers=self._headers(), json=data or {}, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str) -> Any:
        """Make a DELETE request."""
        import requests
        url = f"{self.url}{path}"
        resp = requests.delete(url, headers=self._headers(), timeout=30)
        resp.raise_for_status()
        return resp.json()

    def status(self) -> Dict[str, Any]:
        """Get server status."""
        return self._get("/status")

    def health(self) -> bool:
        """Check if server is healthy."""
        try:
            result = self.status()
            return result.get("ok", False)
        except Exception:
            return False

    def run_now(self) -> Dict[str, Any]:
        """Trigger immediate dispatch cycle."""
        return self._post("/commands/run-now")

    def suspend(self) -> Dict[str, Any]:
        """Suspend the scheduler."""
        return self._post("/commands/suspend")

    def activate(self) -> Dict[str, Any]:
        """Activate the scheduler."""
        return self._post("/commands/activate")

    def stats(self) -> Dict[str, Any]:
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
_connections: Dict[str, Dict[str, Any]] = {}


def _load_connections_from_file() -> Dict[str, Dict[str, Any]]:
    """Load connections from ~/.mail-proxy/connections.json."""
    from pathlib import Path
    import json as _json

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
    token: Optional[str] = None,
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
    token: Optional[str] = None,
    name: Optional[str] = None,
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
