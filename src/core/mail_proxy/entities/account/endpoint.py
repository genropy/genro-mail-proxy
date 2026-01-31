# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SMTP account REST API endpoint.

This module provides the AccountEndpoint class exposing CRUD operations
for SMTP account configurations via REST API and CLI commands.

The endpoint is designed for automatic introspection by api_base and
cli_base modules, which generate FastAPI routes and Typer commands
from method signatures using inspect and pydantic.create_model.

Example:
    Register endpoint with the API router::

        from core.mail_proxy.entities.account import AccountEndpoint

        endpoint = AccountEndpoint(proxy.db.table("accounts"))
        # Routes auto-generated: POST /accounts, GET /accounts/{id}, etc.

    CLI commands auto-generated::

        mail-proxy accounts add --tenant-id acme --id main --host smtp.example.com --port 587
        mail-proxy accounts list --tenant-id acme
        mail-proxy accounts get --tenant-id acme --account-id main
        mail-proxy accounts delete --tenant-id acme --account-id main

Note:
    Enterprise Edition (EE) extends this with AccountEndpoint_EE mixin
    adding PEC-specific fields (is_pec_account, imap_* settings).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ...interface.endpoint_base import POST, BaseEndpoint

if TYPE_CHECKING:
    from .table import AccountsTable


class AccountEndpoint(BaseEndpoint):
    """REST API endpoint for SMTP account management.

    Provides CRUD operations for SMTP accounts. Each method is
    introspected to auto-generate API routes and CLI commands.

    Attributes:
        name: Endpoint name used in URL paths ("accounts").
        table: AccountsTable instance for database operations.

    Example:
        Using the endpoint programmatically::

            endpoint = AccountEndpoint(db.table("accounts"))

            # Add account
            account = await endpoint.add(
                id="main",
                tenant_id="acme",
                host="smtp.gmail.com",
                port=587,
                user="sender@acme.com",
                password="app-password",
                use_tls=True,
            )

            # List accounts
            accounts = await endpoint.list(tenant_id="acme")
    """

    name = "accounts"

    def __init__(self, table: AccountsTable):
        """Initialize endpoint with table reference.

        Args:
            table: AccountsTable instance for database operations.
        """
        super().__init__(table)

    @POST
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
        """Add or update an SMTP account configuration.

        Performs upsert: creates new account or updates existing one
        based on (tenant_id, id) composite key.

        Args:
            id: Account identifier (unique within tenant).
            tenant_id: Owning tenant ID.
            host: SMTP server hostname.
            port: SMTP server port (typically 25, 465, or 587).
            user: SMTP username for authentication.
            password: SMTP password (encrypted at rest).
            use_tls: Enable STARTTLS (True) or plain connection (False).
            batch_size: Max messages per SMTP connection.
            ttl: Connection cache TTL in seconds.
            limit_per_minute: Rate limit per minute.
            limit_per_hour: Rate limit per hour.
            limit_per_day: Rate limit per day.
            limit_behavior: Action when rate exceeded ("defer" or "reject").

        Returns:
            Complete account record after insert/update.
        """
        data = {k: v for k, v in locals().items() if k != "self"}
        await self.table.add(data)
        return await self.table.get(tenant_id, id)

    async def get(self, tenant_id: str, account_id: str) -> dict:
        """Retrieve a single SMTP account by tenant and ID.

        Args:
            tenant_id: Tenant that owns the account.
            account_id: Account identifier.

        Returns:
            Account configuration dict.

        Raises:
            ValueError: If account not found.
        """
        return await self.table.get(tenant_id, account_id)

    async def list(self, tenant_id: str) -> list[dict]:
        """List all SMTP accounts for a tenant.

        Args:
            tenant_id: Tenant to list accounts for.

        Returns:
            List of account dicts ordered by ID.
        """
        return await self.table.list_all(tenant_id=tenant_id)

    @POST
    async def delete(self, tenant_id: str, account_id: str) -> None:
        """Delete an SMTP account.

        Args:
            tenant_id: Tenant that owns the account.
            account_id: Account identifier to delete.
        """
        await self.table.remove(tenant_id, account_id)


__all__ = ["AccountEndpoint"]
