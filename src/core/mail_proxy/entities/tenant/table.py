# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tenant configuration table manager.

This module provides the TenantsTable class for managing tenant
configurations in a multi-tenant mail proxy environment.

In Community Edition (CE), a single "default" tenant is used implicitly.
Enterprise Edition (EE) extends with full multi-tenant management via
TenantsTable_EE mixin, adding API key authentication and tenant CRUD.

Each tenant can configure:
    - Client authentication (for callback URLs)
    - Rate limits (per minute/hour/day)
    - Large file handling (warn/reject/rewrite)
    - Batch suspension (pause specific campaigns)

Example:
    Basic tenant operations::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        tenants = proxy.db.table("tenants")

        # Ensure default tenant exists (CE mode)
        await tenants.ensure_default()

        # Get tenant config
        tenant = await tenants.get("default")

        # Suspend a batch
        await tenants.suspend_batch("default", "newsletter-q1")

        # Activate all batches
        await tenants.activate_batch("default")

Note:
    Enterprise Edition (EE) extends this class with TenantsTable_EE
    mixin, adding: add(), list_all(), update_fields(), remove(),
    create_api_key(), revoke_api_key(), get_tenant_by_token().
"""

from __future__ import annotations

from typing import Any

from sql import Integer, String, Table, Timestamp


class TenantsTable(Table):
    """Tenant configuration storage table.

    Manages tenant settings including client authentication, rate limits,
    and batch suspension for campaign control.

    Attributes:
        name: Table name ("tenants").
        pkey: Primary key column ("id").

    Table Schema:
        - id: Tenant identifier (primary key)
        - name: Display name
        - client_auth: JSON dict with HTTP auth config for callbacks
        - client_base_url: Base URL for client callbacks
        - client_sync_path: Path for sync endpoint
        - client_attachment_path: Path for attachment endpoint
        - rate_limits: JSON dict with per-minute/hour/day limits
        - large_file_config: JSON dict for large attachment handling
        - active: 0/1 flag for tenant status
        - suspended_batches: Comma-separated batch codes or "*" for all
        - api_key_hash: Hashed API key (EE only)
        - api_key_expires_at: API key expiration (EE only)
        - created_at, updated_at: Timestamps

    Example:
        Work with tenant configuration::

            tenants = proxy.db.table("tenants")

            # Get tenant
            tenant = await tenants.get("acme")

            # Check batch suspension
            is_suspended = tenants.is_batch_suspended(
                tenant["suspended_batches"],
                "campaign-001",
            )

            # Suspend a batch
            await tenants.suspend_batch("acme", "campaign-001")
    """

    name = "tenants"
    pkey = "id"

    def configure(self) -> None:
        """Define table columns.

        Columns:
            id: Tenant identifier (primary key string).
            name: Human-readable tenant name.
            client_auth: JSON dict with auth method, credentials for callbacks.
            client_base_url: Base URL for client HTTP callbacks.
            client_sync_path: Path appended to base_url for sync endpoint.
            client_attachment_path: Path for attachment fetch endpoint.
            rate_limits: JSON dict with per_minute, per_hour, per_day limits.
            large_file_config: JSON dict with threshold, action settings.
            active: 1=active, 0=disabled (INTEGER for SQLite).
            suspended_batches: Comma-separated batch codes or "*" for all.
            api_key_hash: Bcrypt hash of API key (EE only).
            api_key_expires_at: API key expiration timestamp (EE only).
            created_at: Row creation timestamp.
            updated_at: Last modification timestamp.
        """
        c = self.columns
        c.column("id", String)
        c.column("name", String)
        c.column("client_auth", String, json_encoded=True)
        c.column("client_base_url", String)
        c.column("client_sync_path", String)
        c.column("client_attachment_path", String)
        c.column("rate_limits", String, json_encoded=True)
        c.column("large_file_config", String, json_encoded=True)
        c.column("active", Integer, default=1)
        c.column("suspended_batches", String)
        c.column("api_key_hash", String)
        c.column("api_key_expires_at", Timestamp)
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")

    async def get(self, tenant_id: str) -> dict[str, Any] | None:
        """Fetch a tenant configuration by ID.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            Tenant dict with 'active' converted to bool, or None if not found.
        """
        tenant = await self.select_one(where={"id": tenant_id})
        if not tenant:
            return None
        return self._decode_active(tenant)

    def _decode_active(self, tenant: dict[str, Any]) -> dict[str, Any]:
        """Convert active INTEGER to bool.

        Args:
            tenant: Raw tenant dict from database.

        Returns:
            Tenant dict with 'active' as boolean.
        """
        tenant["active"] = bool(tenant.get("active", 1))
        return tenant

    def is_batch_suspended(self, suspended_batches: str | None, batch_code: str | None) -> bool:
        """Check if a batch is suspended.

        Args:
            suspended_batches: Tenant's suspended_batches value (comma-separated or "*").
            batch_code: Message's batch_code (None if no batch).

        Returns:
            True if the message should be skipped.

        Note:
            - "*" suspends all messages regardless of batch_code
            - Messages without batch_code are only suspended by "*"
            - Specific batch codes must match exactly

        Example:
            ::

                # All suspended
                is_batch_suspended("*", "any-batch")  # True
                is_batch_suspended("*", None)  # True

                # Specific batches
                is_batch_suspended("batch1,batch2", "batch1")  # True
                is_batch_suspended("batch1,batch2", "batch3")  # False
                is_batch_suspended("batch1,batch2", None)  # False
        """
        if not suspended_batches:
            return False
        if suspended_batches == "*":
            return True
        if batch_code is None:
            return False
        suspended_set = set(suspended_batches.split(","))
        return batch_code in suspended_set

    async def ensure_default(self) -> None:
        """Ensure the 'default' tenant exists for CE single-tenant mode.

        Creates the default tenant without API key. In CE mode, all
        operations use the instance token. When upgrading to EE, admin
        can generate tenant token via create_api_key().
        """
        async with self.record("default", insert_missing=True) as rec:
            if not rec.get("name"):
                rec["name"] = "Default Tenant"
                rec["active"] = 1

    async def suspend_batch(self, tenant_id: str, batch_code: str | None = None) -> bool:
        """Suspend sending for a tenant.

        Suspended batches are skipped by the dispatcher. Use for:
            - Pausing a campaign (specific batch_code)
            - Emergency stop (batch_code=None suspends all)

        Args:
            tenant_id: Tenant identifier.
            batch_code: Batch to suspend. If None, suspends all ("*").

        Returns:
            True if tenant found and updated, False if not found.

        Example:
            ::

                # Suspend specific batch
                await tenants.suspend_batch("acme", "newsletter-q1")

                # Suspend all sending
                await tenants.suspend_batch("acme")
        """
        async with self.record(tenant_id) as rec:
            if not rec:
                return False

            if batch_code is None:
                rec["suspended_batches"] = "*"
            else:
                current = rec.get("suspended_batches") or ""
                if current == "*":
                    return True
                batches = set(current.split(",")) if current else set()
                batches.discard("")
                batches.add(batch_code)
                rec["suspended_batches"] = ",".join(sorted(batches))

        return True

    async def activate_batch(self, tenant_id: str, batch_code: str | None = None) -> bool:
        """Resume sending for a tenant.

        Removes batch from suspension list. If batch_code is None,
        clears ALL suspensions.

        Args:
            tenant_id: Tenant identifier.
            batch_code: Batch to activate. If None, clears all suspensions.

        Returns:
            True if updated successfully.
            False if tenant not found or cannot remove single batch from "*".

        Note:
            Cannot remove a single batch when full suspension ("*") is active.
            Must call activate_batch(tenant_id, None) first to clear all.

        Example:
            ::

                # Activate specific batch
                await tenants.activate_batch("acme", "newsletter-q1")

                # Clear all suspensions
                await tenants.activate_batch("acme")
        """
        async with self.record(tenant_id) as rec:
            if not rec:
                return False

            if batch_code is None:
                rec["suspended_batches"] = None
            else:
                current = rec.get("suspended_batches") or ""
                if current == "*":
                    return False
                batches = set(current.split(",")) if current else set()
                batches.discard("")
                batches.discard(batch_code)
                rec["suspended_batches"] = ",".join(sorted(batches)) if batches else None

        return True

    async def get_suspended_batches(self, tenant_id: str) -> set[str]:
        """Get suspended batch codes for a tenant.

        Args:
            tenant_id: Tenant identifier.

        Returns:
            Set of batch codes, {"*"} if all suspended, or empty set.

        Example:
            ::

                suspended = await tenants.get_suspended_batches("acme")
                if "*" in suspended:
                    print("All sending suspended")
                elif "campaign-001" in suspended:
                    print("Campaign 001 is suspended")
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
