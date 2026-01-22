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

        from async_mail_service.core import AsyncMailCore
        from async_mail_service.api import create_app

        core = AsyncMailCore(db_path="/data/mail.db")
        app = create_app(core, api_token="secret-token")

        # Run with uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)
"""

import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, ConfigDict, Field

from .core import AsyncMailCore
from .models import AttachmentPayload

logger = logging.getLogger(__name__)

app = FastAPI(title="Async Mail Service")
service: AsyncMailCore | None = None
API_TOKEN_HEADER_NAME = "X-API-Token"
api_key_scheme = APIKeyHeader(name=API_TOKEN_HEADER_NAME, auto_error=False)
app.state.api_token = None

async def require_token(
    request: Request,
    api_token: str | None = Depends(api_key_scheme)
) -> None:
    """Validate the API token carried in the ``X-API-Token`` header.

    If a token has been configured through :func:`create_app` and a request
    provides either a missing or different value, a ``401`` error is raised.
    When no token is configured the dependency is effectively bypassed.
    """
    expected = getattr(request.app.state, "api_token", None)
    if expected is None:
        return
    if not api_token or api_token != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")

auth_dependency = Depends(require_token)

class AccountPayload(BaseModel):
    """SMTP account definition used when adding or updating accounts."""
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


# AttachmentPayload is imported from models.py for consistency and proper validation


class MessagePayload(BaseModel):
    """Payload accepted by the ``addMessages`` command."""
    model_config = ConfigDict(populate_by_name=True)
    id: str
    account_id: str | None = None
    from_addr: str = Field(alias="from")
    to: list[str] | str
    cc: list[str] | str | None = None
    bcc: list[str] | str | None = None
    reply_to: str | None = None
    return_path: str | None = None
    subject: str
    body: str
    content_type: str | None = Field(default="plain")
    headers: dict[str, Any] | None = None
    message_id: str | None = None
    attachments: list[AttachmentPayload] | None = None
    priority: int | Literal["immediate", "high", "medium", "low"] | None = None
    deferred_ts: int | None = None


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
    created_at: str | None = None


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
    deferred_ts: int | None = None
    sent_ts: int | None = None
    error_ts: int | None = None
    error: str | None = None
    reported_ts: int | None = None
    created_at: str | None = None
    updated_at: str | None = None
    message: dict[str, Any]


class MessagesResponse(CommandStatus):
    messages: list[MessageRecord]


class DeleteMessagesPayload(BaseModel):
    ids: list[str] = Field(default_factory=list)


class DeleteMessagesResponse(CommandStatus):
    removed: int
    not_found: list[str] | None = None


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
    active: bool = True


class TenantUpdatePayload(BaseModel):
    """Tenant update payload - all fields optional."""
    name: str | None = None
    client_auth: dict[str, Any] | None = None
    client_base_url: str | None = None
    client_sync_path: str | None = None
    client_attachment_path: str | None = None
    rate_limits: dict[str, Any] | None = None
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
    active: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class TenantsResponse(CommandStatus):
    """Response with list of tenants."""
    tenants: list[TenantInfo]


def create_app(
    svc: AsyncMailCore,
    api_token: str | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager] | None = None
) -> FastAPI:
    """Create and configure the FastAPI application.

    Parameters
    ----------
    svc:
        Instance of :class:`async_mail_service.core.AsyncMailCore` that
        implements the business logic for each command.
    api_token:
        Optional secret used to protect every endpoint. When provided, the
        ``X-API-Token`` header must match this value on every request.
    lifespan:
        Optional lifespan context manager for startup/shutdown events.

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

    @api.get("/status", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def status():
        """Return authenticated service status.

        Unlike ``/health``, this endpoint requires authentication and confirms
        that the API token is valid.

        Returns:
            BasicOkResponse: Status response with ``ok=True``.
        """
        return BasicOkResponse(ok=True)

    @router.post("/run-now", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def run_now(tenant_id: str | None = None):
        """Trigger an immediate dispatch cycle without waiting for the scheduler.

        Args:
            tenant_id: Optional tenant ID to limit the sync to a specific tenant.
                If None, processes messages for all tenants.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True`` after the cycle completes.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        payload = {"tenant_id": tenant_id} if tenant_id else {}
        result = await service.handle_command("run now", payload)
        return BasicOkResponse.model_validate(result)

    @router.post("/suspend", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def suspend():
        """Pause the automatic dispatch scheduler.

        Messages remain in the queue but are not processed until the scheduler
        is reactivated via ``/activate``. Useful for maintenance or debugging.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("suspend", {})
        return BasicOkResponse.model_validate(result)

    @router.post("/activate", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def activate():
        """Resume the automatic dispatch scheduler after suspension.

        Restarts processing of queued messages according to the configured
        interval and rate limits.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("activate", {})
        return BasicOkResponse.model_validate(result)

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
    async def delete_messages(payload: DeleteMessagesPayload):
        """Remove messages from the queue by their IDs.

        Deletes specified messages from both the queue and tracking tables.
        Messages already sent or in transit may not be cancellable.

        Args:
            payload: Request body containing list of message IDs to remove.

        Returns:
            DeleteMessagesResponse: Count of removed messages and list of
                IDs that were not found.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("deleteMessages", payload.model_dump())
        return DeleteMessagesResponse.model_validate(result)

    @router.post("/cleanup-messages", response_model=CleanupMessagesResponse, response_model_exclude_none=True)
    async def cleanup_messages(payload: CleanupMessagesPayload = CleanupMessagesPayload()):
        """Manually trigger cleanup of reported messages older than retention period.

        By default uses the configured retention period. Optionally specify
        older_than_seconds to override the retention period for this cleanup.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("cleanupMessages", payload.model_dump())
        return CleanupMessagesResponse.model_validate(result)

    @api.post("/account", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def add_account(acc: AccountPayload):
        """Register or update an SMTP account configuration.

        Creates a new SMTP account or updates an existing one with the same ID.
        Account settings include host, port, credentials, TLS options, and rate limits.

        Args:
            acc: SMTP account configuration including connection details and limits.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("addAccount", acc.model_dump())
        return BasicOkResponse.model_validate(result)

    @api.get("/accounts", response_model=AccountsResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def list_accounts():
        """Retrieve all configured SMTP accounts.

        Returns the list of registered SMTP accounts with their configuration,
        excluding sensitive data like passwords.

        Returns:
            AccountsResponse: List of account configurations with ``ok=True``.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("listAccounts", {})
        return AccountsResponse.model_validate(result)

    @api.delete("/account/{account_id}", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def delete_account(account_id: str):
        """Delete an SMTP account by its ID.

        Removes the account configuration and cleans up associated scheduler state.
        Messages assigned to this account will fail delivery.

        Args:
            account_id: Unique identifier of the account to delete.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("deleteAccount", {"id": account_id})
        return BasicOkResponse.model_validate(result)

    @api.get("/messages", response_model=MessagesResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def all_messages():
        """List all messages currently in the dispatch queue.

        Returns complete message records including payload, status timestamps,
        and error information for debugging and monitoring purposes.

        Returns:
            MessagesResponse: List of message records with ``ok=True``.

        Raises:
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("listMessages", {})
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
    async def get_tenant(tenant_id: str):
        """Retrieve a specific tenant configuration.

        Args:
            tenant_id: Unique identifier of the tenant.

        Returns:
            TenantInfo: Complete tenant configuration.

        Raises:
            HTTPException: 404 if the tenant is not found.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("getTenant", {"id": tenant_id})
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        # Remove 'ok' key before returning as TenantInfo
        result.pop("ok", None)
        return TenantInfo(**result)

    @api.put("/tenant/{tenant_id}", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def update_tenant(tenant_id: str, payload: TenantUpdatePayload):
        """Update an existing tenant's configuration.

        Applies partial updates to the tenant. Only provided fields are updated;
        omitted fields retain their current values.

        Args:
            tenant_id: Unique identifier of the tenant to update.
            payload: Fields to update (all optional).

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 404 if the tenant is not found.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        update_data = payload.model_dump(exclude_none=True)
        update_data["id"] = tenant_id
        result = await service.handle_command("updateTenant", update_data)
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return BasicOkResponse.model_validate(result)

    @api.delete("/tenant/{tenant_id}", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def delete_tenant(tenant_id: str):
        """Delete a tenant and all associated resources.

        Removes the tenant along with all its SMTP accounts and queued messages.
        This operation is irreversible.

        Args:
            tenant_id: Unique identifier of the tenant to delete.

        Returns:
            BasicOkResponse: Confirmation with ``ok=True``.

        Raises:
            HTTPException: 404 if the tenant is not found.
            HTTPException: 500 if the service is not initialized.
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("deleteTenant", {"id": tenant_id})
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return BasicOkResponse.model_validate(result)

    api.include_router(router)
    return api
