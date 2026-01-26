# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Tenants table manager with JSON field handling."""

from __future__ import annotations

from typing import Any

from ...sql import Integer, String, Table, Timestamp


class TenantsTable(Table):
    """Tenants table: multi-tenant configuration storage.

    JSON-encoded fields: client_auth, rate_limits, large_file_config.
    Boolean field: active (stored as INTEGER 0/1).
    Suspension: suspended_batches contains comma-separated batch codes or "*" for all.
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
        c.column("suspended_batches", String)  # Comma-separated batch codes or "*" for all
        c.column("api_key_hash", String)
        c.column("api_key_expires_at", Timestamp)
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

    # ----------------------------------------------------------------- API Keys

    async def create_api_key(
        self, tenant_id: str, expires_at: int | None = None
    ) -> str | None:
        """Create a new API key for a tenant.

        Args:
            tenant_id: The tenant ID.
            expires_at: Optional Unix timestamp for key expiration.

        Returns:
            The raw API key (show once), or None if tenant not found.
        """
        import hashlib
        import secrets

        tenant = await self.get(tenant_id)
        if not tenant:
            return None

        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        await self.execute(
            """
            UPDATE tenants
            SET api_key_hash = :key_hash,
                api_key_expires_at = :expires_at,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :tenant_id
            """,
            {"tenant_id": tenant_id, "key_hash": key_hash, "expires_at": expires_at},
        )
        return raw_key

    async def get_tenant_by_token(self, raw_key: str) -> dict[str, Any] | None:
        """Find tenant by API key token.

        Args:
            raw_key: The raw API key to look up.

        Returns:
            Tenant dict if found and not expired, None otherwise.
        """
        import hashlib
        import time

        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        tenant = await self.fetch_one(
            "SELECT * FROM tenants WHERE api_key_hash = :key_hash",
            {"key_hash": key_hash},
        )
        if not tenant:
            return None

        expires_at = tenant.get("api_key_expires_at")
        if expires_at and expires_at < time.time():
            return None  # Expired

        return self._decode_active(tenant)

    async def revoke_api_key(self, tenant_id: str) -> bool:
        """Revoke the API key for a tenant.

        Args:
            tenant_id: The tenant ID.

        Returns:
            True if key was revoked, False if tenant not found.
        """
        rowcount = await self.execute(
            """
            UPDATE tenants
            SET api_key_hash = NULL,
                api_key_expires_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :tenant_id
            """,
            {"tenant_id": tenant_id},
        )
        return rowcount > 0

    # -------------------------------------------------------------- Batch Suspension

    async def suspend_batch(self, tenant_id: str, batch_code: str | None = None) -> bool:
        """Suspend sending for a tenant, optionally for a specific batch only.

        Args:
            tenant_id: The tenant ID.
            batch_code: Optional batch code. If None, suspends all sending ("*").

        Returns:
            True if tenant was found and updated.
        """
        tenant = await self.get(tenant_id)
        if not tenant:
            return False

        if batch_code is None:
            # Suspend all
            new_value = "*"
        else:
            # Add batch to suspended list
            current = tenant.get("suspended_batches") or ""
            if current == "*":
                # Already fully suspended
                return True
            batches = set(current.split(",")) if current else set()
            batches.discard("")  # Remove empty string if present
            batches.add(batch_code)
            new_value = ",".join(sorted(batches))

        await self.execute(
            """
            UPDATE tenants
            SET suspended_batches = :suspended_batches,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :tenant_id
            """,
            {"tenant_id": tenant_id, "suspended_batches": new_value},
        )
        return True

    async def activate_batch(self, tenant_id: str, batch_code: str | None = None) -> bool:
        """Resume sending for a tenant, optionally for a specific batch only.

        Args:
            tenant_id: The tenant ID.
            batch_code: Optional batch code. If None, clears all suspensions.

        Returns:
            True if tenant was found and updated.
        """
        tenant = await self.get(tenant_id)
        if not tenant:
            return False

        if batch_code is None:
            # Clear all suspensions
            new_value = None
        else:
            # Remove batch from suspended list
            current = tenant.get("suspended_batches") or ""
            if current == "*":
                # Cannot remove single batch from full suspension
                # User must activate all first
                return False
            batches = set(current.split(",")) if current else set()
            batches.discard("")
            batches.discard(batch_code)
            new_value = ",".join(sorted(batches)) if batches else None

        await self.execute(
            """
            UPDATE tenants
            SET suspended_batches = :suspended_batches,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = :tenant_id
            """,
            {"tenant_id": tenant_id, "suspended_batches": new_value},
        )
        return True

    async def get_suspended_batches(self, tenant_id: str) -> set[str]:
        """Get the set of suspended batch codes for a tenant.

        Returns:
            Set of batch codes, or {"*"} if all suspended, or empty set if none.
        """
        tenant = await self.get(tenant_id)
        if not tenant:
            return set()

        suspended = tenant.get("suspended_batches") or ""
        if not suspended:
            return set()
        if suspended == "*":
            return {"*"}
        batches = set(suspended.split(","))
        batches.discard("")
        return batches

    def is_batch_suspended(self, suspended_batches: str | None, batch_code: str | None) -> bool:
        """Check if a batch is suspended based on tenant's suspended_batches field.

        Args:
            suspended_batches: The tenant's suspended_batches value.
            batch_code: The message's batch_code (None if no batch).

        Returns:
            True if the message should be skipped.
        """
        if not suspended_batches:
            return False
        if suspended_batches == "*":
            return True
        if batch_code is None:
            # Messages without batch_code are only suspended by "*"
            return False
        suspended_set = set(suspended_batches.split(","))
        return batch_code in suspended_set


__all__ = ["TenantsTable"]
