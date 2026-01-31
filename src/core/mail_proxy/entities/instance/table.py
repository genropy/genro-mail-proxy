# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Instance configuration table with typed columns (CE base).

Singleton table (id=1) for instance-level configuration.
"""

from __future__ import annotations

from typing import Any

from sql import Integer, String, Table, Timestamp


class InstanceTable(Table):
    """Instance configuration table: singleton with typed columns (CE base).

    CE provides: get/set_name(), get/set_api_token(), get/set_edition(),
                 is_enterprise(), get/set_config(), update_instance().
    EE extends with: is_bounce_enabled(), get/set_bounce_config(),
                     update_bounce_sync_state().

    Schema: id=1 (singleton), name, api_token, edition, config (JSON),
            bounce_* columns for EE IMAP configuration.
    """

    name = "instance"
    pkey = "id"

    def configure(self) -> None:
        c = self.columns
        # Singleton ID (always 1)
        c.column("id", Integer)

        # General instance config (migrated from key-value)
        c.column("name", String, default="mail-proxy")
        c.column("api_token", String)

        # Edition: "ce" (Community) or "ee" (Enterprise)
        c.column("edition", String, default="ce")

        # Flexible config storage (JSON) for CLI settings like host, port, etc.
        c.column("config", String, json_encoded=True)

        # Timestamps
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")
        # EE columns (bounce_*) added by InstanceTable_EE.configure()

    async def get_instance(self) -> dict[str, Any] | None:
        """Get the singleton instance configuration."""
        return await self.select_one(where={"id": 1})

    async def ensure_instance(self) -> dict[str, Any]:
        """Get or create the singleton instance configuration."""
        row = await self.get_instance()
        if row is None:
            await self.insert({"id": 1})
            row = await self.get_instance()
        return row  # type: ignore[return-value]

    async def update_instance(self, updates: dict[str, Any]) -> None:
        """Update the singleton instance configuration."""
        await self.ensure_instance()
        async with self.record(1) as rec:
            for key, value in updates.items():
                rec[key] = value

    # Convenience methods for common config access

    async def get_name(self) -> str:
        """Get instance name."""
        row = await self.ensure_instance()
        return row.get("name") or "mail-proxy"

    async def set_name(self, name: str) -> None:
        """Set instance name."""
        await self.update_instance({"name": name})

    async def get_api_token(self) -> str | None:
        """Get API token."""
        row = await self.ensure_instance()
        return row.get("api_token")

    async def set_api_token(self, token: str) -> None:
        """Set API token."""
        await self.update_instance({"api_token": token})

    # Edition management

    async def get_edition(self) -> str:
        """Get current edition ('ce' or 'ee')."""
        row = await self.ensure_instance()
        return row.get("edition") or "ce"

    async def is_enterprise(self) -> bool:
        """Check if running in Enterprise Edition mode."""
        return await self.get_edition() == "ee"

    async def set_edition(self, edition: str) -> None:
        """Set edition ('ce' or 'ee')."""
        if edition not in ("ce", "ee"):
            raise ValueError(f"Invalid edition: {edition}. Must be 'ce' or 'ee'.")
        await self.update_instance({"edition": edition})

    # Generic config access (typed columns + JSON config)
    # Typed columns: name, api_token, edition
    _TYPED_CONFIG_KEYS = {"name", "api_token", "edition"}

    async def get_config(self, key: str, default: str | None = None) -> str | None:
        """Get a configuration value by key.

        Keys in _TYPED_CONFIG_KEYS are read from typed columns.
        Other keys are read from the JSON 'config' column.
        """
        row = await self.ensure_instance()
        if key in self._TYPED_CONFIG_KEYS:
            value = row.get(key)
        else:
            config = row.get("config") or {}
            value = config.get(key)
        return str(value) if value is not None else default

    async def set_config(self, key: str, value: str) -> None:
        """Set a configuration value.

        Keys in _TYPED_CONFIG_KEYS are saved to typed columns.
        Other keys are saved to the JSON 'config' column.
        """
        if key in self._TYPED_CONFIG_KEYS:
            await self.update_instance({key: value})
        else:
            row = await self.ensure_instance()
            config = row.get("config") or {}
            config[key] = value
            await self.update_instance({"config": config})

    async def get_all_config(self) -> dict[str, Any]:
        """Get all configuration values (typed columns + JSON config merged)."""
        row = await self.ensure_instance()
        result: dict[str, Any] = {}
        # Add typed columns
        for key in self._TYPED_CONFIG_KEYS:
            if row.get(key) is not None:
                result[key] = row[key]
        # Merge JSON config (overrides typed if same key exists)
        config = row.get("config") or {}
        result.update(config)
        return result


__all__ = ["InstanceTable"]
