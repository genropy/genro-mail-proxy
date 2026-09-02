# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Request and response schemas of the v1 HTTP contract.

The shapes the 0.7.7 transport must answer with, transcribed from the FastAPI
application they were declared in (``api.py``, which Phase 3 removes): same
fields, same defaults, same aliases. Two of them carry behaviour the wire
depends on and no handler may re-invent:

- ``MessagePayload.from_addr`` is aliased ``from`` — the JSON name the Genropy
  client sends — and ``populate_by_name`` keeps the Python name accepted too;
- ``MessageRecord.sent_ts`` reads the database column ``smtp_ts``.

Handlers validate an incoming body by constructing the request model, and
answer with ``model_dump(mode="json", exclude_none=True)``: ``mode="json"``
turns datetimes into the ISO strings v1 sent, and ``exclude_none`` reproduces
the ``response_model_exclude_none=True`` v1 declared on almost every route.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from .entities.message.schema import AttachmentPayload

__all__ = [
    "AccountInfo",
    "AccountPayload",
    "AccountsResponse",
    "AddMessagesResponse",
    "AddTenantResponse",
    "ApiKeyResponse",
    "BasicOkResponse",
    "CleanupMessagesPayload",
    "CleanupMessagesResponse",
    "CommandLogEntry",
    "CommandLogResponse",
    "CommandStatus",
    "DeleteMessagesPayload",
    "DeleteMessagesResponse",
    "EnqueueMessagesPayload",
    "InstanceInfo",
    "InstanceUpdatePayload",
    "MessageEvent",
    "MessagePayload",
    "MessageRecord",
    "MessagesResponse",
    "RejectedMessage",
    "StatusResponse",
    "SuspendResponse",
    "TenantInfo",
    "TenantPayload",
    "TenantSyncStatusEntry",
    "TenantUpdatePayload",
    "TenantsResponse",
    "TenantsSyncStatusResponse",
]


def _coerce_datetime(value: Any) -> datetime | None:
    """Accept a datetime, an ISO string, or None — anything else is an error."""
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError(f"Cannot convert {type(value)} to datetime")


FlexibleDatetime = Annotated[datetime | None, BeforeValidator(_coerce_datetime)]


class CommandStatus(BaseModel):
    """Base shape shared by most answers: the outcome and its error."""

    ok: bool
    error: str | None = None


class BasicOkResponse(CommandStatus):
    """The bare outcome, for the routes that report nothing else."""


class StatusResponse(CommandStatus):
    """``GET /status`` — the outcome plus whether dispatch is running."""

    active: bool


class ApiKeyResponse(CommandStatus):
    """``POST /tenant/{id}/api-key`` — the generated key, shown once."""

    api_key: str


class AddTenantResponse(CommandStatus):
    """``POST /tenant`` — the key a NEW tenant receives, absent on an upsert."""

    api_key: str | None = None


class SuspendResponse(CommandStatus):
    """``POST /commands/suspend`` and ``/activate`` — the suspension state."""

    tenant_id: str
    batch_code: str | None = None
    suspended_batches: list[str] = Field(default_factory=list)
    pending_messages: int = 0


class AccountPayload(BaseModel):
    """An SMTP account as ``POST /account`` accepts it, PEC fields included."""

    id: str
    tenant_id: str | None = None
    host: str
    port: int
    user: str | None = None
    password: str | None = None
    ttl: int | None = 300
    limit_per_minute: int | None = None
    limit_per_hour: int | None = None
    limit_per_day: int | None = None
    limit_behavior: str | None = "defer"
    use_tls: bool | None = None
    batch_size: int | None = None
    is_pec_account: bool | None = None
    imap_host: str | None = None
    imap_port: int | None = None
    imap_user: str | None = None
    imap_password: str | None = None
    imap_ssl: bool | None = None


class AccountInfo(BaseModel):
    """A stored account as ``GET /accounts`` returns it — never the password."""

    id: str
    tenant_id: str
    host: str
    port: int
    user: str | None = None
    ttl: int
    limit_per_minute: int | None = None
    limit_per_hour: int | None = None
    limit_per_day: int | None = None
    limit_behavior: str | None = None
    use_tls: bool | None = None
    batch_size: int | None = None
    created_at: FlexibleDatetime = None
    updated_at: FlexibleDatetime = None
    is_pec_account: bool | None = None
    imap_host: str | None = None
    imap_port: int | None = None


class AccountsResponse(CommandStatus):
    """``GET /accounts`` — the accounts of one tenant."""

    accounts: list[AccountInfo]


class MessagePayload(BaseModel):
    """One message as ``POST /commands/add-messages`` accepts it."""

    model_config = ConfigDict(populate_by_name=True)

    id: str
    tenant_id: str
    account_id: str
    from_addr: str = Field(alias="from")
    to: list[str] | str
    cc: list[str] | str | None = None
    bcc: list[str] | str | None = None
    reply_to: str | None = None
    return_path: str | None = None
    subject: str = Field(min_length=1)
    body: str = ""
    content_type: str | None = "plain"
    headers: dict[str, Any] | None = None
    message_id: str | None = None
    attachments: list[AttachmentPayload] | None = None
    priority: int | Literal["immediate", "high", "medium", "low"] | None = None
    deferred_ts: int | None = None
    batch_code: str | None = Field(default=None, max_length=64)


class EnqueueMessagesPayload(BaseModel):
    """The batch ``POST /commands/add-messages`` accepts."""

    messages: list[MessagePayload]
    default_priority: int | Literal["immediate", "high", "medium", "low"] | None = None


class RejectedMessage(BaseModel):
    """One message the enqueue refused, with the reason."""

    id: str | None = None
    reason: str


