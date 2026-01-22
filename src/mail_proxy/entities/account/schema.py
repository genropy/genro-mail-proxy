# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Pydantic schemas for SMTP account entity."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class AccountCreate(BaseModel):
    """Payload for creating a new SMTP account.

    Attributes:
        id: Unique account identifier.
        tenant_id: Parent tenant identifier.
        host: SMTP server hostname.
        port: SMTP server port.
        user: SMTP authentication username.
        password: SMTP authentication password.
        use_tls: Whether to use TLS (STARTTLS on 587, implicit on 465).
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
        Field(default=True, description="Use STARTTLS (587) or implicit TLS (465)")
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
    """Payload for updating an existing account. All fields except id are optional."""

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
        Field(default=None, description="Use TLS (STARTTLS on 587, implicit on 465)")
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


class AccountListItem(BaseModel):
    """Account summary for list display."""

    id: str
    tenant_id: str
    host: str
    port: int
    use_tls: bool
    message_count: Annotated[int, Field(default=0)]


__all__ = [
    "Account",
    "AccountCreate",
    "AccountListItem",
    "AccountUpdate",
]
