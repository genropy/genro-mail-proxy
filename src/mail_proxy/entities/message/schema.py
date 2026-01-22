# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Pydantic schemas for message entity."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..tenant.schema import TenantAuth


class FetchMode(str, Enum):
    """Source mode for fetching email attachments.

    Attributes:
        ENDPOINT: Fetch from configured HTTP endpoint with path parameter.
        HTTP_URL: Fetch directly from a full HTTP/HTTPS URL.
        BASE64: Inline base64-encoded content.
        FILESYSTEM: Fetch from local filesystem path.
    """

    ENDPOINT = "endpoint"
    HTTP_URL = "http_url"
    BASE64 = "base64"
    FILESYSTEM = "filesystem"


class AttachmentPayload(BaseModel):
    """Email attachment specification.

    Attributes:
        filename: Attachment filename (may contain MD5 marker).
        storage_path: Content location. Format depends on fetch_mode:
            - endpoint: query params (e.g., ``doc_id=123``)
            - http_url: full URL (e.g., ``https://files.myserver.local/file.pdf``)
            - base64: base64-encoded content
            - filesystem: absolute path (e.g., ``/var/attachments/file.pdf``)
        mime_type: Optional MIME type override.
        fetch_mode: Explicit fetch mode (endpoint, http_url, base64, filesystem).
            Required for determining how to retrieve the content.
        content_md5: MD5 hash for cache lookup. Alternative to embedding
            ``{MD5:hash}`` marker in filename.
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
        Field(min_length=1, description="Storage path (base64:, @http, /path)")
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
    """Complete message model including status and timestamps."""

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


class MessageListItem(BaseModel):
    """Message summary for list display."""

    id: str
    account_id: str
    status: MessageStatus
    subject: Annotated[str, Field(default="")]
    created_at: datetime | None


__all__ = [
    "AttachmentPayload",
    "FetchMode",
    "Message",
    "MessageCreate",
    "MessageListItem",
    "MessageStatus",
]
