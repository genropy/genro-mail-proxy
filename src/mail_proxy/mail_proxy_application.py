# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""MailProxyApplication: the genro-asgi application serving the v1 HTTP contract.

The application is the transport layer of the 0.7.7 swap: it owns no mail
logic — every handler delegates to the ``MailProxy`` core received at
construction. It owns no addresses of its own beyond the
liveness probe: every contract route belongs to a version branch.

The application mounts on ``mailproxy`` and carries ONE BRANCH PER CONTRACT
VERSION: the version is a path segment, so a caller moves between versions by
changing its base URL and nothing else (ADR-011). ``v1`` is the branch serving
the contract the production sites call — ``/mailproxy/v1/tenant/{id}`` and the
other 25 routes (``routers/v1.py``).

One route sits on the application itself, outside every version: ``health``, at
``/mailproxy/health``. It is the container's liveness probe, and it must not
depend on which contract versions are mounted. ``v1`` publishes a ``health`` of
its own too, because the Genropy client polls ``proxy_url + /health`` and
``proxy_url`` carries the version.

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
tags), so 401/403 come from the router, never from handler code. Two things the
router cannot express stay with the handlers, as in v1: tenant scoping (a
tenant token addressing another tenant's resources answers 401) and the
admin-only branches of a shared-path shim whose other branches are not
(``require_admin_tag``).

Two v1-fidelity overrides:

- ``bind_kwargs`` injects the live ``Request`` into a handler that declares a
  ``request`` parameter — the shared-path shims read ``request.method``;
- invalid handler arguments answer **422** as the v1 contract does, not the
  400 genro-asgi maps them to. The same override carries a handler's own
  ``HTTPBadRequest``, which is how a route reports a body pydantic refused.
  A DELIBERATE 400 is therefore raised as ``HTTPException(400, ...)``, never
  as ``HTTPBadRequest``. This override is a bridge: it drops when genro-asgi
  answers 422 at the dispatcher (genropy/genro-asgi#45).
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

from genro_asgi import Avatar
from genro_asgi.applications.openapi import OpenApiApplication
from genro_asgi.exceptions import HTTPBadRequest, HTTPException, HTTPForbidden, HTTPUnauthorized
from genro_asgi.middleware.base import headers_dict
from genro_routes import route

from .auth_tags import ADMIN_IDENTITY, ADMIN_TAG, API_TOKEN_HEADER, TENANT_TAG
from .routers import V1Routes

if TYPE_CHECKING:
    from genro_asgi.request import Request
    from genro_asgi.types import Receive, Scope, Send
    from genro_routes import RouterNode

    from .core import MailProxy

__all__ = ["ADMIN_TAG", "API_TOKEN_HEADER", "TENANT_TAG", "MailProxyApplication"]


class MailProxyApplication(OpenApiApplication):
    """genro-asgi application exposing the mail-proxy v1 REST contract."""

    mount = "mailproxy"
    openapi_info = {"title": "Async Mail Service"}

    def __init__(self, **kwargs: Any) -> None:
        self._core: MailProxy = kwargs.pop("core")
        self._api_token: str | None = kwargs.pop("api_token", None)
        super().__init__(**kwargs)
        self.route.add_branches({"name": "v1", "instance": V1Routes(self)})

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

    def require_admin_tag(self, request: Request) -> None:
        """Refuse a request whose identity is not the admin one (403).

        The gate of the admin-only branches of a shim the router had to open to
        tenant tokens. The status is v1's: the token is real, its scope is not.
        """
        if ADMIN_TAG not in request.auth_tags:
            raise HTTPForbidden(
                "Admin token required, tenant tokens not allowed for this operation"
            )

    def require_tenant_scope(self, request: Request, tenant_id: str | None) -> None:
        """Refuse a tenant token that addresses another tenant's resources (401).

        The admin identity passes for every tenant, and a request naming no
        tenant is not scoped at all — both are v1's rules, and the second is
        what lets ``POST /account`` register a tenantless account.
        """
        if tenant_id is None or not tenant_id:
            return
        avatar = request.avatar()
        if ADMIN_TAG in avatar.tags:
            return
        if avatar.identity != tenant_id:
            raise HTTPUnauthorized("Token not authorized for this tenant")

    def get_token_tenant_id(self, request: Request) -> str | None:
        """The tenant a tenant key names, or ``None`` for the admin identity.

        ``POST /commands/run-now`` deduces its tenant this way and from nowhere
        else, so an admin token wakes every tenant and a tenant key only its own.
        """
        avatar = request.avatar()
        if ADMIN_TAG in avatar.tags:
            return None
        return avatar.identity

    @route()
    async def health(self) -> dict[str, str]:
        """Liveness probe of the process, no auth and no version.

        The container's healthcheck reads this one, so it survives a version
        branch being added or retired. Each version publishes its own.
        """
        return {"status": "ok"}
