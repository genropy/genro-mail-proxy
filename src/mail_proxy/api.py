# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""FastAPI application factory and HTTP schemas for the async mail service.

This module provides the REST API interface for the asynchronous mail dispatcher
service. It includes:

- Pydantic models defining request/response schemas for all endpoints
- A factory function to create and configure the FastAPI application
- Authentication via API token in the X-API-Token header
- Endpoints for message management, SMTP account configuration, and monitoring

The API supports operations including:
- Adding and managing messages in the send queue
- Configuring SMTP accounts with rate limiting
- Health checks and Prometheus metrics exposure
- Manual control of the scheduler (suspend/activate/run-now)

Example:
    Creating and running the API application::

        from mail_proxy.core import MailProxy
        from mail_proxy.api import create_app

        core = MailProxy(db_path="/data/mail.db")
        app = create_app(core, api_token="secret-token")

        # Run with uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
"""

import logging
import secrets
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field


def _coerce_datetime(value: Any) -> datetime | None:
    """Coerce string to datetime or pass through datetime objects."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        # Parse ISO format string
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise ValueError(f"Cannot convert {type(value)} to datetime")


FlexibleDatetime = Annotated[datetime | None, BeforeValidator(_coerce_datetime)]

from .core import MailProxy
from .entities.message.schema import AttachmentPayload

logger = logging.getLogger(__name__)

app = FastAPI(title="Async Mail Service")
service: MailProxy | None = None
API_TOKEN_HEADER_NAME = "X-API-Token"
api_key_scheme = APIKeyHeader(name=API_TOKEN_HEADER_NAME, auto_error=False)
app.state.api_token = None


async def verify_tenant_token(tenant_id: str | None, api_token: str | None, global_token: str | None) -> None:
    """Verify API token for a request, with tenant-specific key support.

    Authentication logic:
    1. Look up token in tenants table (by hash)
    2. If found → token belongs to a tenant, verify tenant_id matches
    3. If not found → verify against global token

    Args:
        tenant_id: The tenant ID from the request (may be None for some endpoints).
        api_token: The token from X-API-Token header.
        global_token: The configured global API token.

    Raises:
        HTTPException: 401 if token is invalid or tenant_id mismatch.
    """
    if not api_token:
        if global_token is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")
        return  # No token configured, allow access

    # Look up token in tenants table
    if service and getattr(service, "db", None):
        token_tenant = await service.db.tenants.get_tenant_by_token(api_token)
        if token_tenant:
            # Token belongs to a tenant - verify tenant_id matches
            if tenant_id and token_tenant["id"] != tenant_id:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token not authorized for this tenant")
            return  # Valid tenant token

    # Token not found in tenants - verify against global token
    if global_token is None:
        return  # No global token configured, allow access
    if not secrets.compare_digest(api_token, global_token):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")


async def require_token(
    request: Request,
    api_token: str | None = Depends(api_key_scheme)
) -> None:
    """Validate the API token carried in the ``X-API-Token`` header.

    Accepts either:
    - The global API token (GMP_API_TOKEN)
    - A valid tenant-specific API key

    For endpoints with tenant_id, verify_tenant_token() performs additional
    scope verification to ensure the token matches the requested tenant.
    """
    # Store token in request state for later tenant-aware verification
    request.state.api_token = api_token

    expected = getattr(request.app.state, "api_token", None)

    # No token provided
    if not api_token:
        if expected is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")
        return  # No global token configured, allow access

    # Check global token first
    if expected is not None and secrets.compare_digest(api_token, expected):
        return  # Global token matches

    # Check tenant token
    if service and getattr(service, "db", None):
        token_tenant = await service.db.tenants.get_tenant_by_token(api_token)
        if token_tenant:
            # Valid tenant token - store tenant info for later scope verification
            request.state.token_tenant_id = token_tenant["id"]
            return

    # Token didn't match global or any tenant
    if expected is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")


auth_dependency = Depends(require_token)

