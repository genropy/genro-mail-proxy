# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tenant entity: multi-tenant configuration."""

from .endpoint import (
    AuthMethod,
    DEFAULT_ATTACHMENT_PATH,
    DEFAULT_SYNC_PATH,
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
