# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The names the v1 authentication is written in.

Kept in a module of their own because both sides need them and neither can
import the other: ``MailProxyApplication`` resolves a token into an ``Avatar``
carrying these tags, and every route group declares its gate with them.

``ADMIN_TAG`` is the global token's tag, ``TENANT_TAG`` the tag a tenant api
key carries — the admin avatar carries both, so a route gated on ``TENANT_TAG``
serves the admin too. ``ADMIN_IDENTITY`` is the admin avatar's identity, the one
value a tenant id can never equal.
"""

from __future__ import annotations

__all__ = ["ADMIN_IDENTITY", "ADMIN_TAG", "API_TOKEN_HEADER", "TENANT_TAG"]

API_TOKEN_HEADER = "x-api-token"
ADMIN_IDENTITY = "admin"
ADMIN_TAG = "ADMIN"
TENANT_TAG = "TENANT"
