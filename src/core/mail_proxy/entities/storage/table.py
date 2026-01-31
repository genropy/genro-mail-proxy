# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Storages table: per-tenant storage backend configurations."""

from __future__ import annotations

from typing import Any

from genro_toolbox import get_uuid

from sql import String, Table, Timestamp


class StoragesTable(Table):
    """Storages table: named storage backends per tenant.

    Each tenant can have multiple named storage backends (e.g., HOME, SALES, ARCHIVE).
    CE supports only local filesystem; EE adds S3, GCS, Azure via fsspec.

    Schema: pk (UUID), tenant_id, name (unique per tenant), protocol, config (JSON).
    """

    name = "storages"
    pkey = "pk"

    def create_table_sql(self) -> str:
        """Generate CREATE TABLE with UNIQUE (tenant_id, name)."""
        sql = super().create_table_sql()
        last_paren = sql.rfind(")")
        return sql[:last_paren] + ',\n    UNIQUE ("tenant_id", "name")\n)'

    def configure(self) -> None:
        c = self.columns
        c.column("pk", String)
        c.column("tenant_id", String, nullable=False).relation("tenants", sql=True)
        c.column("name", String, nullable=False)  # e.g., "HOME", "SALES"
        c.column("protocol", String, nullable=False)  # local, s3, gcs, azure
        c.column("config", String, json_encoded=True, encrypted=True)  # protocol-specific config
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")

    async def add(self, storage: dict[str, Any]) -> str:
        """Insert or update a storage configuration.

        Args:
            storage: Dict with tenant_id, name, protocol, and protocol-specific config.

        Returns:
            The storage's internal pk (UUID).
        """
        tenant_id = storage["tenant_id"]
        name = storage["name"]
        protocol = storage["protocol"]

        # Validate protocol in CE (only local allowed)
        if protocol != "local":
            # Check if EE is available
            if not self._is_ee_available():
                raise ValueError(
                    f"Protocol '{protocol}' requires Enterprise Edition. "
                    "Only 'local' protocol is available in CE."
                )

        async with self.record(
            {"tenant_id": tenant_id, "name": name},
            insert_missing=True,
        ) as rec:
            if "pk" not in rec:
                rec["pk"] = get_uuid()

            rec["protocol"] = protocol
            rec["config"] = storage.get("config", {})
            pk = rec["pk"]

        return pk

    def _is_ee_available(self) -> bool:
        """Check if Enterprise Edition is available."""
        try:
            from enterprise.mail_proxy import is_ee_enabled

            return is_ee_enabled()
        except ImportError:
            return False

    async def get(self, tenant_id: str, name: str) -> dict[str, Any]:
        """Fetch a single storage configuration.

        Args:
            tenant_id: The tenant that owns this storage.
            name: The storage name (e.g., "HOME").

        Raises:
            ValueError: If storage not found for this tenant.
        """
        storage = await self.select_one(where={"tenant_id": tenant_id, "name": name})
        if not storage:
            raise ValueError(f"Storage '{name}' not found for tenant '{tenant_id}'")
        return storage

    async def list_all(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Return storages, optionally filtered by tenant."""
        if tenant_id:
            return await self.select(where={"tenant_id": tenant_id}, order_by="name")
        return await self.select(order_by="name")

    async def remove(self, tenant_id: str, name: str) -> bool:
        """Remove a storage configuration.

        Args:
            tenant_id: The tenant that owns this storage.
            name: The storage name.

        Returns:
            True if deleted, False if not found.
        """
        result = await self.delete(where={"tenant_id": tenant_id, "name": name})
        return result > 0

    async def get_storage_manager(self, tenant_id: str):
        """Get a configured StorageManager for a tenant.

        Returns a StorageManager with all tenant's storages registered.
        """
        from storage import StorageManager

        storages = await self.list_all(tenant_id=tenant_id)
        manager = StorageManager()

        for s in storages:
            config = s.get("config", {})
            config["protocol"] = s["protocol"]
            manager.register(s["name"], config)

        return manager


__all__ = ["StoragesTable"]
