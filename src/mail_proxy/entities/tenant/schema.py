# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Pydantic schemas for tenant entity."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any

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


class LargeFileAction(str, Enum):
    """Action to take when attachment exceeds size limit.

    Attributes:
        WARN: Log warning but send attachment normally (default).
        REJECT: Reject the message with an error.
        REWRITE: Upload to storage and replace with download link.
    """

    WARN = "warn"
    REJECT = "reject"
    REWRITE = "rewrite"


class TenantLargeFileConfig(BaseModel):
    """Configuration for large file handling via external storage.

    When enabled, attachments exceeding max_size_mb are uploaded to external
    storage (via fsspec) and replaced with download links in the email body.

    Attributes:
        enabled: Whether large file handling is active.
        max_size_mb: Size threshold in MB (attachments larger than this are processed).
        storage_url: fsspec-compatible URL (s3://bucket/path, file:///data, etc.).
        public_base_url: Public URL for download links (required for local filesystem).
        file_ttl_days: Days before uploaded files expire if never downloaded.
        lifespan_after_download_days: Days to keep file after first download.
        action: Behavior when attachment exceeds limit (warn, reject, rewrite).
    """

    model_config = ConfigDict(extra="forbid")

    enabled: Annotated[
        bool,
        Field(default=False, description="Enable large file handling")
    ]
    max_size_mb: Annotated[
        float,
        Field(default=10.0, gt=0, description="Size threshold in MB")
    ]
    storage_url: Annotated[
        str | None,
        Field(default=None, description="fsspec URL (s3://bucket/path, file:///data)")
    ]
    public_base_url: Annotated[
        str | None,
        Field(default=None, description="Public URL for download links")
    ]
    file_ttl_days: Annotated[
        int,
        Field(default=30, ge=1, description="Days before files expire if never downloaded")
    ]
    lifespan_after_download_days: Annotated[
        int | None,
        Field(default=None, ge=1, description="Days to keep after first download")
    ]
    action: Annotated[
        LargeFileAction,
        Field(default=LargeFileAction.WARN, description="Action when limit exceeded")
    ]

    @field_validator("storage_url")
    @classmethod
    def storage_url_required_for_rewrite(cls, v: str | None, info) -> str | None:
        """Validate that storage_url is provided when action is rewrite."""
        if info.data.get("enabled") and info.data.get("action") == LargeFileAction.REWRITE:
            if not v:
                raise ValueError("storage_url is required when action is 'rewrite'")
        return v


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
    large_file_config: Annotated[
        TenantLargeFileConfig | None,
        Field(default=None, description="Large file storage configuration")
    ]
    active: Annotated[
        bool,
        Field(default=True, description="Whether tenant is active")
    ]


class TenantUpdate(BaseModel):
    """Payload for updating an existing tenant. All fields are optional."""

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
    large_file_config: Annotated[
        TenantLargeFileConfig | None,
        Field(default=None, description="Large file storage configuration")
    ]
    active: Annotated[
        bool | None,
        Field(default=None, description="Whether tenant is active")
    ]


class Tenant(TenantCreate):
    """Complete tenant model including timestamps."""

    created_at: Annotated[
        datetime | None,
        Field(default=None, description="Timestamp when tenant was created")
    ]
    updated_at: Annotated[
        datetime | None,
        Field(default=None, description="Timestamp of last update")
    ]


class TenantListItem(BaseModel):
    """Tenant summary for list display."""

    id: str
    name: str | None
    active: bool
    client_base_url: str | None
    account_count: Annotated[int, Field(default=0)]


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


__all__ = [
    "AuthMethod",
    "DEFAULT_ATTACHMENT_PATH",
    "DEFAULT_SYNC_PATH",
    "LargeFileAction",
    "Tenant",
    "TenantAuth",
    "TenantCreate",
    "TenantLargeFileConfig",
    "TenantListItem",
    "TenantRateLimits",
    "TenantSyncAuth",
    "TenantUpdate",
    "get_tenant_attachment_url",
    "get_tenant_sync_url",
]
