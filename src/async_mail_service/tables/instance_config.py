# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Instance config table manager for key-value configuration."""

from __future__ import annotations

from ..sql import String, Table, Timestamp


class InstanceConfigTable(Table):
    """Instance config table: key-value configuration storage.

    Used for storing instance-specific settings like last sync timestamps.
    """

    name = "instance_config"

    def configure(self) -> None:
        c = self.columns
        c.column("key", String, primary_key=True)
        c.column("value", String, nullable=False)
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")

    async def get(self, key: str, default: str | None = None) -> str | None:
        """Get a configuration value by key."""
        row = await self.select_one(columns=["value"], where={"key": key})
        return row["value"] if row else default

    async def set(self, key: str, value: str) -> None:
        """Set a configuration value."""
        await self.upsert(
            {"key": key, "value": value},
            conflict_columns=["key"],
            update_extras=["updated_at = CURRENT_TIMESTAMP"],
        )

    async def get_all(self) -> dict[str, str]:
        """Get all configuration values as a dict."""
        rows = await self.select(columns=["key", "value"])
        return {row["key"]: row["value"] for row in rows}


__all__ = ["InstanceConfigTable"]
