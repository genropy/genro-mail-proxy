# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition extensions for TenantsTable.

This module adds multi-tenant management functionality to the base TenantsTable.
In EE mode, multiple tenants can be created with isolated configurations
and API key authentication.

Multi-tenant features:
- Tenant CRUD operations (add, list, update, remove)
- Tenant API key management for scoped authentication

Note: Batch suspension is available in CE (core/tenant/table.py).

Usage:
    class TenantsTable(TenantsTable_EE, TenantsTableBase):
        pass
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any


class TenantsTable_EE:
    """Enterprise Edition: Multi-tenant management.

    Adds methods for:
    - Creating and managing multiple tenants
    - Tenant API key authentication
    """

    async def add(self, tenant: dict[str, Any]) -> str:
        """Insert or update a tenant configuration.

        For new tenants, automatically generates an API key.
        For existing tenants, keeps the existing API key.

        Args:
            tenant: Tenant configuration dict with at least 'id' field.
                Optional fields: name, client_auth, client_base_url,
                client_sync_path, client_attachment_path, rate_limits,
                large_file_config, active.

        Returns:
            The API key (raw, show once) for new tenants.
            Empty string for existing tenants (key unchanged).
        """
        tenant_id = tenant["id"]

        # Check if tenant exists
        existing = await self.get(tenant_id)  # type: ignore[attr-defined]

        if existing:
            # Update existing tenant - don't change API key
            async with self.record(tenant_id) as rec:  # type: ignore[attr-defined]
                rec["name"] = tenant.get("name")
                rec["client_auth"] = tenant.get("client_auth")
                rec["client_base_url"] = tenant.get("client_base_url")
                rec["client_sync_path"] = tenant.get("client_sync_path")
                rec["client_attachment_path"] = tenant.get("client_attachment_path")
                rec["rate_limits"] = tenant.get("rate_limits")
                rec["large_file_config"] = tenant.get("large_file_config")
                rec["active"] = 1 if tenant.get("active", True) else 0
            return ""  # Key unchanged

        # New tenant - generate API key
        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        await self.insert(
            {  # type: ignore[attr-defined]
                "id": tenant_id,
                "name": tenant.get("name"),
                "client_auth": tenant.get("client_auth"),
                "client_base_url": tenant.get("client_base_url"),
                "client_sync_path": tenant.get("client_sync_path"),
                "client_attachment_path": tenant.get("client_attachment_path"),
                "rate_limits": tenant.get("rate_limits"),
                "large_file_config": tenant.get("large_file_config"),
                "active": 1 if tenant.get("active", True) else 0,
                "api_key_hash": key_hash,
            }
        )
        return raw_key

    async def list_all(self, active_only: bool = False) -> list[dict[str, Any]]:
        """Return all tenants, optionally filtered by active status.

        Args:
            active_only: If True, return only active tenants.

        Returns:
            List of tenant dicts with decoded fields.
        """
        if active_only:
            rows = await self.fetch_all(  # type: ignore[attr-defined]
                "SELECT * FROM tenants WHERE active = 1 ORDER BY id"
            )
        else:
            rows = await self.select(order_by="id")  # type: ignore[attr-defined]
        return [self._decode_active(row) for row in rows]  # type: ignore[attr-defined]

    async def update_fields(self, tenant_id: str, updates: dict[str, Any]) -> bool:
        """Update a tenant's fields.

        Args:
            tenant_id: The tenant to update.
            updates: Dict of fields to update. Supported fields:
                name, client_auth, client_base_url, client_sync_path,
                client_attachment_path, rate_limits, large_file_config, active.

        Returns:
            True if row was updated, False if no valid updates or tenant not found.
        """
        if not updates:
            return False

        async with self.record(tenant_id) as rec:  # type: ignore[attr-defined]
            if not rec:
                return False
            for key, value in updates.items():
                if key in ("client_auth", "rate_limits", "large_file_config"):
                    rec[key] = value  # Will be JSON-encoded by Table.update()
                elif key == "active":
                    rec["active"] = 1 if value else 0
                elif key in (
                    "name",
                    "client_base_url",
                    "client_sync_path",
                    "client_attachment_path",
                ):
                    rec[key] = value
        return True

    async def remove(self, tenant_id: str) -> bool:
        """Delete a tenant and cascade to related accounts and messages.

        Warning: This deletes ALL data associated with the tenant including:
        - All messages in the queue
        - All SMTP account configurations
        - The tenant configuration itself

        Args:
            tenant_id: The tenant to delete.

        Returns:
            True if tenant was deleted.
        """
        # Delete messages for this tenant
        await self.db.adapter.execute(  # type: ignore[attr-defined]
            "DELETE FROM messages WHERE tenant_id = :tenant_id", {"tenant_id": tenant_id}
        )
        # Delete accounts for this tenant
        await self.db.adapter.execute(  # type: ignore[attr-defined]
            "DELETE FROM accounts WHERE tenant_id = :tenant_id", {"tenant_id": tenant_id}
        )
        # Delete the tenant
        rowcount = await self.delete(where={"id": tenant_id})  # type: ignore[attr-defined]
        return rowcount > 0

    # ----------------------------------------------------------------- API Keys

    async def create_api_key(self, tenant_id: str, expires_at: int | None = None) -> str | None:
        """Create a new API key for a tenant.

        Generates a new random API key, replacing any existing key.
        The raw key is returned once and cannot be retrieved later.

        Args:
            tenant_id: The tenant ID.
            expires_at: Optional Unix timestamp for key expiration.

        Returns:
            The raw API key (show once), or None if tenant not found.
        """
        tenant = await self.get(tenant_id)  # type: ignore[attr-defined]
        if not tenant:
            return None

        raw_key = secrets.token_urlsafe(32)
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()

        await self.execute(  # type: ignore[attr-defined]
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

        Looks up the tenant associated with the given API key.
        Validates that the key has not expired.

        Args:
            raw_key: The raw API key to look up.

        Returns:
            Tenant dict if found and not expired, None otherwise.
        """
        key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
        tenant = await self.fetch_one(  # type: ignore[attr-defined]
            "SELECT * FROM tenants WHERE api_key_hash = :key_hash",
            {"key_hash": key_hash},
        )
        if not tenant:
            return None

        expires_at = tenant.get("api_key_expires_at")
        if expires_at:
            # Handle both datetime (PostgreSQL) and int (SQLite) types
            if isinstance(expires_at, datetime):
                now = datetime.now(timezone.utc)
                # Make expires_at timezone-aware if it isn't
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                if expires_at < now:
                    return None  # Expired
            else:
                # SQLite returns int (Unix timestamp)
                now_ts = datetime.now(timezone.utc).timestamp()
                if expires_at < now_ts:
                    return None  # Expired

        return self._decode_active(tenant)  # type: ignore[attr-defined]

    async def revoke_api_key(self, tenant_id: str) -> bool:
        """Revoke the API key for a tenant.

        Removes the API key, preventing further authentication.
        The tenant can still be accessed via instance token.

        Args:
            tenant_id: The tenant ID.

        Returns:
            True if key was revoked, False if tenant not found.
        """
        rowcount = await self.execute(  # type: ignore[attr-defined]
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


__all__ = ["TenantsTable_EE"]
