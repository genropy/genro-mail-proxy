# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Python client for interacting with mail-proxy instances.

This module provides a Pythonic interface for connecting to running
mail-proxy servers and managing messages, accounts, tenants, and instance config.

Uses @smartasync decorator for automatic sync/async context detection:
- In sync context (REPL, CLI): methods work without await
- In async context (tests, server): methods return coroutines for await

API Style: RPC-style paths matching api_base.py generation:
- /{entity}/{method_name} with snake_case
- GET for read operations, POST for write operations
- Query parameters for simple args, JSON body for complex

Usage in REPL (sync):
    >>> from tools.http_client import MailProxyClient
    >>> client = MailProxyClient("http://localhost:8000", token="secret")
    >>> client.status()
    {'ok': True, 'active': True}
    >>> client.messages.list()
    [...]

Usage in async context:
    >>> async def main():
    ...     client = MailProxyClient("http://localhost:8000", token="secret")
    ...     status = await client.status()
    ...     messages = await client.messages.list()
"""

from __future__ import annotations

import builtins
from dataclasses import dataclass, field
from typing import Any

import httpx
from genro_toolbox import smartasync

# =============================================================================
# Dataclasses
# =============================================================================


@dataclass
class Message:
    """Email message representation for the client API.

    Attributes:
        id: Unique message identifier (client-provided).
        pk: Internal primary key (UUID).
        tenant_id: Tenant identifier.
        account_id: SMTP account used for delivery.
        subject: Email subject line.
        from_addr: Sender email address.
        to: List of recipient addresses.
        status: Current status (pending, sent, error, deferred).
        priority: Delivery priority (0=immediate, 1=high, 2=medium, 3=low).
        batch_code: Batch/campaign identifier.
        created_at: ISO timestamp when message was queued.
        smtp_ts: Unix timestamp when sent (if delivered).
        deferred_ts: Unix timestamp for deferred delivery.
        error: Error message (if failed).
    """

    id: str
    pk: str | None = None
    tenant_id: str | None = None
    account_id: str | None = None
    subject: str = ""
    from_addr: str = ""
    to: list[str] = field(default_factory=list)
    status: str = "pending"
    priority: int = 2
    batch_code: str | None = None
    created_at: str | None = None
    smtp_ts: int | None = None
    deferred_ts: int | None = None
    error: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Message:
        """Create a Message from API response dictionary."""
        payload = data.get("payload") or data.get("message") or {}
        return cls(
            id=data["id"],
            pk=data.get("pk"),
            tenant_id=data.get("tenant_id"),
            account_id=data.get("account_id"),
            subject=payload.get("subject", ""),
            from_addr=payload.get("from", ""),
            to=payload.get("to", []),
            status=data.get("status", "pending"),
            priority=data.get("priority", 2),
            batch_code=data.get("batch_code"),
            created_at=data.get("created_at"),
            smtp_ts=data.get("smtp_ts"),
            deferred_ts=data.get("deferred_ts"),
            error=data.get("error"),
        )

    def __repr__(self) -> str:
        subj = self.subject[:30] + "..." if len(self.subject) > 30 else self.subject
        return f"Message(id='{self.id}', subject='{subj}', status='{self.status}')"


@dataclass
class Account:
    """SMTP account configuration for the client API.

    Attributes:
        id: Unique account identifier.
        tenant_id: Associated tenant.
        host: SMTP server hostname.
        port: SMTP server port.
        user: SMTP username for authentication.
        use_tls: Whether to use TLS.
        ttl: Connection TTL in seconds.
        limit_per_minute: Rate limit per minute.
        limit_per_hour: Rate limit per hour.
        limit_per_day: Rate limit per day.
        limit_behavior: What to do when rate limited (defer, reject).
        batch_size: Max messages per dispatch cycle.
        is_pec_account: Whether this is a PEC account (EE).
    """

    id: str
    tenant_id: str
    host: str = ""
    port: int = 587
    user: str | None = None
    use_tls: bool = True
    ttl: int = 300
    limit_per_minute: int | None = None
    limit_per_hour: int | None = None
    limit_per_day: int | None = None
    limit_behavior: str = "defer"
    batch_size: int | None = None
    is_pec_account: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Account:
        """Create an Account from API response dictionary."""
        return cls(
            id=data["id"],
            tenant_id=data["tenant_id"],
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
            is_pec_account=bool(data.get("is_pec_account", False)),
        )

    def __repr__(self) -> str:
        pec = " [PEC]" if self.is_pec_account else ""
        return f"Account(id='{self.id}', host='{self.host}:{self.port}'{pec})"


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
        suspended_batches: Set of suspended batch codes (or {"*"} for all).
        api_key_expires_at: Unix timestamp when API key expires (EE).
    """

    id: str
    name: str | None = None
    active: bool = True
    client_base_url: str | None = None
    client_sync_path: str | None = None
    client_attachment_path: str | None = None
    suspended_batches: set[str] = field(default_factory=set)
    api_key_expires_at: int | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Tenant:
        """Create a Tenant from API response dictionary."""
        suspended = data.get("suspended_batches") or ""
        if isinstance(suspended, str):
            suspended_set = set(suspended.split(",")) if suspended else set()
            suspended_set.discard("")
        else:
            suspended_set = set(suspended)

        return cls(
            id=data["id"],
            name=data.get("name"),
            active=bool(data.get("active", True)),
            client_base_url=data.get("client_base_url"),
            client_sync_path=data.get("client_sync_path"),
            client_attachment_path=data.get("client_attachment_path"),
            suspended_batches=suspended_set,
            api_key_expires_at=data.get("api_key_expires_at"),
        )

    def __repr__(self) -> str:
        status = "active" if self.active else "inactive"
        return f"Tenant(id='{self.id}', name='{self.name}', {status})"


