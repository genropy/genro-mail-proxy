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
- Managing storage volumes for attachments
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

from typing import Optional, Dict, Any, List, Literal, Union, Callable, AsyncContextManager
import logging

from fastapi import FastAPI, HTTPException, APIRouter, Depends, status, Request
from fastapi.responses import Response, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ConfigDict

from .core import AsyncMailCore
from .models import AttachmentPayload

logger = logging.getLogger(__name__)

app = FastAPI(title="Async Mail Service")
service: AsyncMailCore | None = None
API_TOKEN_HEADER_NAME = "X-API-Token"
api_key_scheme = APIKeyHeader(name=API_TOKEN_HEADER_NAME, auto_error=False)
app.state.api_token = None

async def require_token(api_token: str | None = Depends(api_key_scheme)) -> None:
    """Validate the API token carried in the ``X-API-Token`` header.

    If a token has been configured through :func:`create_app` and a request
    provides either a missing or different value, a ``401`` error is raised.
    When no token is configured the dependency is effectively bypassed.
    """
    expected = getattr(app.state, "api_token", None)
    if expected is None:
        return
    if not api_token or api_token != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")

auth_dependency = Depends(require_token)

class AccountPayload(BaseModel):
    """SMTP account definition used when adding or updating accounts."""
    id: str
    tenant_id: Optional[str] = None
    host: str
    port: int
    user: Optional[str] = None
    password: Optional[str] = None
    ttl: Optional[int] = 300
    limit_per_minute: Optional[int] = None
    limit_per_hour: Optional[int] = None
    limit_per_day: Optional[int] = None
    limit_behavior: Optional[str] = "defer"
    use_tls: Optional[bool] = None
    use_ssl: Optional[bool] = None
    batch_size: Optional[int] = None


class CommandStatus(BaseModel):
    """Base schema shared by most responses produced by the service."""
    ok: bool
    error: Optional[str] = None


class BasicOkResponse(CommandStatus):
    pass


# AttachmentPayload is imported from models.py for consistency and proper validation


class MessagePayload(BaseModel):
    """Payload accepted by the ``addMessages`` command."""
    model_config = ConfigDict(populate_by_name=True)
    id: str
    account_id: Optional[str] = None
    from_addr: str = Field(alias="from")
    to: Union[List[str], str]
    cc: Optional[Union[List[str], str]] = None
    bcc: Optional[Union[List[str], str]] = None
    reply_to: Optional[str] = None
    return_path: Optional[str] = None
    subject: str
    body: str
    content_type: Optional[str] = Field(default="plain")
    headers: Optional[Dict[str, Any]] = None
    message_id: Optional[str] = None
    attachments: Optional[List[AttachmentPayload]] = None
    priority: Optional[Union[int, Literal["immediate", "high", "medium", "low"]]] = None
    deferred_ts: Optional[int] = None


class AccountInfo(BaseModel):
    """Stored SMTP account as returned by ``listAccounts``."""
    id: str
    tenant_id: Optional[str] = None
    host: str
    port: int
    user: Optional[str] = None
    ttl: int
    limit_per_minute: Optional[int] = None
    limit_per_hour: Optional[int] = None
    limit_per_day: Optional[int] = None
    limit_behavior: Optional[str] = None
    use_tls: Optional[bool] = None
    use_ssl: Optional[bool] = None
    batch_size: Optional[int] = None
    created_at: Optional[str] = None


class AccountsResponse(CommandStatus):
    accounts: List[AccountInfo]


class EnqueueMessagesPayload(BaseModel):
    """Queue of messages used by ``addMessages``."""
    messages: List[MessagePayload]
    default_priority: Optional[Union[int, Literal["immediate", "high", "medium", "low"]]] = None


class RejectedMessage(BaseModel):
    """Rejected message entry."""
    id: Optional[str] = None
    reason: str


class AddMessagesResponse(CommandStatus):
    """Response returned by the ``addMessages`` command."""
    queued: int = 0
    rejected: List[RejectedMessage] = Field(default_factory=list)


