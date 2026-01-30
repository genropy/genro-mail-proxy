# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tenants table manager with JSON field handling.

CE (Core Edition): Single-tenant mode with implicit 'default' tenant.
EE (Enterprise): Extends with multi-tenant management via TenantsTable_EE mixin.
"""

from __future__ import annotations

from typing import Any

from sql import Integer, String, Table, Timestamp


class TenantsTable(Table):
    """Tenants table: tenant configuration storage (CE base).

    CE provides: get(), is_batch_suspended(), ensure_default().
    EE extends with: add(), list_all(), update_fields(), remove(),
                     API key management, batch suspension control.

    Schema: id (PK), name, client_auth (JSON), rate_limits (JSON),
            active (0/1), suspended_batches, api_key_hash, timestamps.
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

    async def get(self, tenant_id: str) -> dict[str, Any] | None:
        """Fetch a tenant configuration by ID."""
        tenant = await self.select_one(where={"id": tenant_id})
        if not tenant:
            return None
        return self._decode_active(tenant)

    def _decode_active(self, tenant: dict[str, Any]) -> dict[str, Any]:
        """Convert active INTEGER to bool."""
        tenant["active"] = bool(tenant.get("active", 1))
        return tenant

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

    async def ensure_default(self) -> None:
        """Ensure the 'default' tenant exists for CE single-tenant mode.

        Creates the default tenant WITHOUT an API key. In CE mode, all operations
        use the instance token. When upgrading to EE, the admin can generate
        a tenant token via POST /tenant/default/api-key.
        """
        existing = await self.get("default")
        if existing:
            return

        # Create without API key - CE uses instance token only
        await self.insert({
            "id": "default",
            "name": "Default Tenant",
            "active": 1,
        })

    # ----------------------------------------------------------------- Batch Suspension

    async def suspend_batch(self, tenant_id: str, batch_code: str | None = None) -> bool:
        """Suspend sending for a tenant, optionally for a specific batch only.

        Suspended batches are skipped by the dispatcher. Use this for:
        - Pausing a campaign (specific batch_code)
        - Emergency stop for a tenant (batch_code=None suspends all)

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

        Removes the batch from the suspension list. If batch_code is None,
        clears ALL suspensions for the tenant.

        Note: Cannot remove a single batch when full suspension ("*") is active.
        Must activate_batch(None) first to clear full suspension.

        Args:
            tenant_id: The tenant ID.
            batch_code: Optional batch code. If None, clears all suspensions.

        Returns:
            True if tenant was found and updated.
            False if tenant not found or trying to remove single batch from "*".
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

        Args:
            tenant_id: The tenant to query.

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


__all__ = ["TenantsTable"]