class AddMessagesResponse(CommandStatus):
    """``POST /commands/add-messages`` — what was queued, what was refused."""

    queued: int = 0
    rejected: list[RejectedMessage] = Field(default_factory=list)


class MessageEvent(BaseModel):
    """One entry of a message's history."""

    event_id: int
    event_type: str
    event_ts: int
    description: str | None = None
    metadata: dict[str, Any] | None = None
    reported_ts: int | None = None


class MessageRecord(BaseModel):
    """A tracked message as ``GET /messages`` returns it."""

    pk: str
    id: str
    tenant_id: str
    tenant_name: str | None = None
    account_id: str
    priority: int
    batch_code: str | None = None
    deferred_ts: int | None = None
    sent_ts: int | None = Field(default=None, validation_alias="smtp_ts")
    error_ts: int | None = None
    error: str | None = None
    reported_ts: int | None = None
    bounce_type: str | None = None
    bounce_code: str | None = None
    bounce_reason: str | None = None
    bounce_ts: FlexibleDatetime = None
    created_at: FlexibleDatetime = None
    updated_at: FlexibleDatetime = None
    message: dict[str, Any]
    is_pec: bool | None = None
    history: list[MessageEvent] | None = None


class MessagesResponse(CommandStatus):
    """``GET /messages`` — the queue of one tenant."""

    messages: list[MessageRecord]


class DeleteMessagesPayload(BaseModel):
    """The ids ``POST /commands/delete-messages`` accepts."""

    ids: list[str] = Field(default_factory=list)


class DeleteMessagesResponse(CommandStatus):
    """``POST /commands/delete-messages`` — removed, missing, out of scope."""

    removed: int
    not_found: list[str] | None = None
    unauthorized: list[str] | None = None


class CleanupMessagesPayload(BaseModel):
    """The retention override ``POST /commands/cleanup-messages`` accepts."""

    older_than_seconds: int | None = None


class CleanupMessagesResponse(CommandStatus):
    """``POST /commands/cleanup-messages`` — how many rows the sweep removed."""

    removed: int


class TenantPayload(BaseModel):
    """A tenant as ``POST /tenant`` accepts it."""

    id: str
    name: str | None = None
    client_auth: dict[str, Any] | None = None
    client_base_url: str | None = None
    client_sync_path: str | None = None
    client_attachment_path: str | None = None
    rate_limits: dict[str, Any] | None = None
    large_file_config: dict[str, Any] | None = None
    active: bool = True


class TenantUpdatePayload(BaseModel):
    """A partial tenant update — every field optional."""

    name: str | None = None
    client_auth: dict[str, Any] | None = None
    client_base_url: str | None = None
    client_sync_path: str | None = None
    client_attachment_path: str | None = None
    rate_limits: dict[str, Any] | None = None
    large_file_config: dict[str, Any] | None = None
    active: bool | None = None


class TenantInfo(BaseModel):
    """A stored tenant as ``GET /tenant/{id}`` and ``GET /tenants`` return it."""

    id: str
    name: str | None = None
    client_auth: dict[str, Any] | None = None
    client_base_url: str | None = None
    client_sync_path: str | None = None
    client_attachment_path: str | None = None
    rate_limits: dict[str, Any] | None = None
    large_file_config: dict[str, Any] | None = None
    active: bool = True
    created_at: FlexibleDatetime = None
    updated_at: FlexibleDatetime = None


class TenantsResponse(CommandStatus):
    """``GET /tenants`` — every tenant, or only the active ones."""

    tenants: list[TenantInfo]


class TenantSyncStatusEntry(BaseModel):
    """The sync flags of one tenant."""

    id: str
    name: str | None = None
    active: bool = True
    client_base_url: str | None = None
    last_sync_ts: float | None = None
    next_sync_due: bool = False
    in_dnd: bool = False


class TenantsSyncStatusResponse(CommandStatus):
    """``GET /tenants/sync-status`` — the sync flags of every tenant."""

    tenants: list[TenantSyncStatusEntry]
    sync_interval_seconds: int = 300


class CommandLogEntry(BaseModel):
    """One logged command of the audit trail."""

    id: int
    command_ts: int
    endpoint: str
    tenant_id: str | None = None
    payload: dict[str, Any]
    response_status: int | None = None


class CommandLogResponse(CommandStatus):
    """``GET /command-log`` — the page of the trail, and its own length."""

    commands: list[CommandLogEntry]
    total: int


class InstanceInfo(BaseModel):
    """The singleton instance row as ``GET /instance`` returns it."""

    id: int = 1
    name: str | None = None
    api_token: str | None = None
    bounce_enabled: bool = False
    bounce_imap_host: str | None = None
    bounce_imap_port: int | None = 993
    bounce_imap_user: str | None = None
    bounce_imap_folder: str | None = "INBOX"
    bounce_imap_ssl: bool = True
    bounce_poll_interval: int = 60
    bounce_return_path: str | None = None
    bounce_last_uid: int | None = None
    bounce_last_sync: FlexibleDatetime = None
    bounce_uidvalidity: int | None = None
    created_at: FlexibleDatetime = None
    updated_at: FlexibleDatetime = None


class InstanceUpdatePayload(BaseModel):
    """A partial instance update — every field optional."""

    name: str | None = None
    api_token: str | None = None
    bounce_enabled: bool | None = None
    bounce_imap_host: str | None = None
    bounce_imap_port: int | None = None
    bounce_imap_user: str | None = None
    bounce_imap_password: str | None = None
    bounce_imap_folder: str | None = None
    bounce_imap_ssl: bool | None = None
    bounce_poll_interval: int | None = None
    bounce_return_path: str | None = None