class MessageRecord(BaseModel):
    """Full representation of a message tracked by the dispatcher."""
    id: str
    priority: int
    account_id: Optional[str] = None
    deferred_ts: Optional[int] = None
    sent_ts: Optional[int] = None
    error_ts: Optional[int] = None
    error: Optional[str] = None
    reported_ts: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message: Dict[str, Any]


class MessagesResponse(CommandStatus):
    messages: List[MessageRecord]


class DeleteMessagesPayload(BaseModel):
    ids: List[str] = Field(default_factory=list)


class DeleteMessagesResponse(CommandStatus):
    removed: int
    not_found: Optional[List[str]] = None


class CleanupMessagesPayload(BaseModel):
    """Request payload for manual cleanup of reported messages."""
    older_than_seconds: Optional[int] = None


class CleanupMessagesResponse(CommandStatus):
    """Response from cleanup operation."""
    removed: int


class VolumePayload(BaseModel):
    """Storage volume configuration."""
    name: str
    backend: Literal["s3", "gcs", "azure", "local", "http", "webdav", "memory"]
    config: Dict[str, Any]
    account_id: Optional[str] = None  # None = global volume


class AddVolumesPayload(BaseModel):
    """Payload for adding/updating storage volumes."""
    volumes: List[VolumePayload]


class VolumeInfo(BaseModel):
    """Stored volume as returned by listVolumes."""
    id: int
    name: str
    backend: str
    config: Dict[str, Any]
    account_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class VolumesResponse(CommandStatus):
    """Response with list of volumes."""
    volumes: List[VolumeInfo]


class TenantPayload(BaseModel):
    """Tenant configuration payload."""
    id: str
    name: Optional[str] = None
    client_auth: Optional[Dict[str, Any]] = None
    client_base_url: Optional[str] = None
    client_sync_path: Optional[str] = None
    client_attachment_path: Optional[str] = None
    rate_limits: Optional[Dict[str, Any]] = None
    active: bool = True


class TenantUpdatePayload(BaseModel):
    """Tenant update payload - all fields optional."""
    name: Optional[str] = None
    client_auth: Optional[Dict[str, Any]] = None
    client_base_url: Optional[str] = None
    client_sync_path: Optional[str] = None
    client_attachment_path: Optional[str] = None
    rate_limits: Optional[Dict[str, Any]] = None
    active: Optional[bool] = None


class TenantInfo(BaseModel):
    """Stored tenant as returned by listTenants."""
    id: str
    name: Optional[str] = None
    client_auth: Optional[Dict[str, Any]] = None
    client_base_url: Optional[str] = None
    client_sync_path: Optional[str] = None
    client_attachment_path: Optional[str] = None
    rate_limits: Optional[Dict[str, Any]] = None
    active: bool = True
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TenantsResponse(CommandStatus):
    """Response with list of tenants."""
    tenants: List[TenantInfo]


