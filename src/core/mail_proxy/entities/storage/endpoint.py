# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Storage endpoint: CRUD operations for tenant storage backends.

Designed for introspection by api_base/cli_base to auto-generate routes/commands.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ...interface.endpoint_base import POST, BaseEndpoint

if TYPE_CHECKING:
    from .table import StoragesTable


class StorageEndpoint(BaseEndpoint):
    """Storage management endpoint. Methods are introspected for API/CLI generation."""

    name = "storages"

    def __init__(self, table: StoragesTable):
        super().__init__(table)

    @POST
    async def add(
        self,
        tenant_id: str,
        name: str,
        protocol: str,
        config: dict[str, Any] | None = None,
    ) -> dict:
        """Add or update a storage backend for a tenant.

        Args:
            tenant_id: The tenant ID.
            name: Storage name (e.g., "HOME", "SALES").
            protocol: Storage protocol (local, s3, gcs, azure).
            config: Protocol-specific configuration.

        For local protocol:
            config: {"base_path": "/data/attachments"}

        For S3 protocol (EE only):
            config: {"bucket": "my-bucket", "prefix": "attachments/",
                    "aws_access_key_id": "...", "aws_secret_access_key": "..."}

        For GCS protocol (EE only):
            config: {"bucket": "my-bucket", "prefix": "attachments/",
                    "project": "...", "token": "..."}

        For Azure protocol (EE only):
            config: {"container": "my-container", "prefix": "attachments/",
                    "account_name": "...", "account_key": "..."}
        """
        data = {
            "tenant_id": tenant_id,
            "name": name,
            "protocol": protocol,
            "config": config or {},
        }
        await self.table.add(data)
        return await self.table.get(tenant_id, name)

    async def get(self, tenant_id: str, name: str) -> dict:
        """Get a single storage configuration."""
        return await self.table.get(tenant_id, name)

    async def list(self, tenant_id: str) -> list[dict]:
        """List all storage backends for a tenant."""
        return await self.table.list_all(tenant_id=tenant_id)

    @POST
    async def delete(self, tenant_id: str, name: str) -> dict:
        """Delete a storage backend."""
        deleted = await self.table.remove(tenant_id, name)
        return {"ok": deleted, "tenant_id": tenant_id, "name": name}


__all__ = ["StorageEndpoint"]
