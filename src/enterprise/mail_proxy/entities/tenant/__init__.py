# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise tenant entity with multi-tenant management.

This package extends the core tenants table with full multi-tenant
support including tenant CRUD operations and API key management.

Components:
    TenantsTable_EE: Mixin adding tenant management methods.
    TenantEndpoint_EE: Mixin adding tenant management API.

Example:
    Create a new tenant with API key::

        api_key = await db.table("tenants").add({
            "id": "acme",
            "name": "ACME Corporation",
            "client_base_url": "https://acme.example.com",
        })
        # api_key is shown once, store it securely

Note:
    In EE mode, multiple tenants can coexist with isolated configurations.
    Each tenant has its own API key for scoped authentication.
"""

from .endpoint_ee import TenantEndpoint_EE
from .table_ee import TenantsTable_EE

__all__ = ["TenantEndpoint_EE", "TenantsTable_EE"]
