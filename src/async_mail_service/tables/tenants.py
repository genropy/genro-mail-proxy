# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tenants table manager with JSON field handling."""

from __future__ import annotations

from typing import Any

from ..sql import Integer, String, Table, Timestamp


class TenantsTable(Table):
    """Tenants table: multi-tenant configuration storage.

    JSON-encoded fields: client_auth, rate_limits, large_file_config.
    Boolean field: active (stored as INTEGER 0/1).
    """

    name = "tenants"

    def configure(self) -> None:
        c = self.columns
        c.column("id", String, primary_key=True)
        c.column("name", String)
        c.column("client_auth", String, json_encoded=True)
        c.column("client_base_url", String)
        c.column("client_sync_path", String)
        c.column("client_attachment_path", String)
        c.column("rate_limits", String, json_encoded=True)
        c.column("large_file_config", String, json_encoded=True)
        c.column("active", Integer, default=1)
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")

    async def add(self, tenant: dict[str, Any]) -> None:
        """Insert or update a tenant configuration."""
        await self.upsert(
            {
                "id": tenant["id"],
                "name": tenant.get("name"),
                "client_auth": tenant.get("client_auth"),
                "client_base_url": tenant.get("client_base_url"),
                "client_sync_path": tenant.get("client_sync_path"),
                "client_attachment_path": tenant.get("client_attachment_path"),
                "rate_limits": tenant.get("rate_limits"),
                "large_file_config": tenant.get("large_file_config"),
                "active": 1 if tenant.get("active", True) else 0,
            },
            conflict_columns=["id"],
            update_extras=["updated_at = CURRENT_TIMESTAMP"],
        )

    async def get(self, tenant_id: str) -> dict[str, Any] | None:
        """Fetch a tenant configuration by ID."""
        tenant = await self.select_one(where={"id": tenant_id})
        if not tenant:
            return None
        return self._decode_active(tenant)

    async def list_all(self, active_only: bool = False) -> list[dict[str, Any]]:
        """Return all tenants, optionally filtered by active status."""
        if active_only:
            rows = await self.fetch_all(
                "SELECT * FROM tenants WHERE active = 1 ORDER BY id"
            )
        else:
            rows = await self.select(order_by="id")
        return [self._decode_active(row) for row in rows]

    async def update_fields(self, tenant_id: str, updates: dict[str, Any]) -> bool:
        """Update a tenant's fields. Returns True if row was updated."""
        if not updates:
            return False

        values: dict[str, Any] = {}
        for key, value in updates.items():
            if key in ("client_auth", "rate_limits", "large_file_config"):
                values[key] = value  # Will be JSON-encoded by Table.update()
            elif key == "active":
                values["active"] = 1 if value else 0
            elif key in ("name", "client_base_url", "client_sync_path", "client_attachment_path"):
                values[key] = value

        if not values:
            return False

        # Add updated_at via raw query to use CURRENT_TIMESTAMP
        set_parts = [f"{k} = :val_{k}" for k in values]
        set_parts.append("updated_at = CURRENT_TIMESTAMP")
        params = {f"val_{k}": v for k, v in self._encode_json_fields(values).items()}
        params["tenant_id"] = tenant_id

        rowcount = await self.execute(
            f"UPDATE tenants SET {', '.join(set_parts)} WHERE id = :tenant_id",
            params,
        )
        return rowcount > 0

    async def remove(self, tenant_id: str) -> bool:
        """Delete a tenant. Returns True if deleted.

        Note: Cascading deletes of accounts/messages should be handled
        by the calling code or foreign key constraints.
        """
        rowcount = await self.delete(where={"id": tenant_id})
        return rowcount > 0

    async def get_for_account(self, account_id: str) -> dict[str, Any] | None:
        """Get the tenant configuration for a given account."""
        tenant = await self.fetch_one(
            """
            SELECT t.* FROM tenants t
            JOIN accounts a ON a.tenant_id = t.id
            WHERE a.id = :account_id
            """,
            {"account_id": account_id},
        )
        if not tenant:
            return None
        return self._decode_active(tenant)

    def _decode_active(self, tenant: dict[str, Any]) -> dict[str, Any]:
        """Convert active INTEGER to bool."""
        tenant["active"] = bool(tenant.get("active", 1))
        return tenant


__all__ = ["TenantsTable"]
