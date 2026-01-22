# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Accounts table manager for SMTP configurations."""

from __future__ import annotations

from typing import Any

from ..sql import Integer, String, Table, Timestamp


class AccountsTable(Table):
    """Accounts table: SMTP server configurations.

    Fields:
    - id: Account identifier
    - tenant_id: Parent tenant (FK)
    - host, port: SMTP server
    - user, password: Authentication
    - ttl: Connection TTL in seconds
    - limit_per_minute/hour/day: Rate limits
    - limit_behavior: "defer" or "reject"
    - use_tls: TLS mode (NULL=auto, 0=off, 1=on)
    - batch_size: Messages per connection
    """

    name = "accounts"

    def configure(self) -> None:
        c = self.columns
        c.column("id", String, primary_key=True)
        c.column("tenant_id", String).relation("tenants", sql=True)
        c.column("host", String, nullable=False)
        c.column("port", Integer, nullable=False)
        c.column("user", String)
        c.column("password", String)
        c.column("ttl", Integer, default=300)
        c.column("limit_per_minute", Integer)
        c.column("limit_per_hour", Integer)
        c.column("limit_per_day", Integer)
        c.column("limit_behavior", String)
        c.column("use_tls", Integer)
        c.column("batch_size", Integer)
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")

    async def add(self, acc: dict[str, Any]) -> None:
        """Insert or update an SMTP account."""
        use_tls = acc.get("use_tls")
        use_tls_val = None if use_tls is None else (1 if use_tls else 0)

        await self.upsert(
            {
                "id": acc["id"],
                "tenant_id": acc.get("tenant_id"),
                "host": acc["host"],
                "port": int(acc["port"]),
                "user": acc.get("user"),
                "password": acc.get("password"),
                "ttl": int(acc.get("ttl", 300)),
                "limit_per_minute": acc.get("limit_per_minute"),
                "limit_per_hour": acc.get("limit_per_hour"),
                "limit_per_day": acc.get("limit_per_day"),
                "limit_behavior": acc.get("limit_behavior", "defer"),
                "use_tls": use_tls_val,
                "batch_size": acc.get("batch_size"),
            },
            conflict_columns=["id"],
            update_extras=["updated_at = CURRENT_TIMESTAMP"],
        )

    async def get(self, account_id: str) -> dict[str, Any]:
        """Fetch a single SMTP account or raise if not found."""
        account = await self.select_one(where={"id": account_id})
        if not account:
            raise ValueError(f"Account '{account_id}' not found")
        return self._decode_use_tls(account)

    async def list_all(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Return SMTP accounts, optionally filtered by tenant."""
        columns = [
            "id", "tenant_id", "host", "port", "user", "ttl",
            "limit_per_minute", "limit_per_hour", "limit_per_day",
            "limit_behavior", "use_tls", "batch_size", "created_at", "updated_at",
        ]

        if tenant_id:
            rows = await self.select(columns=columns, where={"tenant_id": tenant_id}, order_by="id")
        else:
            rows = await self.select(columns=columns, order_by="id")

        return [self._decode_use_tls(acc) for acc in rows]

    async def remove(self, account_id: str) -> None:
        """Remove an SMTP account.

        Note: Related messages and send_log should be cleaned by the calling code
        or via foreign key constraints.
        """
        await self.delete(where={"id": account_id})

    def _decode_use_tls(self, account: dict[str, Any]) -> dict[str, Any]:
        """Convert use_tls INTEGER to bool/None."""
        if "use_tls" in account:
            val = account["use_tls"]
            account["use_tls"] = bool(val) if val is not None else None
        return account


__all__ = ["AccountsTable"]