class AccountPayload(BaseModel):
    """SMTP account definition used when adding or updating accounts.

    Attributes:
        id: Unique account identifier.
        tenant_id: Parent tenant identifier. If None, account is not associated
            with any tenant (standalone mode for single-tenant deployments).
        host: SMTP server hostname.
        port: SMTP server port.
        user: SMTP username for authentication.
        password: SMTP password for authentication.
        ttl: Connection TTL in seconds (default: 300).
        limit_per_minute: Max emails per minute (None = unlimited).
        limit_per_hour: Max emails per hour (None = unlimited).
        limit_per_day: Max emails per day (None = unlimited).
        limit_behavior: Behavior when rate limit hit ("defer" or "reject").
        use_tls: Use STARTTLS (port 587) or implicit TLS (port 465).
        batch_size: Max messages per dispatch cycle.
    """
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


class CommandStatus(BaseModel):
    """Base schema shared by most responses produced by the service."""
    ok: bool
    error: str | None = None


class BasicOkResponse(CommandStatus):
    pass


class StatusResponse(CommandStatus):
    """Response from /status endpoint."""

    active: bool = Field(..., description="Whether the service is actively processing messages")


class SuspendResponse(CommandStatus):
    """Response from /suspend and /activate endpoints."""

    tenant_id: str = Field(..., description="Tenant ID")
    batch_code: str | None = Field(default=None, description="Batch code (None if all)")
    suspended_batches: list[str] = Field(default_factory=list, description="Currently suspended batches")
    pending_messages: int = Field(default=0, description="Count of pending messages")


# AttachmentPayload is imported from models.py for consistency and proper validation


class MessagePayload(BaseModel):
    """Payload accepted by the ``addMessages`` command.

    Attributes:
        id: Unique message identifier (client-provided).
        account_id: SMTP account to use for sending. If None, uses the instance's
            default SMTP settings (default_host, default_port, etc.).
        from_addr: Sender email address (aliased as "from" in JSON).
        to: Recipient address(es), string or list.
        cc: CC address(es), string or list.
        bcc: BCC address(es), string or list.
        reply_to: Reply-To address.
        return_path: Return-Path (envelope sender) address.
        subject: Email subject.
        body: Email body content.
        content_type: Body content type ("plain" or "html").
        headers: Additional custom email headers.
        message_id: Custom Message-ID header.
        attachments: List of attachment specifications.
        priority: Message priority (0-3 or "immediate"/"high"/"medium"/"low").
        deferred_ts: Unix timestamp to defer delivery until.
        batch_code: Optional batch/campaign identifier for grouping messages.
    """
    model_config = ConfigDict(populate_by_name=True)
    id: str
    account_id: str | None = None
    from_addr: str = Field(alias="from")
    to: list[str] | str
    cc: list[str] | str | None = None
    bcc: list[str] | str | None = None
    reply_to: str | None = None
    return_path: str | None = None
    subject: str = Field(min_length=1)
    body: str = Field(default="")
    content_type: str | None = Field(default="plain")
    headers: dict[str, Any] | None = None
    message_id: str | None = None
    attachments: list[AttachmentPayload] | None = None
    priority: int | Literal["immediate", "high", "medium", "low"] | None = None
    deferred_ts: int | None = None
    batch_code: str | None = Field(default=None, max_length=64)


class AccountInfo(BaseModel):
    """Stored SMTP account as returned by ``listAccounts``."""
    id: str
    tenant_id: str | None = None
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


class AccountsResponse(CommandStatus):
    accounts: list[AccountInfo]


class EnqueueMessagesPayload(BaseModel):
    """Queue of messages used by ``addMessages``."""
    messages: list[MessagePayload]
    default_priority: int | Literal["immediate", "high", "medium", "low"] | None = None


class RejectedMessage(BaseModel):
    """Rejected message entry."""
    id: str | None = None
    reason: str


class AddMessagesResponse(CommandStatus):
    """Response returned by the ``addMessages`` command."""
    queued: int = 0
    rejected: list[RejectedMessage] = Field(default_factory=list)


