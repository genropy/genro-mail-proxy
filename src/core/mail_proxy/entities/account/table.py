# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Accounts table manager for SMTP configurations (CE base)."""

from __future__ import annotations

from typing import Any

from sql import Integer, String, Table, Timestamp
from genro_toolbox import get_uuid


class AccountsTable(Table):
    """Accounts table: SMTP server configurations (CE base).

    CE provides: add(), get(), list_all(), remove(), migrations.
    EE extends with: add_pec_account(), list_pec_accounts(),
                     get_pec_account_ids(), update_imap_sync_state().

    Schema: pk (UUID), tenant_id+id (unique), host, port, user, password,
            rate limits, use_tls, IMAP/PEC columns, timestamps.
    """

    name = "accounts"
    pkey = "pk"

    def create_table_sql(self) -> str:
        """Generate CREATE TABLE with UNIQUE (tenant_id, id) for multi-tenant isolation."""
        sql = super().create_table_sql()
        # Add UNIQUE constraint before final closing parenthesis
        last_paren = sql.rfind(")")
        return sql[:last_paren] + ',\n    UNIQUE ("tenant_id", "id")\n)'

    def configure(self) -> None:
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
        """Migrate from legacy schema (composite PK) to new schema (UUID pk).

        This migration is needed for databases created before this version where
        the accounts table used a composite PRIMARY KEY (tenant_id, id).

        Returns:
            True if migration was performed, False if not needed.
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
                {"pk": pk, **row_dict}
            )

        # Swap tables
        await self.db.adapter.execute("DROP TABLE accounts")
        await self.db.adapter.execute("ALTER TABLE accounts_new RENAME TO accounts")

        return True

    async def add(self, acc: dict[str, Any]) -> str:
        """Insert or update an SMTP account.

        Supports both regular SMTP accounts and PEC accounts with IMAP config.

        Returns:
            The account's internal pk (UUID).
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
        """Fetch a single SMTP account or raise if not found.

        Args:
            tenant_id: The tenant that owns this account.
            account_id: The account identifier.

        Raises:
            ValueError: If account not found for this tenant.
        """
        account = await self.select_one(where={"tenant_id": tenant_id, "id": account_id})
        if not account:
            raise ValueError(f"Account '{account_id}' not found for tenant '{tenant_id}'")
        return self._decode_use_tls(account)

    async def list_all(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        """Return SMTP accounts, optionally filtered by tenant."""
        columns = [
            "pk", "id", "tenant_id", "host", "port", "user", "ttl",
            "limit_per_minute", "limit_per_hour", "limit_per_day",
            "limit_behavior", "use_tls", "batch_size", "created_at", "updated_at",
            # PEC/IMAP fields
            "is_pec_account", "imap_host", "imap_port",
        ]

        if tenant_id:
            rows = await self.select(columns=columns, where={"tenant_id": tenant_id}, order_by="id")
        else:
            rows = await self.select(columns=columns, order_by="id")

        return [self._decode_account(acc) for acc in rows]

    async def remove(self, tenant_id: str, account_id: str) -> None:
        """Remove an SMTP account for a specific tenant.

        Args:
            tenant_id: The tenant that owns this account.
            account_id: The account identifier.

        Note: Related messages should be cleaned by the calling code
        or via foreign key constraints.
        """
        await self.delete(where={"tenant_id": tenant_id, "id": account_id})

    def _decode_use_tls(self, account: dict[str, Any]) -> dict[str, Any]:
        """Convert use_tls INTEGER to bool/None."""
        if "use_tls" in account:
            val = account["use_tls"]
            account["use_tls"] = bool(val) if val is not None else None
        return account

    def _decode_account(self, account: dict[str, Any]) -> dict[str, Any]:
        """Convert database integers to booleans for API response."""
        self._decode_use_tls(account)
        # Convert is_pec_account to bool
        if "is_pec_account" in account:
            val = account["is_pec_account"]
            account["is_pec_account"] = bool(val) if val else False
        return account

    async def sync_schema(self) -> None:
        """Sync table schema.

        For new databases, pk is PRIMARY KEY and UNIQUE(tenant_id, id) is created.
        For existing databases, ensures the unique index exists as fallback.
        """
        await super().sync_schema()
        # Ensure UNIQUE index for tenant isolation
        try:
            await self.execute(
                'CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_tenant_id '
                'ON accounts ("tenant_id", "id")'
            )
        except Exception:
            pass  # Index already exists or UNIQUE constraint covers it


__all__ = ["AccountsTable"]
