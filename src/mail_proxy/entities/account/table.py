# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Accounts table manager for SMTP configurations."""

from __future__ import annotations

from typing import Any

from ...sql import Integer, String, Table, Timestamp


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
        # EE columns - IMAP/PEC config
        c.column("is_pec_account", Integer, default=0)
        c.column("imap_host", String)
        c.column("imap_port", Integer, default=993)
        c.column("imap_user", String)
        c.column("imap_password", String)
        c.column("imap_folder", String, default="INBOX")
        # EE columns - IMAP sync state
        c.column("imap_last_uid", Integer)
        c.column("imap_last_sync", Timestamp)
        c.column("imap_uidvalidity", Integer)

    async def add(self, acc: dict[str, Any]) -> None:
        """Insert or update an SMTP account.

        Supports both regular SMTP accounts and PEC accounts with IMAP config.
        """
        use_tls = acc.get("use_tls")
        use_tls_val = None if use_tls is None else (1 if use_tls else 0)

        is_pec = acc.get("is_pec_account")
        is_pec_val = 1 if is_pec else 0

        data = {
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
            "is_pec_account": is_pec_val,
        }

        # Add PEC/IMAP fields if present
        if acc.get("imap_host"):
            data["imap_host"] = acc["imap_host"]
            data["imap_port"] = int(acc.get("imap_port") or 993)
            data["imap_user"] = acc.get("imap_user") or acc.get("user")
            data["imap_password"] = acc.get("imap_password") or acc.get("password")
            data["imap_folder"] = acc.get("imap_folder", "INBOX")

        await self.upsert(
            data,
            conflict_columns=["id"],
            update_extras=["updated_at = CURRENT_TIMESTAMP"],
        )

    async def add_pec_account(self, acc: dict[str, Any]) -> None:
        """Insert or update a PEC account with IMAP configuration.

        PEC accounts have is_pec_account=1 and require IMAP settings
        for reading delivery receipts (ricevute di accettazione/consegna).

        Required fields:
        - id, host, port: SMTP server config (same as regular accounts)
        - imap_host: IMAP server for reading receipts

        Optional IMAP fields:
        - imap_port: IMAP port (default 993)
        - imap_user: IMAP username (defaults to SMTP user)
        - imap_password: IMAP password (defaults to SMTP password)
        - imap_folder: Folder to monitor (default "INBOX")
        """
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
                # PEC-specific fields
                "is_pec_account": 1,
                "imap_host": acc["imap_host"],
                "imap_port": int(acc.get("imap_port", 993)),
                "imap_user": acc.get("imap_user") or acc.get("user"),
                "imap_password": acc.get("imap_password") or acc.get("password"),
                "imap_folder": acc.get("imap_folder", "INBOX"),
            },
            conflict_columns=["id"],
            update_extras=["updated_at = CURRENT_TIMESTAMP"],
        )

    async def list_pec_accounts(self) -> list[dict[str, Any]]:
        """Return all PEC accounts (is_pec_account=1)."""
        rows = await self.db.adapter.fetch_all(
            """
            SELECT id, tenant_id, host, port, user, ttl,
                   limit_per_minute, limit_per_hour, limit_per_day,
                   limit_behavior, use_tls, batch_size,
                   imap_host, imap_port, imap_user, imap_password, imap_folder,
                   imap_last_uid, imap_last_sync, imap_uidvalidity,
                   created_at, updated_at
            FROM accounts
            WHERE is_pec_account = 1
            ORDER BY id
            """,
            {},
        )
        return [self._decode_use_tls(dict(row)) for row in rows]

    async def update_imap_sync_state(
        self,
        account_id: str,
        last_uid: int,
        uidvalidity: int | None = None,
    ) -> None:
        """Update IMAP sync state after processing receipts."""
        params: dict[str, Any] = {
            "account_id": account_id,
            "last_uid": last_uid,
        }
        if uidvalidity is not None:
            await self.execute(
                """
                UPDATE accounts
                SET imap_last_uid = :last_uid,
                    imap_uidvalidity = :uidvalidity,
                    imap_last_sync = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :account_id
                """,
                {**params, "uidvalidity": uidvalidity},
            )
        else:
            await self.execute(
                """
                UPDATE accounts
                SET imap_last_uid = :last_uid,
                    imap_last_sync = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = :account_id
                """,
                params,
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
            # PEC/IMAP fields
            "is_pec_account", "imap_host", "imap_port",
        ]

        if tenant_id:
            rows = await self.select(columns=columns, where={"tenant_id": tenant_id}, order_by="id")
        else:
            rows = await self.select(columns=columns, order_by="id")

        return [self._decode_account(acc) for acc in rows]

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

    def _decode_account(self, account: dict[str, Any]) -> dict[str, Any]:
        """Convert database integers to booleans for API response."""
        self._decode_use_tls(account)
        # Convert is_pec_account to bool
        if "is_pec_account" in account:
            val = account["is_pec_account"]
            account["is_pec_account"] = bool(val) if val else False
        return account


__all__ = ["AccountsTable"]