class MessageRecord(BaseModel):
    """Full representation of a message tracked by the dispatcher."""
    id: str
    priority: int
    account_id: str | None = None
    batch_code: str | None = None
    deferred_ts: int | None = None
    sent_ts: int | None = None
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


class MessagesResponse(CommandStatus):
    messages: list[MessageRecord]


class DeleteMessagesPayload(BaseModel):
    """Request payload for deleting messages."""
    ids: list[str] = Field(default_factory=list)


class DeleteMessagesResponse(CommandStatus):
    removed: int
    not_found: list[str] | None = None
    unauthorized: list[str] | None = None


class CleanupMessagesPayload(BaseModel):
    """Request payload for manual cleanup of reported messages."""
    older_than_seconds: int | None = None


class CleanupMessagesResponse(CommandStatus):
    """Response from cleanup operation."""
    removed: int


class TenantPayload(BaseModel):
    """Tenant configuration payload."""
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
    """Tenant update payload - all fields optional."""
    name: str | None = None
    client_auth: dict[str, Any] | None = None
    client_base_url: str | None = None
    client_sync_path: str | None = None
    client_attachment_path: str | None = None
    rate_limits: dict[str, Any] | None = None
    large_file_config: dict[str, Any] | None = None
    active: bool | None = None


class TenantInfo(BaseModel):
    """Stored tenant as returned by listTenants."""
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
    """Response with list of tenants."""
    tenants: list[TenantInfo]


class ApiKeyResponse(CommandStatus):
    """Response with generated API key (shown once)."""
    api_key: str


class InstanceInfo(BaseModel):
    """Instance configuration as returned by getInstance."""
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
    """Instance update payload - all fields optional."""
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


