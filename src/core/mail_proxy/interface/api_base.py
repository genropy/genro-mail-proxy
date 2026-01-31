# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""FastAPI route generation from endpoint classes via introspection.

This module generates REST API routes automatically from endpoint classes
by introspecting method signatures and creating appropriate handlers.

Components:
    create_app: FastAPI application factory.
    register_endpoint: Register endpoint methods as FastAPI routes.
    verify_tenant_token: Token verification for tenant-scoped requests.
    require_admin_token: Admin-only endpoint protection.
    require_token: General authentication dependency.

Example:
    Create and run the API server::

        from core.mail_proxy.interface import create_app
        from core.mail_proxy.proxy import MailProxy

        proxy = MailProxy(db_path="/data/mail.db")
        app = create_app(proxy, api_token="secret")

        # Run with uvicorn
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=8000)

    Register custom endpoints::

        from fastapi import FastAPI
        from core.mail_proxy.interface import register_endpoint

        app = FastAPI()
        endpoint = MyCustomEndpoint(table)
        register_endpoint(app, endpoint)

Note:
    Authentication uses X-API-Token header. Global token grants admin
    access to all tenants. Tenant tokens restrict access to own resources.
"""

from __future__ import annotations

import inspect
import logging
import secrets
from collections.abc import Callable
from collections.abc import Callable as CallableType
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.security import APIKeyHeader

from .endpoint_base import BaseEndpoint

if TYPE_CHECKING:
    from ..proxy import MailProxy

logger = logging.getLogger(__name__)

# Authentication constants
API_TOKEN_HEADER_NAME = "X-API-Token"
api_key_scheme = APIKeyHeader(name=API_TOKEN_HEADER_NAME, auto_error=False)

# Global service reference (set by create_app)
_service: MailProxy | None = None


def _get_http_method_fallback(method_name: str) -> str:
    """Infer HTTP method from method name prefix.

    Args:
        method_name: Name of the endpoint method.

    Returns:
        HTTP method string (GET, POST, DELETE, PATCH, PUT).
    """
    if method_name.startswith(("add", "create", "post", "run", "suspend", "activate")):
        return "POST"
    elif method_name.startswith(("delete", "remove")):
        return "DELETE"
    elif method_name.startswith(("update", "patch")):
        return "PATCH"
    elif method_name.startswith(("set", "put")):
        return "PUT"
    return "GET"


def _count_params_fallback(method: Callable) -> int:
    """Count non-self parameters for a method.

    Args:
        method: The method to introspect.

    Returns:
        Number of parameters excluding 'self'.
    """
    sig = inspect.signature(method)
    return sum(1 for p in sig.parameters if p != "self")


def _create_model_fallback(method: Callable, method_name: str) -> type:
    """Create Pydantic model from method signature.

    Args:
        method: The method to introspect.
        method_name: Name used for model class name.

    Returns:
        Dynamically created Pydantic model class.
    """
    from typing import get_type_hints

    from pydantic import create_model

    sig = inspect.signature(method)

    try:
        hints = get_type_hints(method)
    except Exception:
        hints = {}

    fields = {}
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        annotation = hints.get(param_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = Any

        if param.default is inspect.Parameter.empty:
            fields[param_name] = (annotation, ...)
        else:
            fields[param_name] = (annotation, param.default)

    model_name = f"{method_name.title().replace('_', '')}Request"
    return create_model(model_name, **fields)


def register_endpoint(app: FastAPI | APIRouter, endpoint: Any, prefix: str = "") -> None:
    """Register all methods of an endpoint as FastAPI routes.

    Introspects the endpoint to discover async methods and creates
    appropriate GET (query params) or POST (body) routes.

    Args:
        app: FastAPI app or APIRouter to register routes on.
        endpoint: Endpoint instance (BaseEndpoint or duck-typed).
        prefix: Optional URL prefix. Defaults to /{endpoint.name}.

    Example:
        ::

            endpoint = AccountEndpoint(db.table("accounts"))
            register_endpoint(app, endpoint)
            # Creates routes: GET /accounts/list, POST /accounts/add, etc.
    """
    name = getattr(endpoint, "name", endpoint.__class__.__name__.lower())
    base_path = prefix or f"/{name}"

    if isinstance(endpoint, BaseEndpoint):
        methods = endpoint.get_methods()
    else:
        methods = []
        for method_name in dir(endpoint):
            if method_name.startswith("_"):
                continue
            method = getattr(endpoint, method_name)
            if callable(method) and inspect.iscoroutinefunction(method):
                methods.append((method_name, method))

    for method_name, method in methods:
        if isinstance(endpoint, BaseEndpoint):
            http_method = endpoint.get_http_method(method_name)
            param_count = endpoint.count_params(method_name)
        else:
            http_method = _get_http_method_fallback(method_name)
            param_count = _count_params_fallback(method)

        path = f"{base_path}/{method_name}"
        doc = method.__doc__ or f"{method_name} operation"

        if http_method == "GET" or (http_method == "DELETE" and param_count <= 3):
            _register_query_route(app, path, method, http_method, doc)
        else:
            _register_body_route(app, path, method, http_method, doc, method_name, endpoint)


def _register_query_route(
    app: FastAPI | APIRouter, path: str, method: Callable, http_method: str, doc: str
) -> None:
    """Register route with query parameters."""
    sig = inspect.signature(method)

    params = []
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        ann = param.annotation if param.annotation is not inspect.Parameter.empty else str
        default = param.default if param.default is not inspect.Parameter.empty else ...
        params.append((param_name, ann, default))

    async def handler(**kwargs: Any) -> Any:
        return await method(**kwargs)

    new_params = [
        inspect.Parameter(
            name=p[0],
            kind=inspect.Parameter.KEYWORD_ONLY,
            default=Query(p[2]) if p[2] is not ... else Query(...),
            annotation=p[1],
        )
        for p in params
    ]
    handler.__signature__ = inspect.Signature(parameters=new_params)  # type: ignore
    handler.__doc__ = doc

    if http_method == "GET":
        app.get(path, summary=doc.split("\n")[0])(handler)
    elif http_method == "DELETE":
        app.delete(path, summary=doc.split("\n")[0])(handler)


def _make_body_handler(method: Callable, RequestModel: type) -> Callable:
    """Create handler that accepts body and calls method."""

    async def handler(data: RequestModel) -> Any:  # type: ignore
        return await method(**data.model_dump())

    handler.__signature__ = inspect.Signature(  # type: ignore
        parameters=[
            inspect.Parameter(
                "data",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                annotation=RequestModel,
            )
        ]
    )
    return handler


def _register_body_route(
    app: FastAPI | APIRouter,
    path: str,
    method: Callable,
    http_method: str,
    doc: str,
    method_name: str,
    endpoint: Any = None,
) -> None:
    """Register route with request body."""
    if isinstance(endpoint, BaseEndpoint):
        RequestModel = endpoint.create_request_model(method_name)
    else:
        RequestModel = _create_model_fallback(method, method_name)

    handler = _make_body_handler(method, RequestModel)
    handler.__doc__ = doc

    if http_method == "POST":
        app.post(path, summary=doc.split("\n")[0])(handler)
    elif http_method == "PUT":
        app.put(path, summary=doc.split("\n")[0])(handler)
    elif http_method == "PATCH":
        app.patch(path, summary=doc.split("\n")[0])(handler)
    elif http_method == "DELETE":
        app.delete(path, summary=doc.split("\n")[0])(handler)


# =============================================================================
# Authentication functions
# =============================================================================


async def verify_tenant_token(
    tenant_id: str | None,
    api_token: str | None,
    global_token: str | None,
) -> None:
    """Verify API token for a tenant-scoped request.

    Args:
        tenant_id: The tenant ID from the request.
        api_token: The token from X-API-Token header.
        global_token: The configured global API token (admin).

    Raises:
        HTTPException: 401 if token is invalid or tenant_id mismatch.

    Note:
        - Global token grants access to any tenant
        - Tenant token grants access ONLY to own resources
        - No token configured = open access
    """
    if not api_token:
        if global_token is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")
        return

    if global_token is not None and secrets.compare_digest(api_token, global_token):
        return

    if _service and getattr(_service, "db", None):
        token_tenant = await _service.db.table("tenants").get_tenant_by_token(api_token)
        if token_tenant:
            if tenant_id and token_tenant["id"] != tenant_id:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED, "Token not authorized for this tenant"
                )
            return

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")


async def require_admin_token(
    request: Request,
    api_token: str | None = Depends(api_key_scheme),
) -> None:
    """Require global admin token for admin-only endpoints.

    Admin-only endpoints include tenant management, API key operations,
    and instance configuration.

    Args:
        request: FastAPI request object.
        api_token: Token from X-API-Token header (via Depends).

    Raises:
        HTTPException: 401 if not global admin token, 403 if tenant token.
    """
    expected = getattr(request.app.state, "api_token", None)

    if not api_token:
        if expected is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin token required")
        return

    if expected is not None and secrets.compare_digest(api_token, expected):
        return

    if _service and getattr(_service, "db", None):
        token_tenant = await _service.db.table("tenants").get_tenant_by_token(api_token)
        if token_tenant:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Admin token required, tenant tokens not allowed for this operation",
            )

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")


async def require_token(
    request: Request,
    api_token: str | None = Depends(api_key_scheme),
) -> None:
    """Validate API token from X-API-Token header.

    Accepts global admin token (full access) or tenant token (own resources).
    Stores token info in request.state for downstream verification.

    Args:
        request: FastAPI request object.
        api_token: Token from X-API-Token header (via Depends).

    Raises:
        HTTPException: 401 if token is invalid.
    """
    request.state.api_token = api_token

    expected = getattr(request.app.state, "api_token", None)

    if not api_token:
        if expected is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")
        return

    if expected is not None and secrets.compare_digest(api_token, expected):
        request.state.is_admin = True
        return

    if _service and getattr(_service, "db", None):
        token_tenant = await _service.db.table("tenants").get_tenant_by_token(api_token)
        if token_tenant:
            request.state.token_tenant_id = token_tenant["id"]
            request.state.is_admin = False
            return

    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")


# Dependency shortcuts
admin_dependency = Depends(require_admin_token)
auth_dependency = Depends(require_token)


# =============================================================================
# Application factory
# =============================================================================


def create_app(
    svc: MailProxy,
    api_token: str | None = None,
    lifespan: CallableType[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
    tenant_tokens_enabled: bool = False,
) -> FastAPI:
    """Create and configure the FastAPI application.

    Args:
        svc: MailProxy instance implementing business logic.
        api_token: Optional global token for X-API-Token authentication.
        lifespan: Optional lifespan context manager. If None, creates
            default that starts/stops the proxy service.
        tenant_tokens_enabled: When True, enables per-tenant API keys.

    Returns:
        Configured FastAPI application with all routes registered.

    Example:
        ::

            from core.mail_proxy.proxy import MailProxy
            from core.mail_proxy.interface import create_app

            proxy = MailProxy(db_path="/data/mail.db")
            app = create_app(proxy, api_token="admin-secret")

            # Run with uvicorn
            import uvicorn
            uvicorn.run(app)
    """
    global _service
    _service = svc

    if lifespan is None:
        from collections.abc import AsyncGenerator
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def default_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
            """Default lifespan: start and stop the MailProxy service."""
            logger.info("Starting mail-proxy service...")
            await svc.start()
            logger.info("Mail-proxy service started")
            try:
                yield
            finally:
                logger.info("Stopping mail-proxy service...")
                await svc.stop()
                logger.info("Mail-proxy service stopped")

        lifespan = default_lifespan

    app = FastAPI(title="Async Mail Service", lifespan=lifespan)
    app.state.api_token = api_token
    app.state.tenant_tokens_enabled = tenant_tokens_enabled

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """Handle FastAPI request validation errors with detailed logging."""
        body = await request.body()
        logger.error(f"Validation error on {request.method} {request.url.path}")
        logger.error(f"Request body: {body.decode('utf-8', errors='replace')}")
        logger.error(f"Validation errors: {exc.errors()}")
        return JSONResponse(status_code=422, content={"detail": exc.errors()})

    _register_entity_endpoints(app, svc)
    _register_instance_endpoints(app, svc)

    return app


def _register_entity_endpoints(app: FastAPI, svc: MailProxy) -> None:
    """Register entity endpoints via autodiscovery."""
    router = APIRouter(dependencies=[auth_dependency])

    for endpoint_class in BaseEndpoint.discover():
        if endpoint_class.name == "instance":
            continue

        table = svc.db.table(endpoint_class.name)
        endpoint = endpoint_class(table)
        register_endpoint(router, endpoint)

    app.include_router(router)


def _register_instance_endpoints(app: FastAPI, svc: MailProxy) -> None:
    """Register instance-level endpoints (health, metrics, operations)."""
    instance_class = None
    for endpoint_class in BaseEndpoint.discover():
        if endpoint_class.name == "instance":
            instance_class = endpoint_class
            break

    if not instance_class:
        logger.warning("InstanceEndpoint not found in discovery")
        return

    instance_table = svc.db.table("instance")
    instance_endpoint = instance_class(instance_table, proxy=svc)

    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint for container orchestration."""
        return await instance_endpoint.health()

    @app.get("/metrics")
    async def metrics() -> Response:
        """Export Prometheus metrics in text exposition format."""
        return Response(
            content=svc.metrics.generate_latest(), media_type="text/plain; version=0.0.4"
        )

    router = APIRouter(dependencies=[auth_dependency])
    register_endpoint(router, instance_endpoint)
    app.include_router(router)


__all__ = [
    "API_TOKEN_HEADER_NAME",
    "admin_dependency",
    "api_key_scheme",
    "auth_dependency",
    "create_app",
    "register_endpoint",
    "require_admin_token",
    "require_token",
    "verify_tenant_token",
]
