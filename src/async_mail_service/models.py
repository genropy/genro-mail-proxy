# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Pydantic models for multi-tenant mail service.

This module defines the data models used throughout the application for
validation, serialization, and type safety.

Models:
    - TenantAuth: Common authentication config for tenant HTTP endpoints
    - TenantRateLimits: Rate limiting configuration
    - Tenant: Complete tenant configuration
    - Account: SMTP account configuration
    - Message: Email message payload
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthMethod(str, Enum):
    """Authentication methods supported for HTTP endpoints.

    Attributes:
        NONE: No authentication required.
        BEARER: Bearer token authentication (Authorization: Bearer <token>).
        BASIC: HTTP Basic authentication (username:password).
    """

    NONE = "none"
    BEARER = "bearer"
    BASIC = "basic"


class TenantAuth(BaseModel):
    """Authentication configuration for tenant's HTTP endpoints.

    Used for both sync (delivery reports) and attachment fetcher endpoints.

    Attributes:
        method: Authentication method (none, bearer, basic).
        token: Bearer token (required if method is "bearer").
        user: Username (required if method is "basic").
        password: Password (required if method is "basic").
    """

    model_config = ConfigDict(extra="forbid")

    method: Annotated[
        AuthMethod,
        Field(default=AuthMethod.NONE, description="Authentication method")
    ]
    token: Annotated[
        str | None,
        Field(default=None, description="Bearer token for authentication")
    ]
    user: Annotated[
        str | None,
        Field(default=None, description="Username for basic auth")
    ]
    password: Annotated[
        str | None,
        Field(default=None, description="Password for basic auth")
    ]

    @field_validator("token")
    @classmethod
    def token_required_for_bearer(cls, v: str | None, info) -> str | None:
        """Validate that token is provided when method is bearer."""
        if info.data.get("method") == AuthMethod.BEARER and not v:
            raise ValueError("token is required when method is 'bearer'")
        return v

    @field_validator("password")
    @classmethod
    def basic_auth_requires_user_and_password(cls, v: str | None, info) -> str | None:
        """Validate that user and password are provided for basic auth."""
        if info.data.get("method") == AuthMethod.BASIC:
            if not info.data.get("user"):
                raise ValueError("user is required when method is 'basic'")
            if not v:
                raise ValueError("password is required when method is 'basic'")
        return v


# Alias for backward compatibility
TenantSyncAuth = TenantAuth


class TenantRateLimits(BaseModel):
    """Rate limiting configuration for a tenant.

    Attributes:
        hourly: Maximum emails per hour (0 = unlimited).
        daily: Maximum emails per day (0 = unlimited).
    """

    model_config = ConfigDict(extra="forbid")

    hourly: Annotated[
        int,
        Field(default=0, ge=0, description="Max emails per hour (0 = unlimited)")
    ]
    daily: Annotated[
        int,
        Field(default=0, ge=0, description="Max emails per day (0 = unlimited)")
    ]


class TenantCreate(BaseModel):
    """Payload for creating a new tenant.

    Attributes:
        id: Unique tenant identifier.
        name: Human-readable tenant name.
        client_auth: Common authentication for all HTTP endpoints.
        client_base_url: Base URL for tenant HTTP endpoints.
        client_sync_path: Path for delivery report callbacks.
        client_attachment_path: Path for attachment fetcher endpoint.
        rate_limits: Rate limiting settings.
        active: Whether tenant is active.
    """

    model_config = ConfigDict(extra="forbid")

    id: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$",
              description="Unique tenant identifier (alphanumeric, underscore, hyphen)")
    ]
    name: Annotated[
        str | None,
        Field(default=None, max_length=255, description="Human-readable tenant name")
    ]
    client_auth: Annotated[
        TenantAuth | None,
        Field(default=None, description="Common authentication for HTTP endpoints")
    ]
    client_base_url: Annotated[
        str | None,
        Field(default=None, description="Base URL for tenant HTTP endpoints")
    ]
    client_sync_path: Annotated[
        str | None,
        Field(default=None, description="Path for delivery report callbacks (default: /mail-proxy/sync)")
    ]
    client_attachment_path: Annotated[
        str | None,
        Field(default=None, description="Path for attachment fetcher endpoint (default: /mail-proxy/attachments)")
    ]
    rate_limits: Annotated[
        TenantRateLimits | None,
        Field(default=None, description="Rate limiting configuration")
    ]
    active: Annotated[
        bool,
        Field(default=True, description="Whether tenant is active")
    ]


