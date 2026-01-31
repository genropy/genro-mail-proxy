# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition: PEC account support for AccountEndpoint.

PEC = Posta Elettronica Certificata (Italian certified email).
Requires IMAP polling for delivery receipts.
"""

from __future__ import annotations

from typing import Literal

from core.mail_proxy.interface.endpoint_base import POST


class AccountEndpoint_EE:
    """EE mixin: adds PEC account methods to AccountEndpoint."""

    @POST
    async def add_pec(
        self,
        id: str,
        tenant_id: str,
        host: str,
        port: int,
        imap_host: str,
        user: str | None = None,
        password: str | None = None,
        use_tls: bool = True,
        imap_port: int = 993,
        imap_user: str | None = None,
        imap_password: str | None = None,
        imap_folder: str = "INBOX",
        batch_size: int | None = None,
        ttl: int = 300,
        limit_per_minute: int | None = None,
        limit_per_hour: int | None = None,
        limit_per_day: int | None = None,
        limit_behavior: Literal["defer", "reject"] = "defer",
    ) -> dict:
        """Add or update a PEC account with IMAP configuration."""
        data = {k: v for k, v in locals().items() if k != "self"}
        data["is_pec_account"] = True
        await self.table.add_pec_account(data)  # type: ignore[attr-defined]
        return await self.table.get(tenant_id, id)  # type: ignore[attr-defined]

    async def list_pec(self) -> list[dict]:
        """List all PEC accounts across all tenants."""
        return await self.table.list_pec_accounts()  # type: ignore[attr-defined]

    async def get_pec_ids(self) -> set[str]:
        """Get set of account IDs that are PEC accounts."""
        return await self.table.get_pec_account_ids()  # type: ignore[attr-defined]


__all__ = ["AccountEndpoint_EE"]
