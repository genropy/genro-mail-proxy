from typing import Optional, Dict, Any, List, Literal

from fastapi import FastAPI, HTTPException, APIRouter
from fastapi.responses import Response
from pydantic import BaseModel, Field, ConfigDict

from .core import AsyncMailCore

app = FastAPI(title="Async Mail Service")
service: AsyncMailCore | None = None

class CommandPayload(BaseModel):
    cmd: str
    payload: Optional[Dict[str, Any]] = None

class AccountPayload(BaseModel):
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
    ok: bool
    error: Optional[str] = None


class BasicOkResponse(CommandStatus):
    pass


class AttachmentPayload(BaseModel):
    filename: Optional[str] = None
    content: Optional[str] = None
    url: Optional[str] = None
    s3: Optional[Dict[str, Any]] = None


class SendMessagePayload(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    id: Optional[str] = None
    account_id: Optional[str] = None
    from_: str = Field(alias="from")
    to: str
    subject: str
    body: str
    content_type: Optional[str] = Field(default="plain")
    attachments: Optional[List[AttachmentPayload]] = None


class MessageEvent(BaseModel):
    id: Optional[str] = None
    status: Literal["sent", "error", "deferred"]
    deferred_until: Optional[int] = None
    error: Optional[str] = None
    timestamp: str
    account: Optional[str] = None


class SendMessageResponse(CommandStatus):
    result: Optional[MessageEvent] = None


class AccountInfo(BaseModel):
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


class PendingMessage(BaseModel):
    id: str
    to_addr: Optional[str] = None
    subject: Optional[str] = None
    started_at: Optional[str] = None


class PendingResponse(CommandStatus):
    pending: List[PendingMessage]


class DeferredMessage(BaseModel):
    id: str
    account_id: Optional[str] = None
    deferred_until: Optional[int] = None
    created_at: Optional[str] = None


class DeferredResponse(CommandStatus):
    deferred: List[DeferredMessage]


class RulePayload(BaseModel):
    name: Optional[str] = None
    enabled: bool = True
    priority: Optional[int] = None
    days: List[int] = Field(default_factory=list)
    start_hour: Optional[int] = Field(default=None, ge=0, le=23)
    end_hour: Optional[int] = Field(default=None, ge=0, le=23)
    cross_midnight: bool = False
    interval_minutes: int = Field(default=1, ge=1)


class RuleInfo(BaseModel):
    id: int
    name: Optional[str] = None
    enabled: bool
    priority: int
    days: List[int]
    start_hour: Optional[int] = None
    end_hour: Optional[int] = None
    cross_midnight: bool
    interval_minutes: int


class RulesResponse(CommandStatus):
    rules: List[RuleInfo]


class RuleTogglePayload(BaseModel):
    enabled: bool


class EnqueueMessagesPayload(BaseModel):
    messages: List[SendMessagePayload]


class EnqueueMessagesResponse(CommandStatus):
    queued: int

def create_app(svc: AsyncMailCore) -> FastAPI:
    global service
    service = svc
    api = app
    router = APIRouter(prefix="/commands", tags=["commands"])

    @api.get("/status", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def status():
        return BasicOkResponse(ok=True)

    @api.post("/command")
    async def command(payload: CommandPayload):
        if not service:
            raise HTTPException(500, "Service not initialized")
        return await service.handle_command(payload.cmd, payload.payload)

    @router.post("/run-now", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def run_now():
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("run now", {})
        return BasicOkResponse.model_validate(result)

    @router.post("/suspend", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def suspend():
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("suspend", {})
        return BasicOkResponse.model_validate(result)

    @router.post("/activate", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def activate():
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("activate", {})
        return BasicOkResponse.model_validate(result)

    @router.post("/send-message", response_model=SendMessageResponse, response_model_exclude_none=True)
    async def send_message(payload: SendMessagePayload):
        if not service:
            raise HTTPException(500, "Service not initialized")
        data = payload.model_dump(by_alias=True, exclude_none=True)
        if payload.attachments is not None:
            data["attachments"] = [att.model_dump(exclude_none=True) for att in payload.attachments]
        result = await service.handle_command("sendMessage", data)
        return SendMessageResponse.model_validate(result)

    @router.post("/add-messages", response_model=EnqueueMessagesResponse, response_model_exclude_none=True)
    async def add_messages(payload: EnqueueMessagesPayload):
        if not service:
            raise HTTPException(500, "Service not initialized")
        data = {
            "messages": [msg.model_dump(by_alias=True, exclude_none=True) for msg in payload.messages]
        }
        result = await service.handle_command("addMessages", data)
        return EnqueueMessagesResponse.model_validate(result)

    @router.post("/rules", response_model=RulesResponse, response_model_exclude_none=True)
    async def add_rule(payload: RulePayload):
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("addRule", payload.model_dump(exclude_none=True))
        return RulesResponse.model_validate(result)

    @router.get("/rules", response_model=RulesResponse, response_model_exclude_none=True)
    async def list_rules():
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("listRules", {})
        return RulesResponse.model_validate(result)

    @router.delete("/rules/{rule_id}", response_model=RulesResponse, response_model_exclude_none=True)
    async def delete_rule(rule_id: int):
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("deleteRule", {"id": rule_id})
        return RulesResponse.model_validate(result)

    @router.patch("/rules/{rule_id}", response_model=RulesResponse, response_model_exclude_none=True)
    async def toggle_rule(rule_id: int, payload: RuleTogglePayload):
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("setRuleEnabled", {"id": rule_id, "enabled": payload.enabled})
        return RulesResponse.model_validate(result)

    @api.post("/account", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def add_account(acc: AccountPayload):
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("addAccount", acc.model_dump())
        return BasicOkResponse.model_validate(result)

    @api.get("/accounts", response_model=AccountsResponse, response_model_exclude_none=True)
    async def list_accounts():
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("listAccounts", {})
        return AccountsResponse.model_validate(result)

    @api.delete("/account/{account_id}", response_model=BasicOkResponse, response_model_exclude_none=True)
    async def delete_account(account_id: str):
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("deleteAccount", {"id": account_id})
        return BasicOkResponse.model_validate(result)

    @api.get("/pending", response_model=PendingResponse, response_model_exclude_none=True)
    async def pending():
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("pendingMessages", {})
        return PendingResponse.model_validate(result)

    @api.get("/deferred", response_model=DeferredResponse, response_model_exclude_none=True)
    async def deferred():
        if not service:
            raise HTTPException(500, "Service not initialized")
        result = await service.handle_command("listDeferred", {})
        return DeferredResponse.model_validate(result)

    @api.get("/metrics")
    async def metrics():
        if not service:
            raise HTTPException(500, "Service not initialized")
        return Response(content=service.metrics.generate_latest(), media_type="text/plain; version=0.0.4")

    api.include_router(router)
    return api
