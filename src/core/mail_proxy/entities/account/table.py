# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SMTP account configuration table manager.

This module provides the AccountsTable class for managing SMTP server
configurations in a multi-tenant environment. Each account belongs to
a tenant and defines connection parameters for outgoing email delivery.

The table uses a UUID primary key (pk) with a unique constraint on
(tenant_id, id) for multi-tenant isolation. This allows each tenant
to use their own account identifiers without conflicts.

Example:
    Basic account management::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        accounts = proxy.db.table("accounts")

        # Add an SMTP account
        pk = await accounts.add({
            "id": "main",
            "tenant_id": "acme",
            "host": "smtp.gmail.com",
            "port": 587,
            "user": "sender@acme.com",
            "password": "app-password",
            "use_tls": True,
        })

        # Retrieve account
        account = await accounts.get("acme", "main")

        # List all accounts for a tenant
        all_accounts = await accounts.list_all(tenant_id="acme")

Attributes:
    name: Table name in database ("accounts").
    pkey: Primary key column name ("pk").

Note:
    Enterprise Edition (EE) extends this class with PEC (Posta Elettronica
    Certificata) support via AccountsTable_EE mixin, adding IMAP polling
    for delivery receipts.
"""

from __future__ import annotations

from typing import Any

from genro_toolbox import get_uuid

from sql import Integer, String, Table, Timestamp


class AccountsTable(Table):
    """SMTP account configurations for outgoing email delivery.

    Manages SMTP server connection parameters including host, port,
    credentials, TLS settings, and rate limits. Each account belongs
    to a tenant and is identified by a client-provided ID.

    The table schema includes:
        - pk: Internal UUID primary key
        - id: Client-provided account identifier (unique per tenant)
        - tenant_id: Foreign key to tenants table
        - host, port: SMTP server connection
        - user, password: Authentication credentials (password encrypted)
        - ttl: Connection cache TTL in seconds
        - limit_per_minute/hour/day: Rate limiting thresholds
        - limit_behavior: Action when rate exceeded ("defer" or "reject")
        - use_tls: TLS/STARTTLS mode
        - batch_size: Max messages per connection

    Example:
        Adding an account with rate limits::

            pk = await accounts.add({
                "id": "transactional",
                "tenant_id": "acme",
                "host": "smtp.sendgrid.net",
                "port": 587,
                "user": "apikey",
                "password": "SG.xxxxx",
                "use_tls": True,
                "limit_per_hour": 1000,
                "limit_per_day": 10000,
                "limit_behavior": "defer",
            })
    """

    name = "accounts"
    pkey = "pk"

    def create_table_sql(self) -> str:
        """Generate CREATE TABLE statement with multi-tenant unique constraint.

        Adds UNIQUE (tenant_id, id) constraint to ensure each tenant
        has unique account identifiers while allowing the same ID
        across different tenants.

        Returns:
            SQL CREATE TABLE statement with UNIQUE constraint.
        """
        sql = super().create_table_sql()
        # Add UNIQUE constraint before final closing parenthesis
        last_paren = sql.rfind(")")
        return sql[:last_paren] + ',\n    UNIQUE ("tenant_id", "id")\n)'

    def configure(self) -> None:
        """Define table columns.

        Columns:
            pk: UUID primary key (auto-generated on insert).
            id: Client account identifier (required).
            tenant_id: Owning tenant (FK to tenants, required).
            host: SMTP server hostname (required).
            port: SMTP server port (required).
            user: SMTP username for authentication.
            password: SMTP password (encrypted at rest).
            ttl: Connection cache TTL in seconds (default: 300).
            limit_per_minute: Max emails per minute.
            limit_per_hour: Max emails per hour.
            limit_per_day: Max emails per day.
            limit_behavior: Rate limit action ("defer" or "reject").
            use_tls: TLS mode (1=STARTTLS, 0=none, NULL=auto).
            batch_size: Messages per SMTP connection.
            created_at: Record creation timestamp.
            updated_at: Last modification timestamp.

        Note:
            EE columns (is_pec_account, imap_*) are added by
            AccountsTable_EE.configure() when enterprise package is installed.
        """
        c = self.columns
        c.column("pk", String)  # UUID generated internally
        c.column("id", String, nullable=False)  # account_id from client
        c.column("tenant_id", String, nullable=False).relation("tenants", sql=True)
        c.column("host", String, nullable=False)
        c.column("port", Integer, nullable=False)
        c.column("user", String)
        c.column("password", String, encrypted=True)
        c.column("ttl", Integer, default=300)
        c.column("limit_per_minute", Integer)
        c.column("limit_per_hour", Integer)
        c.column("limit_per_day", Integer)
        c.column("limit_behavior", String)
        c.column("use_tls", Integer)
        c.column("batch_size", Integer)
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")
        # EE columns added by AccountsTable_EE.configure()

    async def migrate_from_legacy_schema(self) -> bool:
        """Migrate from composite primary key to UUID primary key.

        Legacy databases used PRIMARY KEY (tenant_id, id). This migration
        adds a UUID 'pk' column as the new primary key while preserving
        the UNIQUE constraint on (tenant_id, id).

        The migration process:
            1. Create new table with UUID pk column
            2. Copy existing rows, generating UUIDs
            3. Drop old table and rename new table

        Returns:
            True if migration was performed, False if not needed
            (pk column already exists or table doesn't exist).

        Note:
            Safe to call on every startup. Skips silently if migration
            is not needed.
        """
        # Check if migration is needed by looking for pk column
        try:
            await self.db.adapter.fetch_one("SELECT pk FROM accounts LIMIT 1")
            return False  # pk column exists, no migration needed
        except Exception:
            pass  # pk column doesn't exist, need migration

        # Check if old table exists at all
        try:
            await self.db.adapter.fetch_one("SELECT id FROM accounts LIMIT 1")
        except Exception:
            return False  # Table doesn't exist, will be created fresh

        # Migration: create new table, copy data with generated UUIDs, swap
        await self.db.adapter.execute("""
            CREATE TABLE accounts_new (
                pk TEXT PRIMARY KEY,
                id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                host TEXT NOT NULL,
                port INTEGER NOT NULL,
                user TEXT,
                password TEXT,
                ttl INTEGER DEFAULT 300,
                limit_per_minute INTEGER,
                limit_per_hour INTEGER,
                limit_per_day INTEGER,
                limit_behavior TEXT,
                use_tls INTEGER,
                batch_size INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_pec_account INTEGER DEFAULT 0,
                imap_host TEXT,
                imap_port INTEGER DEFAULT 993,
                imap_user TEXT,
                imap_password TEXT,
                imap_folder TEXT DEFAULT 'INBOX',
                imap_last_uid INTEGER,
                imap_last_sync TIMESTAMP,
                imap_uidvalidity INTEGER,
                UNIQUE (tenant_id, id)
            )
        """)

        # Copy data, generating UUIDs for pk
        rows = await self.db.adapter.fetch_all(
            """SELECT id, tenant_id, host, port, user, password, ttl,
                      limit_per_minute, limit_per_hour, limit_per_day,
                      limit_behavior, use_tls, batch_size,
                      created_at, updated_at, is_pec_account,
                      imap_host, imap_port, imap_user, imap_password, imap_folder,
                      imap_last_uid, imap_last_sync, imap_uidvalidity
               FROM accounts"""
        )
        for row in rows:
            pk = get_uuid()
            row_dict = dict(row)
            await self.db.adapter.execute(
                """INSERT INTO accounts_new
                   (pk, id, tenant_id, host, port, user, password, ttl,
                    limit_per_minute, limit_per_hour, limit_per_day,
                    limit_behavior, use_tls, batch_size,
                    created_at, updated_at, is_pec_account,
                    imap_host, imap_port, imap_user, imap_password, imap_folder,
                    imap_last_uid, imap_last_sync, imap_uidvalidity)
                   VALUES (:pk, :id, :tenant_id, :host, :port, :user, :password, :ttl,
                           :limit_per_minute, :limit_per_hour, :limit_per_day,
                           :limit_behavior, :use_tls, :batch_size,
                           :created_at, :updated_at, :is_pec_account,
                           :imap_host, :imap_port, :imap_user, :imap_password, :imap_folder,
                           :imap_last_uid, :imap_last_sync, :imap_uidvalidity)""",
                {"pk": pk, **row_dict},
            )

        # Swap tables
        await self.db.adapter.execute("DROP TABLE accounts")
        await self.db.adapter.execute("ALTER TABLE accounts_new RENAME TO accounts")

        return True

    async def add(self, acc: dict[str, Any]) -> str:
        """Insert or update an SMTP account configuration.

        Performs an upsert based on (tenant_id, id). If the account exists,
        updates all fields. If new, generates a UUID for the pk column.

        Args:
            acc: Account configuration dict with keys:
                - id (required): Client account identifier.
                - tenant_id (required): Owning tenant ID.
                - host (required): SMTP server hostname.
                - port (required): SMTP server port.
                - user: SMTP username.
                - password: SMTP password (will be encrypted).
                - ttl: Connection cache TTL (default: 300).
                - limit_per_minute/hour/day: Rate limits.
                - limit_behavior: "defer" or "reject" (default: "defer").
                - use_tls: True/False/None for TLS mode.
                - batch_size: Messages per connection.
                - is_pec_account: True for PEC accounts (EE).
                - imap_*: IMAP settings for PEC (EE).

        Returns:
            The account's internal UUID (pk).

        Example:
            ::

                pk = await accounts.add({
                    "id": "marketing",
                    "tenant_id": "acme",
                    "host": "smtp.mailgun.org",
                    "port": 587,
                    "user": "postmaster@acme.com",
                    "password": "secret",
                    "use_tls": True,
                    "limit_per_hour": 500,
                })
        """
        tenant_id = acc["tenant_id"]
        account_id = acc["id"]

        use_tls = acc.get("use_tls")
        use_tls_val = None if use_tls is None else (1 if use_tls else 0)

        is_pec = acc.get("is_pec_account")
        is_pec_val = 1 if is_pec else 0

        # Use composite key for upsert
        async with self.record(
            {"tenant_id": tenant_id, "id": account_id},
            insert_missing=True,
        ) as rec:
            # Generate pk only for new records
            if "pk" not in rec:
                rec["pk"] = get_uuid()

            rec["host"] = acc["host"]
            rec["port"] = int(acc["port"])
            rec["user"] = acc.get("user")
            rec["password"] = acc.get("password")
            rec["ttl"] = int(acc.get("ttl", 300))
            rec["limit_per_minute"] = acc.get("limit_per_minute")
            rec["limit_per_hour"] = acc.get("limit_per_hour")
            rec["limit_per_day"] = acc.get("limit_per_day")
            rec["limit_behavior"] = acc.get("limit_behavior", "defer")
            rec["use_tls"] = use_tls_val
            rec["batch_size"] = acc.get("batch_size")
            rec["is_pec_account"] = is_pec_val

            # Add PEC/IMAP fields if present
            if acc.get("imap_host"):
                rec["imap_host"] = acc["imap_host"]
                rec["imap_port"] = int(acc.get("imap_port") or 993)
                rec["imap_user"] = acc.get("imap_user") or acc.get("user")
                rec["imap_password"] = acc.get("imap_password") or acc.get("password")
                rec["imap_folder"] = acc.get("imap_folder", "INBOX")

            pk = rec["pk"]

        return pk

    async def get(self, tenant_id: str, account_id: str) -> dict[str, Any]:
        """Retrieve a single SMTP account by tenant and ID.

        Args:
            tenant_id: The tenant that owns this account.
            account_id: The client-provided account identifier.

        Returns:
            Account dict with use_tls converted to bool/None.

        Raises:
            ValueError: If account not found for this tenant.

        Example:
            ::

                try:
                    account = await accounts.get("acme", "main")
                    print(f"SMTP host: {account['host']}")
                except ValueError:
                    print("Account not found")
        """
        account = await self.select_one(where={"tenant_id": tenant_id, "id": account_id})
        if not account:
            raise ValueError(f"Account '{account_id}' not found for tenant '{tenant_id}'")
        return self._decode_use_tls(account)

    async def list_all(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """List SMTP accounts, optionally filtered by tenant.

        Args:
            tenant_id: Filter by tenant ID. If None, returns all accounts.

        Returns:
            List of account dicts ordered by ID, with boolean fields decoded.

        Example:
            ::

                # All accounts for a tenant
                acme_accounts = await accounts.list_all(tenant_id="acme")

                # All accounts across all tenants (admin view)
                all_accounts = await accounts.list_all()
        """
        columns = [
            "pk",
            "id",
            "tenant_id",
            "host",
            "port",
            "user",
            "ttl",
            "limit_per_minute",
            "limit_per_hour",
            "limit_per_day",
            "limit_behavior",
            "use_tls",
            "batch_size",
            "created_at",
            "updated_at",
            # PEC/IMAP fields
            "is_pec_account",
            "imap_host",
            "imap_port",
        ]

        if tenant_id:
            rows = await self.select(columns=columns, where={"tenant_id": tenant_id}, order_by="id")
        else:
            rows = await self.select(columns=columns, order_by="id")

        return [self._decode_account(acc) for acc in rows]

    async def remove(self, tenant_id: str, account_id: str) -> None:
        """Delete an SMTP account.

        Args:
            tenant_id: The tenant that owns this account.
            account_id: The account identifier to delete.

        Note:
            Messages referencing this account should be cleaned up
            separately or via foreign key CASCADE constraints.
        """
        await self.delete(where={"tenant_id": tenant_id, "id": account_id})

    def _decode_use_tls(self, account: dict[str, Any]) -> dict[str, Any]:
        """Convert use_tls from INTEGER to bool/None.

        Database stores: 1=True, 0=False, NULL=None (auto-detect).
        API returns: True, False, or None.
        """
        if "use_tls" in account:
            val = account["use_tls"]
            account["use_tls"] = bool(val) if val is not None else None
        return account

    def _decode_account(self, account: dict[str, Any]) -> dict[str, Any]:
        """Decode all boolean fields for API response.

        Converts:
            - use_tls: INTEGER → bool/None
            - is_pec_account: INTEGER → bool
        """
        self._decode_use_tls(account)
        # Convert is_pec_account to bool
        if "is_pec_account" in account:
            val = account["is_pec_account"]
            account["is_pec_account"] = bool(val) if val else False
        return account

    async def sync_schema(self) -> None:
        """Synchronize table schema with column definitions.

        Adds missing columns and ensures the UNIQUE index on
        (tenant_id, id) exists for multi-tenant isolation.

        Safe to call on every startup.
        """
        await super().sync_schema()
        # Ensure UNIQUE index for tenant isolation
        try:
            await self.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_tenant_id "
                'ON accounts ("tenant_id", "id")'
            )
        except Exception:
            pass  # Index already exists or UNIQUE constraint covers it


__all__ = ["AccountsTable"]
