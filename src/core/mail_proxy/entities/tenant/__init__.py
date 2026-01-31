# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tenant entity module for multi-tenant configuration.

This module provides the Tenant entity for managing tenant configurations
in a multi-tenant mail proxy environment.

Components:
    TenantsTable: Database table manager for tenant storage.
    TenantEndpoint: REST API endpoint for CRUD operations.
    AuthMethod: Enum for HTTP authentication methods.
    LargeFileAction: Enum for large attachment handling.
    get_tenant_sync_url: Helper to build sync callback URL.
    get_tenant_attachment_url: Helper to build attachment URL.

Tenant Configuration:
    - Client callbacks: base_url, sync_path, attachment_path, auth
    - Rate limits: per_minute, per_hour, per_day
    - Large files: threshold, action (warn/reject/rewrite)
    - Batch suspension: pause specific campaigns

Example:
    Manage tenants::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        # Table access
        tenants = proxy.db.table("tenants")
        await tenants.ensure_default()
        tenant = await tenants.get("default")

        # Endpoint access (for API/CLI)
        from core.mail_proxy.entities.tenant import TenantEndpoint
        endpoint = TenantEndpoint(tenants)
        await endpoint.suspend_batch("default", "newsletter-q1")

        # Build callback URLs
        from core.mail_proxy.entities.tenant import get_tenant_sync_url
        url = get_tenant_sync_url(tenant)

Note:
    Enterprise Edition (EE) extends both classes with full multi-tenant
    support via TenantsTable_EE and TenantEndpoint_EE mixins, adding
    API key management and tenant CRUD operations.
"""

from .endpoint import (
    DEFAULT_ATTACHMENT_PATH,
    DEFAULT_SYNC_PATH,
    AuthMethod,
    LargeFileAction,
    TenantEndpoint,
    get_tenant_attachment_url,
    get_tenant_sync_url,
)
from .table import TenantsTable

__all__ = [
    "AuthMethod",
    "DEFAULT_ATTACHMENT_PATH",
    "DEFAULT_SYNC_PATH",
    "LargeFileAction",
    "TenantEndpoint",
    "TenantsTable",
    "get_tenant_attachment_url",
    "get_tenant_sync_url",
]