def create_app(
    svc: AsyncMailCore,
    api_token: str | None = None,
    lifespan: Callable[[FastAPI], AsyncContextManager] | None = None
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
    if lifespan is not None:
        api = FastAPI(title="Async Mail Service", lifespan=lifespan)
    else:
        api = app

    api.state.api_token = api_token
    router = APIRouter(prefix="/commands", tags=["commands"], dependencies=[auth_dependency])

    @api.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """Log validation errors with full details."""
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
        """Health check endpoint for container monitoring (no authentication required)."""
        return {"status": "ok"}

    @api.get("/status", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def status():
        """Return a simple health status payload."""
        return BasicOkResponse(ok=True)

    @router.post("/run-now", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def run_now():
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("run now", {})
        return BasicOkResponse.model_validate(result)

    @router.post("/suspend", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def suspend():
        """Suspend the scheduler component of the mail service."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("suspend", {})
        return BasicOkResponse.model_validate(result)

    @router.post("/activate", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def activate():
        """Activate the scheduler component of the mail service."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("activate", {})
        return BasicOkResponse.model_validate(result)

    @router.post("/add-messages", response_model=AddMessagesResponse, response_model_exclude_none=True)
    async def add_messages(payload: EnqueueMessagesPayload):
        """Push a batch of messages into the scheduler queue."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        serialized: List[Dict[str, Any]] = []
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
        """Remove messages from the scheduler queue and related tracking tables."""
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
        """Register or update an SMTP account definition."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("addAccount", acc.model_dump())
        return BasicOkResponse.model_validate(result)

    @api.get("/accounts", response_model=AccountsResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def list_accounts():
        """List the SMTP accounts known by the dispatcher."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("listAccounts", {})
        return AccountsResponse.model_validate(result)

    @api.delete("/account/{account_id}", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def delete_account(account_id: str):
        """Remove an SMTP account and any scheduler state bound to it."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("deleteAccount", {"id": account_id})
        return BasicOkResponse.model_validate(result)

    @api.get("/messages", response_model=MessagesResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def all_messages():
        """Expose the current message queue with detailed payload information."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("listMessages", {})
        return MessagesResponse.model_validate(result)

    @api.get("/metrics", dependencies=[auth_dependency])
    async def metrics():
        """Expose Prometheus metrics collected by the dispatcher."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        return Response(content=service.metrics.generate_latest(), media_type="text/plain; version=0.0.4")

    @api.post("/volume", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def add_volume(payload: VolumePayload):
        """Register or update a storage volume definition."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        await service.persistence.add_volumes([payload.model_dump()])
        await service.reload_volumes()  # Reload volumes into storage manager
        return BasicOkResponse(ok=True)

    @api.get("/volumes", response_model=VolumesResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def list_volumes(account_id: Optional[str] = None):
        """List storage volumes.

        If account_id is provided, returns volumes accessible by that account (specific + global).
        If account_id is None, returns all volumes (admin view).
        """
        if not service:
            raise HTTPException(500, "Service not initialized")
        volumes = await service.persistence.list_volumes(account_id)
        return VolumesResponse(ok=True, volumes=volumes)

    @api.get("/volume/{name}", response_model=VolumeInfo, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def get_volume(name: str, account_id: Optional[str] = None):
        """Get a specific storage volume by name."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        volume = await service.persistence.get_volume(name, account_id)
        if not volume:
            raise HTTPException(404, f"Volume '{name}' not found")
        return VolumeInfo(**volume)

    @api.delete("/volume/{name}", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def delete_volume(name: str, account_id: Optional[str] = None):
        """Remove a storage volume by name."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        deleted = await service.persistence.delete_volume(name, account_id)
        if not deleted:
            raise HTTPException(404, f"Volume '{name}' not found")
        await service.reload_volumes()  # Reload volumes into storage manager
        return BasicOkResponse(ok=True)

    # Tenant endpoints
    @api.post("/tenant", response_model=BasicOkResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def add_tenant(payload: TenantPayload):
        """Register or update a tenant configuration."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("addTenant", payload.model_dump())
        return BasicOkResponse.model_validate(result)

    @api.get("/tenants", response_model=TenantsResponse, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def list_tenants(active_only: bool = False):
        """List all tenants."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("listTenants", {"active_only": active_only})
        return TenantsResponse.model_validate(result)

    @api.get("/tenant/{tenant_id}", response_model=TenantInfo, response_model_exclude_none=True, dependencies=[auth_dependency])
    async def get_tenant(tenant_id: str):
        """Get a specific tenant by ID."""
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
        """Update a tenant's configuration."""
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
        """Delete a tenant and all associated accounts/messages."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("deleteTenant", {"id": tenant_id})
        if not result.get("ok"):
            raise HTTPException(404, f"Tenant '{tenant_id}' not found")
        return BasicOkResponse.model_validate(result)

    api.include_router(router)
    return api
