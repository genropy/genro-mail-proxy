"""
FastAPI application factory and HTTP schemas for the async mail service.

The module exposes a `create_app` function that builds the REST API used to
control the dispatcher and defines the pydantic payloads that document the
behaviour of each command.  Authentication is enforced through a configurable
API token carried in the ``X-API-Token`` header.
"""

from typing import Optional, Dict, Any, List, Literal, Union, Callable, AsyncContextManager
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, APIRouter, Depends, status
from fastapi.responses import Response
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, ConfigDict

from .core import AsyncMailCore

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


class CommandStatus(BaseModel):
    """Base schema shared by most responses produced by the service."""
    ok: bool
    error: Optional[str] = None


class BasicOkResponse(CommandStatus):
    pass


class AttachmentPayload(BaseModel):
    """Description of an attachment supported by the dispatcher."""
    filename: Optional[str] = None
    content: Optional[str] = None
    url: Optional[str] = None
    s3: Optional[Dict[str, Any]] = None


class MessagePayload(BaseModel):
    """Payload accepted by the ``addMessages`` command."""
    model_config = ConfigDict(populate_by_name=True)
    id: str
    account_id: Optional[str] = None
    from_: str = Field(alias="from")
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
    host: str
    port: int
    user: Optional[str] = None
    ttl: int
    limit_per_minute: Optional[int] = None
    limit_per_hour: Optional[int] = None
    limit_per_day: Optional[int] = None
    limit_behavior: Optional[str] = None
    use_tls: Optional[bool] = None
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
            raise HTTPException(status_code=400, detail=detail)
        return AddMessagesResponse.model_validate(result)

    @router.post("/delete-messages", response_model=DeleteMessagesResponse, response_model_exclude_none=True)
    async def delete_messages(payload: DeleteMessagesPayload):
        """Remove messages from the scheduler queue and related tracking tables."""
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("deleteMessages", payload.model_dump())
        return DeleteMessagesResponse.model_validate(result)

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

    api.include_router(router)
    return api
