# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tenant entity: multi-tenant configuration."""

from .schema import (
    AuthMethod,
    LargeFileAction,
    Tenant,
    TenantAuth,
    TenantCreate,
    TenantLargeFileConfig,
    TenantListItem,
    TenantRateLimits,
    TenantSyncAuth,
    TenantUpdate,
    get_tenant_attachment_url,
    get_tenant_sync_url,
)
from .table import TenantsTable

__all__ = [
    "AuthMethod",
    "LargeFileAction",
    "Tenant",
    "TenantAuth",
    "TenantCreate",
    "TenantLargeFileConfig",
    "TenantListItem",
    "TenantRateLimits",
    "TenantSyncAuth",
    "TenantsTable",
    "TenantUpdate",
    "get_tenant_attachment_url",
    "get_tenant_sync_url",
]