class TenantUpdate(BaseModel):
    """Payload for updating an existing tenant.

    All fields are optional - only provided fields are updated.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[
        str | None,
        Field(default=None, max_length=255, description="Human-readable tenant name")
    ]
    client_auth: Annotated[
        TenantAuth | None,
        Field(default=None, description="Common authentication for HTTP endpoints")
    ]
    client_base_url: Annotated[
        str | None,
        Field(default=None, description="Base URL for tenant HTTP endpoints")
    ]
    client_sync_path: Annotated[
        str | None,
        Field(default=None, description="Path for delivery report callbacks")
    ]
    client_attachment_path: Annotated[
        str | None,
        Field(default=None, description="Path for attachment fetcher endpoint")
    ]
    rate_limits: Annotated[
        TenantRateLimits | None,
        Field(default=None, description="Rate limiting configuration")
    ]
    active: Annotated[
        bool | None,
        Field(default=None, description="Whether tenant is active")
    ]


class Tenant(TenantCreate):
    """Complete tenant model including timestamps.

    Extends TenantCreate with server-managed fields.
    """

    created_at: Annotated[
        datetime | None,
        Field(default=None, description="Timestamp when tenant was created")
    ]
    updated_at: Annotated[
        datetime | None,
        Field(default=None, description="Timestamp of last update")
    ]


class AccountCreate(BaseModel):
    """Payload for creating a new SMTP account.

    Attributes:
        id: Unique account identifier.
        tenant_id: Parent tenant identifier.
        host: SMTP server hostname.
        port: SMTP server port.
        user: SMTP authentication username.
        password: SMTP authentication password.
        use_tls: Whether to use STARTTLS.
        use_ssl: Whether to use SSL/TLS.
        batch_size: Max messages per dispatch cycle for this account.
        ttl: Connection TTL in seconds.
        limit_per_minute: Max emails per minute (0 = unlimited).
        limit_per_hour: Max emails per hour (0 = unlimited).
        limit_per_day: Max emails per day (0 = unlimited).
        limit_behavior: Behavior when rate limit is hit (defer or reject).
    """

    model_config = ConfigDict(extra="forbid")

    id: Annotated[
        str,
        Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$",
              description="Unique account identifier")
    ]
    tenant_id: Annotated[
        str,
        Field(min_length=1, max_length=64, description="Parent tenant identifier")
    ]
    host: Annotated[
        str,
        Field(min_length=1, max_length=255, description="SMTP server hostname")
    ]
    port: Annotated[
        int,
        Field(ge=1, le=65535, description="SMTP server port")
    ]
    user: Annotated[
        str | None,
        Field(default=None, max_length=255, description="SMTP username")
    ]
    password: Annotated[
        str | None,
        Field(default=None, max_length=255, description="SMTP password")
    ]
    use_tls: Annotated[
        bool,
        Field(default=True, description="Use STARTTLS")
    ]
    use_ssl: Annotated[
        bool,
        Field(default=False, description="Use SSL/TLS connection")
    ]
    batch_size: Annotated[
        int | None,
        Field(default=None, ge=1, description="Max messages per dispatch cycle")
    ]
    ttl: Annotated[
        int | None,
        Field(default=300, ge=0, description="Connection TTL in seconds")
    ]
    limit_per_minute: Annotated[
        int | None,
        Field(default=None, ge=0, description="Max emails per minute (0 = unlimited)")
    ]
    limit_per_hour: Annotated[
        int | None,
        Field(default=None, ge=0, description="Max emails per hour (0 = unlimited)")
    ]
    limit_per_day: Annotated[
        int | None,
        Field(default=None, ge=0, description="Max emails per day (0 = unlimited)")
    ]
    limit_behavior: Annotated[
        Literal["defer", "reject"] | None,
        Field(default="defer", description="Behavior when rate limit is hit")
    ]


class AccountUpdate(BaseModel):
    """Payload for updating an existing account.

    All fields except id are optional.
    """

    model_config = ConfigDict(extra="forbid")

    tenant_id: Annotated[
        str | None,
        Field(default=None, min_length=1, max_length=64, description="Parent tenant identifier")
    ]
    host: Annotated[
        str | None,
        Field(default=None, min_length=1, max_length=255, description="SMTP server hostname")
    ]
    port: Annotated[
        int | None,
        Field(default=None, ge=1, le=65535, description="SMTP server port")
    ]
    user: Annotated[
        str | None,
        Field(default=None, max_length=255, description="SMTP username")
    ]
    password: Annotated[
        str | None,
        Field(default=None, max_length=255, description="SMTP password")
    ]
    use_tls: Annotated[
        bool | None,
        Field(default=None, description="Use STARTTLS")
    ]
    use_ssl: Annotated[
        bool | None,
        Field(default=None, description="Use SSL/TLS connection")
    ]
    batch_size: Annotated[
        int | None,
        Field(default=None, ge=1, description="Max messages per dispatch cycle")
    ]
    ttl: Annotated[
        int | None,
        Field(default=None, ge=0, description="Connection TTL in seconds")
    ]
    limit_per_minute: Annotated[
        int | None,
        Field(default=None, ge=0, description="Max emails per minute (0 = unlimited)")
    ]
    limit_per_hour: Annotated[
        int | None,
        Field(default=None, ge=0, description="Max emails per hour (0 = unlimited)")
    ]
    limit_per_day: Annotated[
        int | None,
        Field(default=None, ge=0, description="Max emails per day (0 = unlimited)")
    ]
    limit_behavior: Annotated[
        Literal["defer", "reject"] | None,
        Field(default=None, description="Behavior when rate limit is hit")
    ]


class Account(AccountCreate):
    """Complete account model including timestamps."""

    created_at: Annotated[
        datetime | None,
        Field(default=None, description="Timestamp when account was created")
    ]
    updated_at: Annotated[
        datetime | None,
        Field(default=None, description="Timestamp of last update")
    ]


class FetchMode(str, Enum):
    """Source mode for fetching email attachments.

    Attributes:
        ENDPOINT: Fetch from configured HTTP endpoint with path parameter.
        STORAGE: Fetch from genro-storage volume using storage_path.
        HTTP_URL: Fetch directly from a full HTTP/HTTPS URL.
    """

    ENDPOINT = "endpoint"
    STORAGE = "storage"
    HTTP_URL = "http_url"
    BASE64 = "base64"


class AttachmentPayload(BaseModel):
    """Email attachment specification.

    Attributes:
        filename: Attachment filename (may contain MD5 marker).
        storage_path: Path to fetch content (base64:, volume:, @http, /absolute, relative).
        mime_type: Optional MIME type override.
        fetch_mode: Explicit fetch mode (endpoint, storage, http_url, base64).
            If not specified, mode is determined from storage_path prefix.
        content_md5: MD5 hash for cache lookup. Alternative to embedding
            {MD5:hash} marker in filename.
        auth: Optional authentication override for HTTP requests.
            Uses TenantAuth format. If not specified, uses tenant's client_auth.
    """

    model_config = ConfigDict(extra="forbid")

    filename: Annotated[
        str,
        Field(min_length=1, max_length=255, description="Attachment filename")
    ]
    storage_path: Annotated[
        str,
        Field(min_length=1, description="Storage path (base64:, volume:, @http, /path)")
    ]
    mime_type: Annotated[
        str | None,
        Field(default=None, description="MIME type override")
    ]
    fetch_mode: Annotated[
        FetchMode | None,
        Field(default=None, description="Explicit fetch mode (endpoint, storage, http_url, base64)")
    ]
    content_md5: Annotated[
        str | None,
        Field(default=None, pattern=r"^[a-fA-F0-9]{32}$", description="MD5 hash for cache lookup")
    ]
    auth: Annotated[
        TenantAuth | None,
        Field(default=None, description="Auth override for this attachment")
    ]


class MessageCreate(BaseModel):
    """Payload for creating a new email message.

    Attributes:
        id: Unique message identifier.
        account_id: SMTP account to use for sending.
        from_addr: Sender email address.
        to: List of recipient addresses.
        cc: List of CC addresses.
        bcc: List of BCC addresses.
        reply_to: Reply-To address.
        return_path: Return-Path (envelope sender) address.
        subject: Email subject.
        body: Email body content.
        content_type: Body content type (plain or html).
        message_id: Custom Message-ID header.
        priority: Message priority (0=immediate, 1=high, 2=medium, 3=low).
        deferred_ts: Unix timestamp to defer delivery until.
        attachments: List of attachments.
        headers: Additional email headers.
    """

    model_config = ConfigDict(extra="forbid")

    id: Annotated[
        str,
        Field(min_length=1, max_length=255, description="Unique message identifier")
    ]
    account_id: Annotated[
        str,
        Field(min_length=1, max_length=64, description="SMTP account identifier")
    ]
    from_addr: Annotated[
        str,
        Field(alias="from", min_length=1, description="Sender email address")
    ]
    to: Annotated[
        list[str] | str,
        Field(description="Recipient address(es)")
    ]
    cc: Annotated[
        list[str] | str | None,
        Field(default=None, description="CC address(es)")
    ]
    bcc: Annotated[
        list[str] | str | None,
        Field(default=None, description="BCC address(es)")
    ]
    reply_to: Annotated[
        str | None,
        Field(default=None, description="Reply-To address")
    ]
    return_path: Annotated[
        str | None,
        Field(default=None, description="Return-Path (envelope sender) address")
    ]
    subject: Annotated[
        str,
        Field(min_length=1, description="Email subject")
    ]
    body: Annotated[
        str,
        Field(description="Email body content")
    ]
    content_type: Annotated[
        Literal["plain", "html"],
        Field(default="plain", description="Body content type")
    ]
    message_id: Annotated[
        str | None,
        Field(default=None, description="Custom Message-ID header")
    ]
    priority: Annotated[
        int,
        Field(default=2, ge=0, le=3, description="Priority (0=immediate, 3=low)")
    ]
    deferred_ts: Annotated[
        int | None,
        Field(default=None, ge=0, description="Unix timestamp to defer until")
    ]
    attachments: Annotated[
        list[AttachmentPayload] | None,
        Field(default=None, description="List of attachments")
    ]
    headers: Annotated[
        dict[str, str] | None,
        Field(default=None, description="Additional email headers")
    ]

    @field_validator("to", "cc", "bcc", mode="before")
    @classmethod
    def normalize_recipients(cls, v: list[str] | str | None) -> list[str] | None:
        """Convert string recipients to list."""
        if v is None:
            return None
        if isinstance(v, str):
            return [addr.strip() for addr in v.split(",") if addr.strip()]
        return v


class MessageStatus(str, Enum):
    """Current delivery status of an email message.

    Attributes:
        PENDING: Queued and waiting for delivery attempt.
        DEFERRED: Temporarily delayed (rate limit or soft error).
        SENT: Successfully delivered to SMTP server.
        ERROR: Delivery failed with permanent error.
    """

    PENDING = "pending"
    DEFERRED = "deferred"
    SENT = "sent"
    ERROR = "error"


class Message(BaseModel):
    """Complete message model including status and timestamps.

    Returned by message queries.
    """

    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(description="Message identifier")]
    account_id: Annotated[str, Field(description="SMTP account identifier")]
    tenant_id: Annotated[str | None, Field(default=None, description="Tenant identifier")]
    priority: Annotated[int, Field(description="Message priority")]
    status: Annotated[MessageStatus, Field(description="Delivery status")]
    created_at: Annotated[datetime | None, Field(default=None)]
    deferred_ts: Annotated[int | None, Field(default=None)]
    sent_ts: Annotated[int | None, Field(default=None)]
    error_ts: Annotated[int | None, Field(default=None)]
    error: Annotated[str | None, Field(default=None)]
    reported_ts: Annotated[int | None, Field(default=None)]
    payload: Annotated[dict[str, Any], Field(description="Original message payload")]


# CLI-specific models for formatted output

class TenantListItem(BaseModel):
    """Tenant summary for list display."""

    id: str
    name: str | None
    active: bool
    client_base_url: str | None
    account_count: Annotated[int, Field(default=0)]


class AccountListItem(BaseModel):
    """Account summary for list display."""

    id: str
    tenant_id: str
    host: str
    port: int
    use_tls: bool
    message_count: Annotated[int, Field(default=0)]


class MessageListItem(BaseModel):
    """Message summary for list display."""

    id: str
    account_id: str
    status: MessageStatus
    subject: Annotated[str, Field(default="")]
    created_at: datetime | None


# Helper functions for building tenant URLs

DEFAULT_SYNC_PATH = "/mail-proxy/sync"
DEFAULT_ATTACHMENT_PATH = "/mail-proxy/attachments"


def get_tenant_sync_url(tenant: dict[str, Any]) -> str | None:
    """Build full sync URL from tenant config.

    Args:
        tenant: Tenant configuration dict.

    Returns:
        Full sync URL or None if no base URL configured.
    """
    base_url = tenant.get("client_base_url")
    if not base_url:
        return None
    sync_path = tenant.get("client_sync_path") or DEFAULT_SYNC_PATH
    return f"{base_url.rstrip('/')}{sync_path}"


def get_tenant_attachment_url(tenant: dict[str, Any]) -> str | None:
    """Build full attachment URL from tenant config.

    Args:
        tenant: Tenant configuration dict.

    Returns:
        Full attachment URL or None if no base URL configured.
    """
    base_url = tenant.get("client_base_url")
    if not base_url:
        return None
    attachment_path = tenant.get("client_attachment_path") or DEFAULT_ATTACHMENT_PATH
    return f"{base_url.rstrip('/')}{attachment_path}"
