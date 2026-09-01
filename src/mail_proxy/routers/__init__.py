# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The v1 route groups mounted on ``MailProxyApplication``.

One ``RoutingClass`` per root path segment of the contract, each built with the
application it serves and attached as a branch of the app router. The segment a
group answers is decided where it is mounted, not here.
"""

from __future__ import annotations

from .account import AccountRoutes, AccountsRoutes
from .base import ProxyRoutes
from .command import CommandsRoutes
from .command_log import CommandLogRoutes
from .instance import InstanceRoutes
from .message import MessagesRoutes
from .tenant import TenantRoutes, TenantsRoutes

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
]