@dataclass
class CommandLogEntry:
    """API command log entry for audit trail.

    Attributes:
        id: Log entry ID.
        command_ts: Unix timestamp of command.
        endpoint: HTTP method + path.
        tenant_id: Tenant context (if applicable).
        payload: Request body.
        response_status: HTTP response status code.
        response_body: Response body (summary).
    """

    id: int
    command_ts: int
    endpoint: str
    tenant_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    response_status: int | None = None
    response_body: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CommandLogEntry:
        """Create a CommandLogEntry from API response dictionary."""
        return cls(
            id=data["id"],
            command_ts=data["command_ts"],
            endpoint=data["endpoint"],
            tenant_id=data.get("tenant_id"),
            payload=data.get("payload") or {},
            response_status=data.get("response_status"),
            response_body=data.get("response_body"),
        )

    def __repr__(self) -> str:
        return f"CommandLogEntry(id={self.id}, endpoint='{self.endpoint}')"


# =============================================================================
# Sub-APIs
# =============================================================================


class MessagesAPI:
    """Sub-API for managing email messages in the queue. Access via ``client.messages``."""

    def __init__(self, client: MailProxyClient):
        self._client = client

    @smartasync
    async def list(
        self,
        tenant_id: str | None = None,
        active_only: bool = False,
        include_history: bool = False,
    ) -> builtins.list[Message]:
        """List messages in the queue."""
        params: dict[str, Any] = {}
        if tenant_id or self._client.tenant_id:
            params["tenant_id"] = tenant_id or self._client.tenant_id
        if active_only:
            params["active_only"] = "true"
        if include_history:
            params["include_history"] = "true"
        data = await self._client._get("/messages/list", params=params or None)
        return [Message.from_dict(m) for m in data]

    @smartasync
    async def get(self, message_id: str, tenant_id: str) -> Message:
        """Get a specific message by ID."""
        data = await self._client._get(
            "/messages/get",
            params={"message_id": message_id, "tenant_id": tenant_id},
        )
        return Message.from_dict(data)

    @smartasync
    async def add(
        self,
        id: str,
        tenant_id: str,
        account_id: str,
        from_addr: str,
        to: builtins.list[str],
        subject: str,
        body: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Add a single message to the queue."""
        payload = {
            "id": id,
            "tenant_id": tenant_id,
            "account_id": account_id,
            "from_addr": from_addr,
            "to": to,
            "subject": subject,
            "body": body,
            **kwargs,
        }
        return await self._client._post("/messages/add", payload)

    @smartasync
    async def add_batch(
        self,
        messages: builtins.list[dict[str, Any]],
        default_priority: int | None = None,
    ) -> dict[str, Any]:
        """Add multiple messages to the queue."""
        payload: dict[str, Any] = {"messages": messages}
        if default_priority is not None:
            payload["default_priority"] = default_priority
        return await self._client._post("/messages/add_batch", payload)

    @smartasync
    async def delete(self, message_pk: str) -> bool:
        """Delete a message by internal primary key (UUID)."""
        await self._client._post("/messages/delete", {"message_pk": message_pk})
        return True

    @smartasync
    async def delete_batch(
        self,
        tenant_id: str,
        ids: builtins.list[str],
    ) -> dict[str, Any]:
        """Delete multiple messages by their IDs."""
        return await self._client._post(
            "/messages/delete_batch",
            {"tenant_id": tenant_id, "ids": ids},
        )

    @smartasync
    async def cleanup(
        self,
        tenant_id: str,
        older_than_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Remove reported messages older than retention period."""
        params: dict[str, Any] = {"tenant_id": tenant_id}
        if older_than_seconds is not None:
            params["older_than_seconds"] = older_than_seconds
        return await self._client._get("/messages/cleanup", params=params)

    @smartasync
    async def count_active(self) -> int:
        """Count messages awaiting delivery."""
        data = await self._client._get("/messages/count_active")
        return data

    @smartasync
    async def count_pending_for_tenant(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> int:
        """Count pending messages for a tenant."""
        params: dict[str, Any] = {"tenant_id": tenant_id}
        if batch_code:
            params["batch_code"] = batch_code
        data = await self._client._get("/messages/count_pending_for_tenant", params=params)
        return data


class AccountsAPI:
    """Sub-API for managing SMTP accounts. Access via ``client.accounts``."""

    def __init__(self, client: MailProxyClient):
        self._client = client

    @smartasync
    async def list(self, tenant_id: str | None = None) -> builtins.list[Account]:
        """List all SMTP accounts."""
        tid = tenant_id or self._client.tenant_id
        params = {"tenant_id": tid} if tid else None
        data = await self._client._get("/accounts/list", params=params)
        return [Account.from_dict(a) for a in data]

    @smartasync
    async def get(self, tenant_id: str, account_id: str) -> Account:
        """Get a specific account."""
        data = await self._client._get(
            "/accounts/get",
            params={"tenant_id": tenant_id, "account_id": account_id},
        )
        return Account.from_dict(data)

    @smartasync
    async def add(
        self,
        id: str,
        tenant_id: str,
        host: str,
        port: int,
        user: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Add or update an SMTP account."""
        payload = {
            "id": id,
            "tenant_id": tenant_id,
            "host": host,
            "port": port,
            "user": user,
            "password": password,
            "use_tls": use_tls,
            **kwargs,
        }
        return await self._client._post("/accounts/add", payload)

    @smartasync
    async def delete(self, tenant_id: str, account_id: str) -> bool:
        """Delete an SMTP account."""
        await self._client._post(
            "/accounts/delete",
            {"tenant_id": tenant_id, "account_id": account_id},
        )
        return True

    # EE methods

    @smartasync
    async def add_pec(
        self,
        id: str,
        tenant_id: str,
        host: str,
        port: int,
        imap_host: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Add or update a PEC account with IMAP configuration (EE)."""
        payload = {
            "id": id,
            "tenant_id": tenant_id,
            "host": host,
            "port": port,
            "imap_host": imap_host,
            **kwargs,
        }
        return await self._client._post("/accounts/add_pec", payload)

    @smartasync
    async def list_pec(self) -> builtins.list[Account]:
        """List all PEC accounts across all tenants (EE)."""
        data = await self._client._get("/accounts/list_pec")
        return [Account.from_dict(a) for a in data]

    @smartasync
    async def get_pec_ids(self) -> set[str]:
        """Get set of account IDs that are PEC accounts (EE)."""
        data = await self._client._get("/accounts/get_pec_ids")
        return set(data)


class TenantsAPI:
    """Sub-API for managing tenants. Access via ``client.tenants``."""

    def __init__(self, client: MailProxyClient):
        self._client = client

    @smartasync
    async def list(self, active_only: bool = False) -> builtins.list[Tenant]:
        """List all tenants."""
        params = {"active_only": "true"} if active_only else None
        data = await self._client._get("/tenants/list", params=params)
        return [Tenant.from_dict(t) for t in data]

    @smartasync
    async def get(self, tenant_id: str) -> Tenant:
        """Get a specific tenant."""
        data = await self._client._get("/tenants/get", params={"tenant_id": tenant_id})
        return Tenant.from_dict(data)

    @smartasync
    async def add(
        self,
        id: str,
        name: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Add or update a tenant."""
        payload = {"id": id, "name": name, **kwargs}
        return await self._client._post("/tenants/add", payload)

    @smartasync
    async def update(
        self,
        tenant_id: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Update tenant fields."""
        payload = {"tenant_id": tenant_id, **kwargs}
        return await self._client._post("/tenants/update", payload)

    @smartasync
    async def delete(self, tenant_id: str) -> bool:
        """Delete a tenant."""
        await self._client._post("/tenants/delete", {"tenant_id": tenant_id})
        return True

    @smartasync
    async def suspend_batch(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict[str, Any]:
        """Suspend sending for a tenant, optionally for a specific batch."""
        payload: dict[str, Any] = {"tenant_id": tenant_id}
        if batch_code:
            payload["batch_code"] = batch_code
        return await self._client._post("/tenants/suspend_batch", payload)

    @smartasync
    async def activate_batch(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict[str, Any]:
        """Resume sending for a tenant, optionally for a specific batch."""
        payload: dict[str, Any] = {"tenant_id": tenant_id}
        if batch_code:
            payload["batch_code"] = batch_code
        return await self._client._post("/tenants/activate_batch", payload)

    @smartasync
    async def get_suspended_batches(self, tenant_id: str) -> set[str]:
        """Get suspended batches for a tenant."""
        data = await self._client._get(
            "/tenants/get_suspended_batches",
            params={"tenant_id": tenant_id},
        )
        return set(data.get("suspended_batches", []))

    # EE methods

    @smartasync
    async def create_api_key(
        self,
        tenant_id: str,
        expires_at: int | None = None,
    ) -> dict[str, Any]:
        """Create a new API key for a tenant (EE)."""
        payload: dict[str, Any] = {"tenant_id": tenant_id}
        if expires_at is not None:
            payload["expires_at"] = expires_at
        return await self._client._post("/tenants/create_api_key", payload)

    @smartasync
    async def revoke_api_key(self, tenant_id: str) -> dict[str, Any]:
        """Revoke the API key for a tenant (EE)."""
        return await self._client._post("/tenants/revoke_api_key", {"tenant_id": tenant_id})


class InstanceAPI:
    """Sub-API for instance-level operations. Access via ``client.instance``."""

    def __init__(self, client: MailProxyClient):
        self._client = client

    @smartasync
    async def health(self) -> dict[str, Any]:
        """Health check for container orchestration."""
        return await self._client._get("/health")

    @smartasync
    async def status(self) -> dict[str, Any]:
        """Authenticated service status."""
        return await self._client._get("/instance/status")

    @smartasync
    async def get(self) -> dict[str, Any]:
        """Get instance configuration."""
        return await self._client._get("/instance/get")

    @smartasync
    async def update(
        self,
        name: str | None = None,
        api_token: str | None = None,
        edition: str | None = None,
    ) -> dict[str, Any]:
        """Update instance configuration."""
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if api_token is not None:
            payload["api_token"] = api_token
        if edition is not None:
            payload["edition"] = edition
        return await self._client._post("/instance/update", payload)

    @smartasync
    async def run_now(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Trigger immediate dispatch cycle."""
        payload: dict[str, Any] = {}
        if tenant_id:
            payload["tenant_id"] = tenant_id
        return await self._client._post("/instance/run_now", payload)

    @smartasync
    async def suspend(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict[str, Any]:
        """Suspend message sending for a tenant."""
        payload: dict[str, Any] = {"tenant_id": tenant_id}
        if batch_code:
            payload["batch_code"] = batch_code
        return await self._client._post("/instance/suspend", payload)

    @smartasync
    async def activate(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict[str, Any]:
        """Resume message sending for a tenant."""
        payload: dict[str, Any] = {"tenant_id": tenant_id}
        if batch_code:
            payload["batch_code"] = batch_code
        return await self._client._post("/instance/activate", payload)

    @smartasync
    async def get_sync_status(self) -> dict[str, Any]:
        """Get sync status for all tenants."""
        return await self._client._get("/instance/get_sync_status")

    @smartasync
    async def upgrade_to_ee(self) -> dict[str, Any]:
        """Upgrade instance from CE to EE."""
        return await self._client._post("/instance/upgrade_to_ee", {})

    # EE methods

    @smartasync
    async def get_bounce_config(self) -> dict[str, Any]:
        """Get bounce detection configuration (EE)."""
        return await self._client._get("/instance/get_bounce_config")

    @smartasync
    async def set_bounce_config(self, **kwargs: Any) -> dict[str, Any]:
        """Set bounce detection configuration (EE)."""
        return await self._client._post("/instance/set_bounce_config", kwargs)

    @smartasync
    async def reload_bounce(self) -> dict[str, Any]:
        """Reload bounce detection configuration at runtime (EE)."""
        return await self._client._post("/instance/reload_bounce", {})


class CommandLogAPI:
    """Sub-API for API audit trail. Access via ``client.command_log``."""

    def __init__(self, client: MailProxyClient):
        self._client = client

    @smartasync
    async def list(
        self,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
        endpoint_filter: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> builtins.list[CommandLogEntry]:
        """List logged commands with optional filters."""
        params: dict[str, Any] = {"limit": limit, "offset": offset}
        if tenant_id:
            params["tenant_id"] = tenant_id
        if since_ts:
            params["since_ts"] = since_ts
        if until_ts:
            params["until_ts"] = until_ts
        if endpoint_filter:
            params["endpoint_filter"] = endpoint_filter
        data = await self._client._get("/command_log/list", params=params)
        return [CommandLogEntry.from_dict(c) for c in data]

    @smartasync
    async def get(self, command_id: int) -> CommandLogEntry:
        """Get a specific command by ID."""
        data = await self._client._get("/command_log/get", params={"command_id": command_id})
        return CommandLogEntry.from_dict(data)

    @smartasync
    async def export(
        self,
        tenant_id: str | None = None,
        since_ts: int | None = None,
        until_ts: int | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """Export commands in replay-friendly format."""
        params: dict[str, Any] = {}
        if tenant_id:
            params["tenant_id"] = tenant_id
        if since_ts:
            params["since_ts"] = since_ts
        if until_ts:
            params["until_ts"] = until_ts
        return await self._client._get("/command_log/export", params=params or None)

    @smartasync
    async def purge(self, threshold_ts: int) -> dict[str, Any]:
        """Delete command logs older than threshold."""
        return await self._client._post("/command_log/purge", {"threshold_ts": threshold_ts})


# =============================================================================
# Main Client
# =============================================================================


class MailProxyClient:
    """Client for interacting with a mail-proxy server.

    Uses @smartasync for automatic sync/async context detection.
    API paths follow RPC-style: /{entity}/{method_name}

    Attributes:
        url: Base URL of the mail-proxy server.
        name: Optional name for this connection.
        tenant_id: Default tenant ID for operations.
        messages: API for managing messages.
        accounts: API for managing SMTP accounts.
        tenants: API for managing tenants.
        instance: API for instance-level operations.
        command_log: API for audit trail.

    Example (sync context - REPL):
        >>> client = MailProxyClient("http://localhost:8000", token="secret")
        >>> client.status()
        {'ok': True, 'active': True}
        >>> client.messages.list()
        [Message(...), ...]

    Example (async context - tests):
        >>> async def test():
        ...     client = MailProxyClient("http://localhost:8000", token="secret")
        ...     status = await client.status()
        ...     messages = await client.messages.list()
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

        # Sub-APIs
        self.messages = MessagesAPI(self)
        self.accounts = AccountsAPI(self)
        self.tenants = TenantsAPI(self)
        self.instance = InstanceAPI(self)
        self.command_log = CommandLogAPI(self)

    def _headers(self) -> dict[str, str]:
        """Build request headers."""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-API-Token"] = self.token
        return headers

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """Make a GET request."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{self.url}{path}",
                headers=self._headers(),
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def _post(
        self,
        path: str,
        data: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make a POST request."""
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{self.url}{path}",
                headers=self._headers(),
                json=data or {},
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def _put(
        self,
        path: str,
        data: dict[str, Any] | None = None,
    ) -> Any:
        """Make a PUT request."""
        async with httpx.AsyncClient() as client:
            resp = await client.put(
                f"{self.url}{path}",
                headers=self._headers(),
                json=data or {},
                timeout=30,
            )
            resp.raise_for_status()
            return resp.json()

    async def _delete(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Make a DELETE request."""
        async with httpx.AsyncClient() as client:
            resp = await client.delete(
                f"{self.url}{path}",
                headers=self._headers(),
                params=params,
                timeout=30,
            )
            resp.raise_for_status()
            if resp.content:
                return resp.json()
            return {"ok": True}

    # Convenience methods on main client

    @smartasync
    async def status(self) -> dict[str, Any]:
        """Get server status."""
        return await self.instance.status()

    @smartasync
    async def health(self) -> bool:
        """Check if server is healthy."""
        try:
            result = await self.instance.health()
            return result.get("status") == "ok"
        except Exception:
            return False

    @smartasync
    async def run_now(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Trigger immediate dispatch cycle."""
        return await self.instance.run_now(tenant_id)

    @smartasync
    async def suspend(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict[str, Any]:
        """Suspend message sending for a tenant."""
        return await self.instance.suspend(tenant_id, batch_code)

    @smartasync
    async def activate(
        self,
        tenant_id: str,
        batch_code: str | None = None,
    ) -> dict[str, Any]:
        """Resume message sending for a tenant."""
        return await self.instance.activate(tenant_id, batch_code)

    def __repr__(self) -> str:
        return f"<MailProxyClient '{self.name}'>"


# =============================================================================
# Connection Registry
# =============================================================================

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
    tenant_id: str | None = None,
) -> MailProxyClient:
    """Connect to a mail-proxy server.

    Args:
        name_or_url: Either a registered connection name or a URL.
        token: API token (optional if using registered connection).
        name: Display name for the connection (optional).
        tenant_id: Default tenant ID for multi-tenant operations.

    Returns:
        MailProxyClient instance.

    Example:
        >>> register_connection("prod", "https://mail.example.com", "secret")
        >>> client = connect("prod")
        >>> client.status()
    """
    # Check in-memory registry first
    if name_or_url in _connections:
        conn = _connections[name_or_url]
        return MailProxyClient(
            url=conn["url"],
            token=token or conn.get("token"),
            name=name or name_or_url,
            tenant_id=tenant_id,
        )

    # Check file-based registry
    file_connections = _load_connections_from_file()
    if name_or_url in file_connections:
        conn = file_connections[name_or_url]
        return MailProxyClient(
            url=conn["url"],
            token=token or conn.get("token"),
            name=name or name_or_url,
            tenant_id=tenant_id,
        )

    # Treat as URL
    return MailProxyClient(
        url=name_or_url,
        token=token,
        name=name or name_or_url,
        tenant_id=tenant_id,
    )
