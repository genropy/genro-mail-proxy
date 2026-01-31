# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition extensions for AccountsTable.

This module adds PEC (Posta Elettronica Certificata) functionality to the
base AccountsTable. PEC is the Italian certified email system that requires
IMAP polling for delivery receipts.

PEC accounts have:
- is_pec_account=1 flag
- IMAP configuration for receipt polling
- Sync state tracking (last_uid, uidvalidity)

Usage:
    class AccountsTable(AccountsTable_EE, AccountsTableBase):
        pass
"""

from __future__ import annotations

from typing import Any

from sql import Integer, String, Timestamp


class AccountsTable_EE:
    """Enterprise Edition: PEC account support with IMAP configuration.

    Adds:
    - IMAP/PEC columns via configure()
    - Methods for PEC account management
    - IMAP sync state tracking
    """

    def configure(self) -> None:
        """Add EE columns for IMAP/PEC support after CE columns."""
        super().configure()  # type: ignore[misc]
        c = self.columns  # type: ignore[attr-defined]
        # PEC account flag
        c.column("is_pec_account", Integer, default=0)
        # IMAP configuration for receipt polling
        c.column("imap_host", String)
        c.column("imap_port", Integer, default=993)
        c.column("imap_user", String)
        c.column("imap_password", String, encrypted=True)
        c.column("imap_folder", String, default="INBOX")
        # IMAP sync state
        c.column("imap_last_uid", Integer)
        c.column("imap_last_sync", Timestamp)
        c.column("imap_uidvalidity", Integer)

    async def add_pec_account(self, acc: dict[str, Any]) -> str:
        """Insert or update a PEC account with IMAP configuration.

        PEC accounts have is_pec_account=1 and require IMAP settings
        for reading delivery receipts (ricevute di accettazione/consegna).

        Required fields:
            id: Account identifier
            host, port: SMTP server config
            imap_host: IMAP server for reading receipts

        Optional IMAP fields:
            imap_port: IMAP port (default 993)
            imap_user: IMAP username (defaults to SMTP user)
            imap_password: IMAP password (defaults to SMTP password)
            imap_folder: Folder to monitor (default "INBOX")

        Returns:
            The account's internal pk (UUID).
        """
        pec_acc = dict(acc)
        pec_acc["is_pec_account"] = 1
        return await self.add(pec_acc)  # type: ignore[attr-defined]

    async def list_pec_accounts(self) -> list[dict[str, Any]]:
        """Return all PEC accounts (is_pec_account=1).

        Returns:
            List of PEC account dicts with IMAP configuration.
        """
        rows = await self.db.adapter.fetch_all(  # type: ignore[attr-defined]
            """
            SELECT pk, id, tenant_id, host, port, user, ttl,
                   limit_per_minute, limit_per_hour, limit_per_day,
                   limit_behavior, use_tls, batch_size, is_pec_account,
                   imap_host, imap_port, imap_user, imap_password, imap_folder,
                   imap_last_uid, imap_last_sync, imap_uidvalidity,
                   created_at, updated_at
            FROM accounts
            WHERE is_pec_account = 1
            ORDER BY id
            """,
            {},
        )
        return [self._decode_use_tls(dict(row)) for row in rows]  # type: ignore[attr-defined]

    async def get_pec_account_ids(self) -> set[str]:
        """Get the set of account IDs that are PEC accounts.

        Returns:
            Set of account IDs with is_pec_account=1.
        """
        accounts = await self.list_pec_accounts()
        return {acc["id"] for acc in accounts}

    async def update_imap_sync_state(
        self,
        tenant_id: str,
        account_id: str,
        last_uid: int,
        uidvalidity: int | None = None,
    ) -> None:
        """Update IMAP sync state after processing receipts.

        Called after polling IMAP for PEC receipts to track progress.
        Next poll will start from last_uid + 1.

        Args:
            tenant_id: The tenant that owns this account.
            account_id: The account identifier.
            last_uid: The last processed UID.
            uidvalidity: The UIDVALIDITY value (detects mailbox reset).
        """
        params: dict[str, Any] = {
            "tenant_id": tenant_id,
            "account_id": account_id,
            "last_uid": last_uid,
        }
        if uidvalidity is not None:
            await self.execute(  # type: ignore[attr-defined]
                """
                UPDATE accounts
                SET imap_last_uid = :last_uid,
                    imap_uidvalidity = :uidvalidity,
                    imap_last_sync = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = :tenant_id AND id = :account_id
                """,
                {**params, "uidvalidity": uidvalidity},
            )
        else:
            await self.execute(  # type: ignore[attr-defined]
                """
                UPDATE accounts
                SET imap_last_uid = :last_uid,
                    imap_last_sync = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE tenant_id = :tenant_id AND id = :account_id
                """,
                params,
            )


__all__ = ["AccountsTable_EE"]