def create_app(
    svc: MailProxy,
    api_token: str | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager] | None = None,
    tenant_tokens_enabled: bool = False,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ----------
    svc:
        Instance of :class:`mail_proxy.core.MailProxy` that
        implements the business logic for each command.
    api_token:
        Optional secret used to protect every endpoint. When provided, the
        ``X-API-Token`` header must match this value on every request.
        Ignored when ``tenant_tokens_enabled=True``.
    lifespan:
        Optional lifespan context manager for startup/shutdown events.
    tenant_tokens_enabled:
        When True, enables per-tenant API keys instead of a global token.
        Each tenant must have an API key created via the CLI or API.
        The tenant_id is automatically extracted from the token.

    Returns
    -------
    FastAPI
        A configured application ready to be served by Uvicorn or any ASGI
        server.
    """
    global service
    service = svc

    # Use custom lifespan if provided, otherwise use the global app
    api = FastAPI(title="Async Mail Service", lifespan=lifespan) if lifespan is not None else app

    api.state.api_token = api_token
    api.state.tenant_tokens_enabled = tenant_tokens_enabled
    router = APIRouter(prefix="/commands", tags=["commands"], dependencies=[auth_dependency])

    @api.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Handle FastAPI request validation errors with detailed logging.

        Args:
            request: The incoming HTTP request that failed validation.
            exc: The validation exception containing error details.

        Returns:
            JSONResponse with status 422 and validation error details.
        """
        body = await request.body()
        logger.error(f"Validation error on {request.method} {request.url.path}")
        logger.error(f"Request body: {body.decode('utf-8', errors='replace')}")
        logger.error(f"Validation errors: {exc.errors()}")
        return JSONResponse(
            status_code=422,
            content={"detail": exc.errors()}
        )

    @api.get("/health")
    async def health():
        """Health check endpoint for container orchestration and load balancers.

        This endpoint does not require authentication and is intended for
        Kubernetes liveness/readiness probes or similar monitoring tools.

        Returns:
            dict: Simple status object with ``{"status": "ok"}``.
        """
        return {"status": "ok"}

    @api.get("/status", response_model=StatusResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def status():
        """Return authenticated service status.

        Unlike ``/health``, this endpoint requires authentication and confirms
        that the API token is valid. Also returns whether the service is actively
        processing messages.

        Returns:
            StatusResponse: Status response with ``ok=True`` and ``active`` state.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        return StatusResponse(ok=True, active=service._active)

    @router.post("/run-now", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def run_now():
        """Trigger an immediate dispatch cycle without waiting for the scheduler.

        Wakes up the dispatcher to process all pending messages across all tenants.
        This is a simple "wake up" signal - the dispatcher always processes everything.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True`` after the cycle completes.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("run now", {})
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return BasicOkResponse.model_validate(result)

    @router.post("/suspend", response_model=SuspendResponse, response_model_exclude_none=True)
    async def suspend(tenant_id: str, batch_code: str | None = None):
        """Suspend message sending for a tenant.

        Suspends all messages for the tenant, or only messages with the specified
        batch_code. Other tenants continue sending normally.

        Use case: Stop sending a newsletter campaign with incorrect content,
        then re-submit corrected messages with same IDs to overwrite, then activate.

        Args:
            tenant_id: The tenant to suspend.
            batch_code: Optional batch code. If None, suspends all sending for tenant.

        Returns:
            SuspendResponse: Confirmation with suspended batches list and pending count.

        Raises:
            HTTPException: 400 if tenant_id missing, 500 if service not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("suspend", {
            "tenant_id": tenant_id,
            "batch_code": batch_code,
        })
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return SuspendResponse.model_validate(result)

    @router.post("/activate", response_model=SuspendResponse, response_model_exclude_none=True)
    async def activate(tenant_id: str, batch_code: str | None = None):
        """Resume message sending for a tenant.

        Resumes sending for all messages of the tenant, or only messages with the
        specified batch_code. If batch_code is None, clears all suspensions.

        Args:
            tenant_id: The tenant to activate.
            batch_code: Optional batch code. If None, clears all suspensions.

        Returns:
            SuspendResponse: Confirmation with remaining suspended batches.

        Raises:
            HTTPException: 400 if tenant_id missing or cannot activate single batch
                from full suspension, 500 if service not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("activate", {
            "tenant_id": tenant_id,
            "batch_code": batch_code,
        })
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return SuspendResponse.model_validate(result)

    @router.post("/add-messages", response_model=AddMessagesResponse, response_model_exclude_none=True)
    async def add_messages(payload: EnqueueMessagesPayload):
        """Enqueue one or more email messages for delivery.

        Messages are validated and added to the dispatch queue. Invalid messages
        are rejected with detailed error information while valid ones proceed.

        Args:
            payload: Request body containing the list of messages and optional
                default priority.

        Returns:
            AddMessagesResponse: Result with count of queued messages and list
                of rejected messages with reasons.

        Raises:
            HTTPException: 400 if all messages are rejected or validation fails.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        serialized: list[dict[str, Any]] = []
        for msg in payload.messages:
            data = msg.model_dump(by_alias=True, exclude_none=True)
            if msg.attachments is not None:
                data["attachments"] = [att.model_dump(exclude_none=True) for att in msg.attachments]
            serialized.append(data)
        data = {"messages": serialized}
        if payload.default_priority is not None:
            data["default_priority"] = payload.default_priority
        result = await service.handle_command("addMessages", data)
        if not isinstance(result, dict) or result.get("ok") is not True:
            detail = {"error": result.get("error"), "rejected": result.get("rejected")}
            logger.error(f"add-messages failed: {detail}")
            logger.error(f"Request payload: {payload.model_dump()}")
            raise HTTPException(status_code=400, detail=detail)
        return AddMessagesResponse.model_validate(result)

    @router.post("/delete-messages", response_model=DeleteMessagesResponse, response_model_exclude_none=True)
    async def delete_messages(request: Request, tenant_id: str, payload: DeleteMessagesPayload):
        """Remove messages from the queue by their IDs.

        Deletes specified messages from both the queue and tracking tables.
        Messages already sent or in transit may not be cancellable.
        Only messages belonging to the specified tenant will be deleted.

        Args:
            tenant_id: Tenant identifier (required for security isolation).
            payload: Request body containing list of message IDs to remove.

        Returns:
            DeleteMessagesResponse: Count of removed messages, IDs not found,
                and IDs that were unauthorized (belong to other tenant).

        Raises:
            HTTPException: 400 if tenant_id is missing.
            HTTPException: 401 if API token is invalid.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        command_data = {"tenant_id": tenant_id, "ids": payload.ids}
        result = await service.handle_command("deleteMessages", command_data)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return DeleteMessagesResponse.model_validate(result)

    @router.post("/cleanup-messages", response_model=CleanupMessagesResponse, response_model_exclude_none=True)
    async def cleanup_messages(request: Request, tenant_id: str, payload: CleanupMessagesPayload):
        """Manually trigger cleanup of reported messages older than retention period.

        Only cleans up messages belonging to the specified tenant.
        Optionally specify older_than_seconds to override the retention period.

        Args:
            tenant_id: Tenant identifier (required for security isolation).
            payload: Request body with optional older_than_seconds override.

        Raises:
            HTTPException: 400 if tenant_id is missing.
            HTTPException: 401 if API token is invalid.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        command_data: dict[str, str | int] = {"tenant_id": tenant_id}
        if payload.older_than_seconds is not None:
            command_data["older_than_seconds"] = payload.older_than_seconds
        result = await service.handle_command("cleanupMessages", command_data)
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return CleanupMessagesResponse.model_validate(result)

    @api.post("/account", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def add_account(request: Request, acc: AccountPayload):
        """Register or update an SMTP account configuration.

        Creates a new SMTP account or updates an existing one with the same ID.
        Account settings include host, port, credentials, TLS options, and rate limits.

        Args:
            request: The HTTP request (for token verification).
            acc: SMTP account configuration including connection details and limits.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 401 if token is not authorized for tenant.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        # Verify tenant scope for tenant-specific tokens
        await verify_tenant_token(acc.tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        result = await service.handle_command("addAccount", acc.model_dump())
        return BasicOkResponse.model_validate(result)

    @api.get("/accounts", response_model=AccountsResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def list_accounts(request: Request, tenant_id: str):
        """Retrieve SMTP accounts for a specific tenant.

        Returns the list of registered SMTP accounts with their configuration
        for the specified tenant, excluding sensitive data like passwords.

        Args:
            tenant_id: Tenant identifier (required for security isolation).

        Returns:
            AccountsResponse: List of account configurations with ``ok=True``.

        Raises:
            HTTPException: 400 if tenant_id is missing.
            HTTPException: 401 if API token is invalid.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        result = await service.handle_command("listAccounts", {"tenant_id": tenant_id})
        return AccountsResponse.model_validate(result)

    @api.delete("/account/{account_id}", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def delete_account(request: Request, account_id: str, tenant_id: str):
        """Delete an SMTP account by its ID.

        Removes the account configuration and cleans up associated scheduler state.
        Messages assigned to this account will fail delivery.

        Args:
            account_id: Unique identifier of the account to delete.
            tenant_id: Tenant ID (required for security isolation - account must belong to this tenant).

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 400 if tenant_id is missing or account doesn't belong to tenant.
            HTTPException: 401 if API token is invalid.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        result = await service.handle_command("deleteAccount", {"id": account_id, "tenant_id": tenant_id})
        if not result.get("ok"):
            raise HTTPException(400, result.get("error", "Unknown error"))
        return BasicOkResponse.model_validate(result)

    @api.get("/messages", response_model=MessagesResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def all_messages(request: Request, tenant_id: str, active_only: bool = False):
        """List messages for a specific tenant.

        Returns message records including payload, status timestamps,
        and error information for debugging and monitoring purposes.

        Args:
            tenant_id: Tenant identifier (required for security isolation).
            active_only: If True, returns only messages pending delivery.

        Returns:
            MessagesResponse: List of message records with ``ok=True``.

        Raises:
            HTTPException: 400 if tenant_id is missing.
            HTTPException: 401 if API token is invalid.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        if not tenant_id:
            raise HTTPException(400, "tenant_id is required")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        result = await service.handle_command("listMessages", {
            "tenant_id": tenant_id,
            "active_only": active_only
        })
        return MessagesResponse.model_validate(result)

    @api.get("/metrics", dependencies=[auth_dependency])
    async def metrics():
        """Export Prometheus metrics in text exposition format.

        Provides operational metrics including message counts, delivery latencies,
        error rates, and queue depths for monitoring and alerting.

        Returns:
            Response: Plain text response in Prometheus exposition format.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        return Response(content=service.metrics.generate_latest(), media_type="text/plain; version=0.0.4")

    # Tenant endpoints
    @api.post("/tenant", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def add_tenant(payload: TenantPayload):
        """Register or update a tenant configuration.

        Creates a new tenant or updates an existing one. Tenants provide
        multi-tenancy support with isolated message queues and SMTP accounts.

        Args:
            payload: Tenant configuration including client sync settings and rate limits.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("addTenant", payload.model_dump())
        return BasicOkResponse.model_validate(result)

    @api.get("/tenants", response_model=TenantsResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def list_tenants(active_only: bool = False):
        """Retrieve all registered tenants.

        Args:
            active_only: If True, returns only active tenants. Defaults to False.

        Returns:
            TenantsResponse: List of tenant configurations with ``ok=True``.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("listTenants", {"active_only": active_only})
        return TenantsResponse.model_validate(result)

    @api.get("/tenant/{tenant_id}", response_model=TenantInfo, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def get_tenant(request: Request, tenant_id: str):
        """Retrieve a specific tenant configuration.

        Args:
            tenant_id: Unique identifier of the tenant.

        Returns:
            TenantInfo: Complete tenant configuration.

        Raises:
            HTTPException: 401 if API token is invalid.
            HTTPException: 404 if the tenant is not found.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        result = await service.handle_command("getTenant", {"id": tenant_id})
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        # Remove 'ok' key before returning as TenantInfo
        result.pop("ok", None)
        return TenantInfo(**result)

    @api.put("/tenant/{tenant_id}", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def update_tenant(request: Request, tenant_id: str, payload: TenantUpdatePayload):
        """Update an existing tenant's configuration.

        Applies partial updates to the tenant. Only provided fields are updated;
        omitted fields retain their current values.

        Args:
            tenant_id: Unique identifier of the tenant to update.
            payload: Fields to update (all optional).

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 401 if API token is invalid.
            HTTPException: 404 if the tenant is not found.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        update_data = payload.model_dump(exclude_none=True)
        update_data["id"] = tenant_id
        result = await service.handle_command("updateTenant", update_data)
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return BasicOkResponse.model_validate(result)

    @api.delete("/tenant/{tenant_id}", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def delete_tenant(request: Request, tenant_id: str):
        """Delete a tenant and all associated resources.

        Removes the tenant along with all its SMTP accounts and queued messages.
        This operation is irreversible.

        Args:
            tenant_id: Unique identifier of the tenant to delete.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 401 if API token is invalid.
            HTTPException: 404 if the tenant is not found.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        result = await service.handle_command("deleteTenant", {"id": tenant_id})
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return BasicOkResponse.model_validate(result)

    @api.post("/tenant/{tenant_id}/api-key", response_model=ApiKeyResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def create_tenant_api_key(request: Request, tenant_id: str):
        """Generate a new API key for a tenant.

        Creates a new API key for the specified tenant. The key is returned
        only once and should be stored securely by the client. Subsequent
        calls will generate a new key, invalidating the previous one.

        Args:
            tenant_id: Unique identifier of the tenant.

        Returns:
            ApiKeyResponse: Contains the generated API key (show once).

        Raises:
            HTTPException: 401 if API token is invalid.
            HTTPException: 404 if the tenant is not found.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        api_key = await service.db.tenants.create_api_key(tenant_id)
        if not api_key:
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return ApiKeyResponse(ok=True, api_key=api_key)

    @api.delete("/tenant/{tenant_id}/api-key", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def revoke_tenant_api_key(request: Request, tenant_id: str):
        """Revoke the API key for a tenant.

        Invalidates the current API key for the tenant. After revocation,
        the tenant must use the global API token or generate a new key.

        Args:
            tenant_id: Unique identifier of the tenant.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 401 if API token is invalid.
            HTTPException: 404 if the tenant is not found.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        await verify_tenant_token(tenant_id, getattr(request.state, "api_token", None), getattr(request.app.state, "api_token", None))
        revoked = await service.db.tenants.revoke_api_key(tenant_id)
        if not revoked:
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return BasicOkResponse(ok=True)

    # Instance configuration endpoints
    @api.get("/instance", response_model=InstanceInfo, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def get_instance():
        """Retrieve instance configuration.

        Returns the singleton instance configuration including general settings
        and bounce detection configuration.

        Returns:
            InstanceInfo: Instance configuration.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("getInstance", {})
        if not result.get("ok"):
            # Instance doesn't exist yet, return defaults
            return InstanceInfo()
        result.pop("ok", None)
        # Convert int fields to bool for response
        if "bounce_enabled" in result:
            result["bounce_enabled"] = bool(result["bounce_enabled"])
        if "bounce_imap_ssl" in result:
            result["bounce_imap_ssl"] = bool(result["bounce_imap_ssl"])
        # Remove password from response for security
        result.pop("bounce_imap_password", None)
        return InstanceInfo(**result)

    @api.put("/instance", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def update_instance(payload: InstanceUpdatePayload):
        """Update instance configuration.

        Updates the singleton instance configuration. Only provided fields are
        updated; omitted fields retain their current values.

        Args:
            payload: Fields to update (all optional).

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        update_data = payload.model_dump(exclude_none=True)
        # Convert boolean fields to int for database
        if "bounce_enabled" in update_data:
            update_data["bounce_enabled"] = 1 if update_data["bounce_enabled"] else 0
        if "bounce_imap_ssl" in update_data:
            update_data["bounce_imap_ssl"] = 1 if update_data["bounce_imap_ssl"] else 0
        result = await service.handle_command("updateInstance", update_data)
        return BasicOkResponse.model_validate(result)

    @api.post("/instance/reload-bounce", response_model=BasicOkResponse, dependencies=[auth_dependency])
    async def reload_bounce_config():
        """Reload bounce detection configuration from database.

        Call this after updating bounce settings via PUT /instance to apply
        changes without restarting the server. This will stop any existing
        BounceReceiver and start a new one with the updated configuration.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")

        # Get updated config from DB
        bounce_config = await service.db.instance.get_bounce_config()

        if not bounce_config.get("enabled"):
            # Stop existing bounce receiver if any
            if hasattr(service, "_bounce_receiver") and service._bounce_receiver:
                await service._bounce_receiver.stop()
                service._bounce_receiver = None
            return BasicOkResponse(ok=True)

        host = bounce_config.get("imap_host")
        if not host:
            raise HTTPException(400, "Bounce enabled but imap_host not configured")

        # Stop existing bounce receiver if any
        if hasattr(service, "_bounce_receiver") and service._bounce_receiver:
            await service._bounce_receiver.stop()
            service._bounce_receiver = None

        from .bounce import BounceConfig

        config = BounceConfig(
            host=host,
            port=bounce_config.get("imap_port") or 993,
            user=bounce_config.get("imap_user") or "",
            password=bounce_config.get("imap_password") or "",
            use_ssl=bounce_config.get("imap_ssl", True),
            poll_interval=bounce_config.get("poll_interval") or 60,
        )

        service.configure_bounce_receiver(config)
        await service._start_bounce_receiver()
        return BasicOkResponse(ok=True)

    api.include_router(router)
    return api
