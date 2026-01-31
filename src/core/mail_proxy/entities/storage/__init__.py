# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Storage entity: per-tenant storage backend configurations."""

from .table import StoragesTable
from .endpoint import StorageEndpoint

__all__ = ["StoragesTable", "StorageEndpoint"]
