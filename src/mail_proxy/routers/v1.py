# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""V1Routes: the whole v1 contract under one branch.

The application carries one branch per API version, so the version is a path
segment and a caller changes version by changing its base URL — nothing else
(ADR-011). This class is the v1 branch: everything the four production sites
call answers under it.

It holds the three routes that speak about the service — ``health``, ``status``
and ``metrics`` — and mounts the entity groups as branches of its own, so the
v1 addresses come out as ``/mailproxy/v1/tenant/{id}``,
``/mailproxy/v1/commands/run-now`` and so on.

``health`` appears twice on purpose: here, because the Genropy client polls
``proxy_url + /health`` and ``proxy_url`` carries the version; and on the
application, because the container's liveness probe must not depend on which
contract versions are mounted.

The groups are declared as FACTORIES, not instances, and that is load-bearing.
A genro-routes router inherits its parent's plugins at the moment it is
attached, so a subtree must be built TOP-DOWN: attach, then populate. This
class populates itself in its own constructor, which is bottom-up — an
instance declared here would be attached while this class still has no parent
and no plugins, and would inherit none. The ``auth`` plugin is among them, so
every route gate under it would be silently gone. A factory is materialized on
first traversal, when the chain up to the application is already complete, so
the order cannot be got wrong from here.
"""

from __future__ import annotations

from typing import Any

from genro_routes import route

from ..auth_tags import TENANT_TAG
from ..http_schema import StatusResponse
from .account import AccountRoutes, AccountsRoutes
from .base import ProxyRoutes
from .command import CommandsRoutes
from .command_log import CommandLogRoutes
from .instance import InstanceRoutes
from .message import MessagesRoutes
from .tenant import TenantRoutes, TenantsRoutes

__all__ = ["METRICS_MEDIA_TYPE", "V1Routes"]

METRICS_MEDIA_TYPE = "text/plain; version=0.0.4"


class V1Routes(ProxyRoutes):
    """The v1 branch: the 26 routes of the contract the production sites call."""

    def __init__(self, application: Any) -> None:
        super().__init__(application)
        groups = {
            "tenant": TenantRoutes,
            "tenants": TenantsRoutes,
            "account": AccountRoutes,
            "accounts": AccountsRoutes,
            "messages": MessagesRoutes,
            "commands": CommandsRoutes,
            "instance": InstanceRoutes,
            "command-log": CommandLogRoutes,
        }
        self.route.add_branches(
            {"name": name, "cls": cls, "params": {"application": application}}
            for name, cls in groups.items()
        )

    @route()
    async def health(self) -> dict[str, str]:
        """Liveness probe, no auth: the v1 body at the v1 address."""
        return {"status": "ok"}

    @route(auth_rule=TENANT_TAG)
    async def status(self, **kwargs: Any) -> dict[str, Any]:
        """Authenticated probe: the token is valid, and dispatch is running or not."""
        return StatusResponse(ok=True, active=self.core._active).model_dump(
            exclude_none=True
        )

    @route(media_type=METRICS_MEDIA_TYPE)
    async def metrics(self, **kwargs: Any) -> bytes:
        """Prometheus exposition of the engine's metrics, no auth as in v1."""
        return self.core.metrics.generate_latest()
