# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Send log table manager for rate limiting."""

from __future__ import annotations

from ...sql import Integer, String, Table


class SendLogTable(Table):
    """Send log table: delivery events for rate limiting.

    Stores timestamp of each send operation per account.
    Used to calculate sends within time windows for rate limiting.
    """

    name = "send_log"

    def configure(self) -> None:
        c = self.columns
        c.column("account_id", String)
        c.column("timestamp", Integer)

    async def log(self, account_id: str, timestamp: int) -> None:
        """Record a delivery event."""
        await self.insert({"account_id": account_id, "timestamp": timestamp})

    async def count_since(self, account_id: str, since_ts: int) -> int:
        """Count messages sent after since_ts for the given account."""
        row = await self.db.adapter.fetch_one(
            "SELECT COUNT(*) as cnt FROM send_log WHERE account_id = :account_id AND timestamp > :since_ts",
            {"account_id": account_id, "since_ts": since_ts},
        )
        return int(row["cnt"]) if row else 0

    async def purge_for_account(self, account_id: str) -> int:
        """Delete all log entries for an account. Returns deleted count."""
        return await self.delete(where={"account_id": account_id})


__all__ = ["SendLogTable"]
