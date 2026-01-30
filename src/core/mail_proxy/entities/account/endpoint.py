# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Account endpoint: CRUD operations for SMTP accounts.

Designed for introspection by api_base/cli_base to auto-generate routes/commands.
Schema is derived from method signatures via inspect + pydantic.create_model.
"""

from __future__ import annotations

from typing import Literal, TYPE_CHECKING

from ...interface.endpoint_base import BaseEndpoint

if TYPE_CHECKING:
    from .table import AccountsTable


class AccountEndpoint(BaseEndpoint):
    """Account management endpoint. Methods are introspected for API/CLI generation."""

    name = "accounts"

    def __init__(self, table: AccountsTable):
        super().__init__(table)

    async def add(
        self,
        id: str,
        tenant_id: str,
        host: str,
        port: int,
        user: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        batch_size: int | None = None,
        ttl: int = 300,
        limit_per_minute: int | None = None,
        limit_per_hour: int | None = None,
        limit_per_day: int | None = None,
        limit_behavior: Literal["defer", "reject"] = "defer",
    ) -> dict:
        """Add or update an SMTP account."""
        data = {k: v for k, v in locals().items() if k != "self"}
        await self.table.add(data)
        return await self.table.get(tenant_id, id)

    async def get(self, tenant_id: str, account_id: str) -> dict:
        """Get a single SMTP account."""
        return await self.table.get(tenant_id, account_id)

    async def list(self, tenant_id: str) -> list[dict]:
        """List all SMTP accounts for a tenant."""
        return await self.table.list_all(tenant_id=tenant_id)

    async def delete(self, tenant_id: str, account_id: str) -> None:
        """Delete an SMTP account."""
        await self.table.remove(tenant_id, account_id)


__all__ = ["AccountEndpoint"]
