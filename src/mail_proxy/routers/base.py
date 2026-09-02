# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""ProxyRoutes: the base every v1 route group extends.

Each group is a ``RoutingClass`` mounted on ``MailProxyApplication`` under one
root path segment, and carries the application it serves (``self.application``,
the D7 semantic-name rule) so its handlers can reach the mail engine and the
per-request auth gates. It owns no mail logic: a handler translates the request
into one ``MailProxy`` command and shapes the answer.

Two conventions hold across every group, and the routing depends on them:

- **Path parameters are positional, everything else is keyword-only.**
  genro-routes binds unconsumed path segments to the handler's positional
  parameters BY POSITION, and refuses the node when there are more segments
  than parameters — which is the 404 the v1 contract answers on a path one
  segment too long. A query parameter left positional would silently swallow
  that extra segment instead.
- **A handler taking query parameters also takes ``**kwargs``.** FastAPI
  ignored query keys no parameter declared; genro-routes would raise on them.
  ``POST /commands/run-now?tenant_id=x`` is the live case: the route reads its
  tenant from the token and the query key is accepted and discarded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar

from genro_asgi.exceptions import HTTPBadRequest
from genro_routes import RoutingClass
from pydantic import BaseModel, ValidationError

if TYPE_CHECKING:
    from ..core import MailProxy
    from ..mail_proxy_application import MailProxyApplication

__all__ = ["ProxyRoutes"]

PayloadModel = TypeVar("PayloadModel", bound=BaseModel)


class ProxyRoutes(RoutingClass):
    """Base of the v1 route groups: the application, its core, body validation."""

    def __init__(self, application: MailProxyApplication) -> None:
        self.application = application

    @property
    def core(self) -> MailProxy:
        """The mail engine every handler delegates to."""
        return self.application.core

    def validated_payload(
        self, model: type[PayloadModel], body_data: Any
    ) -> PayloadModel:
        """Build the request model from the parsed body, or answer 422.

        The ``HTTPBadRequest`` becomes a 422 in the application's override —
        the status v1 answered on a body pydantic refused.
        """
        try:
            return model.model_validate(body_data or {})
        except ValidationError as exc:
            raise HTTPBadRequest(f"Invalid request body: {exc}") from exc
