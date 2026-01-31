# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Storage entity: per-tenant storage backend configurations."""

from .endpoint import StorageEndpoint
from .table import StoragesTable

__all__ = ["StoragesTable", "StorageEndpoint"]
