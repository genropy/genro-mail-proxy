# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""API base: generates FastAPI routes from endpoint classes via introspection.

Provides:
- Authentication middleware (verify_tenant_token, require_admin_token, require_token)
- Exception handlers for validation errors
- create_app() factory that registers endpoints dynamically
- register_endpoint() for introspecting endpoint classes

Usage:
    from core.mail_proxy.interface import create_app
    from core.mail_proxy.proxy import MailProxy

    proxy = MailProxy(db_path="/data/mail.db")
    app = create_app(proxy, api_token="secret")
"""

from __future__ import annotations

import inspect
import logging
import secrets
from collections.abc import Callable as CallableType
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any, Callable

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
    """Fallback: determine HTTP method from method name."""
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
    """Fallback: count non-self parameters."""
    sig = inspect.signature(method)
    return sum(1 for p in sig.parameters if p != "self")


def _create_model_fallback(method: Callable, method_name: str) -> type:
    """Fallback: create Pydantic model from method signature."""
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

    Args:
        app: FastAPI app or APIRouter to register routes on.
        endpoint: Endpoint instance (BaseEndpoint or duck-typed).
        prefix: Optional URL prefix (default: uses endpoint.name).
    """
    name = getattr(endpoint, "name", endpoint.__class__.__name__.lower())
    base_path = prefix or f"/{name}"

    # Use BaseEndpoint methods if available, otherwise iterate manually
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
        # Use endpoint methods for introspection if available
        if isinstance(endpoint, BaseEndpoint):
            http_method = endpoint.get_http_method(method_name)
            param_count = endpoint.count_params(method_name)
        else:
            http_method = _get_http_method_fallback(method_name)
            param_count = _count_params_fallback(method)

        path = f"{base_path}/{method_name}"
        doc = method.__doc__ or f"{method_name} operation"

        # Create route handler
        if http_method == "GET" or (http_method == "DELETE" and param_count <= 3):
            # Simple params → query parameters
            _register_query_route(app, path, method, http_method, doc)
        else:
            # Complex → request body
            _register_body_route(app, path, method, http_method, doc, method_name, endpoint)


def _register_query_route(
    app: FastAPI | APIRouter,
    path: str,
    method: Callable,
    http_method: str,
    doc: str
) -> None:
    """Register route with query parameters."""
    sig = inspect.signature(method)

    # Build parameter annotations for FastAPI
    params = []
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        ann = param.annotation if param.annotation is not inspect.Parameter.empty else str
        default = param.default if param.default is not inspect.Parameter.empty else ...
        params.append((param_name, ann, default))

    # Create dynamic handler
    async def handler(**kwargs: Any) -> Any:
        return await method(**kwargs)

    # Update handler signature for FastAPI
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

    # Register route
    if http_method == "GET":
        app.get(path, summary=doc.split("\n")[0])(handler)
    elif http_method == "DELETE":
        app.delete(path, summary=doc.split("\n")[0])(handler)


