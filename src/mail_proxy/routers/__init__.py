# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The v1 route groups mounted on ``MailProxyApplication``.

``V1Routes`` is the branch the application mounts for the v1 contract; the
entity groups under it are one ``RoutingClass`` per path segment, each built
with the application it serves. The segment a group answers is decided where it
is mounted, not here.
"""

from __future__ import annotations

from .account import AccountRoutes, AccountsRoutes
from .base import ProxyRoutes
from .command import CommandsRoutes
from .command_log import CommandLogRoutes
from .instance import InstanceRoutes
from .message import MessagesRoutes
from .tenant import TenantRoutes, TenantsRoutes
from .v1 import V1Routes

__all__ = [
    "AccountRoutes",
    "AccountsRoutes",
    "CommandLogRoutes",
    "CommandsRoutes",
    "InstanceRoutes",
    "MessagesRoutes",
    "ProxyRoutes",
    "TenantRoutes",
    "TenantsRoutes",
    "V1Routes",
]
