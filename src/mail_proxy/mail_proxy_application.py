# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""MailProxyApplication: the genro-asgi application serving the v1 HTTP contract.

The application is the transport layer of the 0.7.7 swap: it owns no mail
logic — every handler delegates to the ``MailProxy`` core received at
construction. It mounts at the site root (``mount = ""``), so the v1 paths
(``/health``, ``/tenant/...``) keep their production addresses.

Authentication reproduces the v1 ``X-API-Token`` semantics, resolved per
request BEFORE dispatch (async, against the tenants table — the reason the
sync server-side ``AuthCore`` is not used):

- token equal to the configured admin token → ``Avatar("admin", [ADMIN_TAG,
  TENANT_TAG])``;
- token matching a tenant api key → ``Avatar(tenant_id, [TENANT_TAG])``;
- no admin token configured → open access: every request is admin (v1 rule);
- anything else → anonymous: ruled entries answer 401, unruled ones serve.

Routes declare their gate with ``auth_rule=ADMIN_TAG`` (admin only) or
``auth_rule=TENANT_TAG`` (tenant or admin — the admin avatar carries both
tags), so 401/403 come from the router, never from handler code. Tenant
scoping (a tenant token addressing another tenant's resources) stays in the
handlers, as in v1.

Two v1-fidelity overrides:

- ``bind_kwargs`` injects the live ``Request`` into a handler that declares a
  ``request`` parameter — the three shared-path shims read ``request.method``;
- invalid handler arguments answer **422** as the v1 contract does, not the
  400 genro-asgi maps them to. This override is a bridge: it drops when
  genro-asgi answers 422 at the dispatcher (genropy/genro-asgi#45).
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from genro_asgi import Avatar
from genro_asgi.applications.openapi import OpenApiApplication
from genro_asgi.exceptions import HTTPBadRequest, HTTPException
from genro_asgi.middleware.base import headers_dict
from genro_routes import route

if TYPE_CHECKING:
    from genro_asgi.request import Request
    from genro_asgi.types import Receive, Scope, Send
    from genro_routes import RouterNode

    from .core import MailProxy

__all__ = ["ADMIN_TAG", "TENANT_TAG", "MailProxyApplication"]

API_TOKEN_HEADER = "x-api-token"
ADMIN_IDENTITY = "admin"
ADMIN_TAG = "ADMIN"
TENANT_TAG = "TENANT"


class MailProxyApplication(OpenApiApplication):
    """genro-asgi application exposing the mail-proxy v1 REST contract."""

    mount = ""
    openapi_info = {"title": "Async Mail Service"}

    def __init__(self, **kwargs: Any) -> None:
        self._core: MailProxy = kwargs.pop("core")
        self._api_token: str | None = kwargs.pop("api_token", None)
        super().__init__(**kwargs)

    @property
    def core(self) -> MailProxy:
        """The mail engine every handler delegates to."""
        return self._core

    @property
    def api_token(self) -> str | None:
        """The configured global admin token (``None`` → open access)."""
        return self._api_token

    async def on_startup(self) -> None:
        await self.core.start()

    async def on_shutdown(self) -> None:
        await self.core.stop()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            scope["auth"] = await self.authenticate_request(scope)
        try:
            await super().__call__(scope, receive, send)
        except HTTPBadRequest as exc:
            raise HTTPException(422, exc.detail, headers=exc.headers) from exc

    async def authenticate_request(self, scope: Scope) -> Avatar | None:
        """Resolve the request's ``X-API-Token`` into an ``Avatar`` (v1 semantics)."""
        token = headers_dict(scope).get(API_TOKEN_HEADER)
        admin_avatar = Avatar(ADMIN_IDENTITY, [ADMIN_TAG, TENANT_TAG])
        if not token:
            return None if self.api_token is not None else admin_avatar
        if self.api_token is not None and secrets.compare_digest(token, self.api_token):
            return admin_avatar
        tenant = await self.core.db.tenants.get_tenant_by_token(token)
        if tenant:
            return Avatar(tenant["id"], [TENANT_TAG])
        return None

    def bind_kwargs(self, node: RouterNode, request: Request) -> dict[str, Any]:
        kwargs = super().bind_kwargs(node, request)
        fields = node.params.get("fields") or []
        if any(field["name"] == "request" for field in fields):
            kwargs["request"] = request
        return kwargs

    @route()
    async def health(self) -> dict[str, str]:
        """Liveness probe, no auth: the v1 body at the v1 address."""
        return {"status": "ok"}