def _make_body_handler(method: Callable, RequestModel: type) -> Callable:
    """Create handler that accepts body and calls method."""
    async def handler(data: RequestModel) -> Any:  # type: ignore
        return await method(**data.model_dump())

    # Set proper signature so FastAPI recognizes data as Body
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
    # Create Pydantic model from signature - use endpoint method if available
    if isinstance(endpoint, BaseEndpoint):
        RequestModel = endpoint.create_request_model(method_name)
    else:
        RequestModel = _create_model_fallback(method, method_name)

    handler = _make_body_handler(method, RequestModel)
    handler.__doc__ = doc

    # Register route
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

    Authentication logic:
    1. Global token (admin) → can access any tenant
    2. Tenant token → can ONLY access own tenant resources

    Args:
        tenant_id: The tenant ID from the request.
        api_token: The token from X-API-Token header.
        global_token: The configured global API token (admin).

    Raises:
        HTTPException: 401 if token is invalid or tenant_id mismatch.
    """
    if not api_token:
        if global_token is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")
        return  # No token configured, allow access

    # Check global token first (admin can access everything)
    if global_token is not None and secrets.compare_digest(api_token, global_token):
        return  # Global admin token, full access

    # Look up token in tenants table
    if _service and getattr(_service, "db", None):
        token_tenant = await _service.db.table("tenants").get_tenant_by_token(api_token)
        if token_tenant:
            # Token belongs to a tenant - verify tenant_id matches
            if tenant_id and token_tenant["id"] != tenant_id:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token not authorized for this tenant")
            return  # Valid tenant token for own resources

    # Token didn't match global or any tenant
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")


async def require_admin_token(
    request: Request,
    api_token: str | None = Depends(api_key_scheme),
) -> None:
    """Require global admin token for admin-only endpoints.

    Admin-only endpoints include:
    - Creating/deleting tenants
    - Listing all tenants
    - Managing tenant API keys
    - Instance configuration

    Raises:
        HTTPException: 401 if token is not the global admin token.
    """
    expected = getattr(request.app.state, "api_token", None)

    if not api_token:
        if expected is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Admin token required")
        return  # No global token configured, allow access

    # Only accept global admin token
    if expected is not None and secrets.compare_digest(api_token, expected):
        return  # Valid admin token

    # Check if it's a tenant token (to give a helpful error message)
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
    """Validate the API token carried in the X-API-Token header.

    Accepts either:
    - The global API token - admin, full access
    - A valid tenant-specific API key - limited to own tenant resources
    """
    # Store token in request state for later tenant-aware verification
    request.state.api_token = api_token

    expected = getattr(request.app.state, "api_token", None)

    # No token provided
    if not api_token:
        if expected is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API token")
        return  # No global token configured, allow access

    # Check global token first (admin)
    if expected is not None and secrets.compare_digest(api_token, expected):
        request.state.is_admin = True
        return  # Global admin token

    # Check tenant token
    if _service and getattr(_service, "db", None):
        token_tenant = await _service.db.table("tenants").get_tenant_by_token(api_token)
        if token_tenant:
            # Valid tenant token - store tenant info for scope verification
            request.state.token_tenant_id = token_tenant["id"]
            request.state.is_admin = False
            return

    # Token didn't match global or any tenant
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
        api_token: Optional secret for X-API-Token header authentication.
        lifespan: Optional lifespan context manager for startup/shutdown.
        tenant_tokens_enabled: When True, enables per-tenant API keys.

    Returns:
        Configured FastAPI application.
    """
    global _service
    _service = svc

    app = FastAPI(title="Async Mail Service", lifespan=lifespan)
    app.state.api_token = api_token
    app.state.tenant_tokens_enabled = tenant_tokens_enabled

    # Exception handler for validation errors
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

    # Register entity endpoints
    _register_entity_endpoints(app, svc)

    # Register instance endpoints
    _register_instance_endpoints(app, svc)

    return app


def _register_entity_endpoints(app: FastAPI, svc: MailProxy) -> None:
    """Register entity endpoints via autodiscovery."""
    # Create router with authentication
    router = APIRouter(dependencies=[auth_dependency])

    # Autodiscover and register all entity endpoints
    for endpoint_class in BaseEndpoint.discover():
        # Skip instance endpoint - handled separately with special routes
        if endpoint_class.name == "instance":
            continue

        table = svc.db.table(endpoint_class.name)
        endpoint = endpoint_class(table)
        register_endpoint(router, endpoint)

    app.include_router(router)


def _register_instance_endpoints(app: FastAPI, svc: MailProxy) -> None:
    """Register instance-level endpoints (health, metrics, and instance operations)."""
    # Find InstanceEndpoint from discovery
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

    # Health endpoint (no auth required)
    @app.get("/health")
    async def health() -> dict:
        """Health check endpoint for container orchestration."""
        return await instance_endpoint.health()

    # Metrics endpoint (no auth required)
    @app.get("/metrics")
    async def metrics() -> Response:
        """Export Prometheus metrics in text exposition format."""
        return Response(content=svc.metrics.generate_latest(), media_type="text/plain; version=0.0.4")

    # Instance operations (auto-generated from InstanceEndpoint)
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
