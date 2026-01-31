# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Instance configuration table manager.

This module provides the InstanceTable class for managing instance-level
configuration in a singleton pattern (single row with id=1).

The instance table stores:
    - Service identity: name, api_token
    - Edition: "ce" (Community) or "ee" (Enterprise)
    - Flexible config: JSON storage for additional settings

Configuration access follows a dual pattern:
    - Typed columns: name, api_token, edition (direct column access)
    - JSON config: Additional key-value pairs in config column

Example:
    Basic instance configuration::

        from core.mail_proxy.proxy_base import MailProxyBase

        proxy = MailProxyBase(db_path=":memory:")
        await proxy.init()

        instance = proxy.db.table("instance")

        # Set instance name
        await instance.set_name("production-mailer")

        # Set API token
        await instance.set_api_token("secret-token")

        # Store additional config
        await instance.set_config("host", "0.0.0.0")
        await instance.set_config("port", "8080")

        # Check edition
        is_ee = await instance.is_enterprise()

Note:
    Enterprise Edition (EE) extends this class with InstanceTable_EE
    mixin, adding bounce detection IMAP configuration columns.
"""

from __future__ import annotations

from typing import Any

from sql import Integer, String, Table, Timestamp


class InstanceTable(Table):
    """Singleton table for instance-level configuration.

    Stores instance-wide settings in a single row (id=1). Provides
    typed access to common settings and flexible JSON storage for
    additional configuration.

    Attributes:
        name: Table name ("instance").
        pkey: Primary key column ("id").

    Table Schema:
        - id: Always 1 (singleton pattern)
        - name: Instance display name
        - api_token: Master API token for authentication
        - edition: "ce" (Community) or "ee" (Enterprise)
        - config: JSON storage for additional settings
        - created_at: Creation timestamp
        - updated_at: Last modification timestamp

    Example:
        Configure instance settings::

            instance = proxy.db.table("instance")

            # Typed column access
            await instance.set_name("my-mailer")
            name = await instance.get_name()

            # JSON config access
            await instance.set_config("max_workers", "4")
            workers = await instance.get_config("max_workers")

            # Get all config merged
            all_config = await instance.get_all_config()
    """

    name = "instance"
    pkey = "id"

    def configure(self) -> None:
        """Define table columns.

        Columns:
            id: Singleton ID (always 1, INTEGER primary key).
            name: Instance display name (default: "mail-proxy").
            api_token: Master API token for authentication.
            edition: "ce" or "ee" (default: "ce").
            config: JSON storage for additional key-value settings.
            created_at: Row creation timestamp.
            updated_at: Last modification timestamp.

        Note:
            EE columns (bounce_*) are added by InstanceTable_EE.configure().
        """
        c = self.columns
        c.column("id", Integer)
        c.column("name", String, default="mail-proxy")
        c.column("api_token", String)
        c.column("edition", String, default="ce")
        c.column("config", String, json_encoded=True)
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")

    async def get_instance(self) -> dict[str, Any] | None:
        """Get the singleton instance configuration.

        Returns:
            Instance record dict, or None if not yet created.
        """
        return await self.select_one(where={"id": 1})

    async def ensure_instance(self) -> dict[str, Any]:
        """Get or create the singleton instance configuration.

        Creates the singleton row if it doesn't exist.

        Returns:
            Instance record dict (never None).
        """
        row = await self.get_instance()
        if row is None:
            await self.insert({"id": 1})
            row = await self.get_instance()
        return row  # type: ignore[return-value]

    async def update_instance(self, updates: dict[str, Any]) -> None:
        """Update the singleton instance configuration.

        Args:
            updates: Dict of column names to new values.

        Example:
            ::

                await instance.update_instance({
                    "name": "production",
                    "api_token": "new-token",
                })
        """
        await self.ensure_instance()
        async with self.record(1) as rec:
            for key, value in updates.items():
                rec[key] = value

    async def get_name(self) -> str:
        """Get instance display name.

        Returns:
            Instance name, defaults to "mail-proxy" if not set.
        """
        row = await self.ensure_instance()
        return row.get("name") or "mail-proxy"

    async def set_name(self, name: str) -> None:
        """Set instance display name.

        Args:
            name: New instance name.
        """
        await self.update_instance({"name": name})

    async def get_api_token(self) -> str | None:
        """Get master API token.

        Returns:
            API token string, or None if not set.
        """
        row = await self.ensure_instance()
        return row.get("api_token")

    async def set_api_token(self, token: str) -> None:
        """Set master API token.

        Args:
            token: New API token.
        """
        await self.update_instance({"api_token": token})

    async def get_edition(self) -> str:
        """Get current edition.

        Returns:
            "ce" (Community Edition) or "ee" (Enterprise Edition).
        """
        row = await self.ensure_instance()
        return row.get("edition") or "ce"

    async def is_enterprise(self) -> bool:
        """Check if running in Enterprise Edition mode.

        Returns:
            True if edition is "ee", False otherwise.
        """
        return await self.get_edition() == "ee"

    async def set_edition(self, edition: str) -> None:
        """Set edition.

        Args:
            edition: "ce" (Community) or "ee" (Enterprise).

        Raises:
            ValueError: If edition is not "ce" or "ee".
        """
        if edition not in ("ce", "ee"):
            raise ValueError(f"Invalid edition: {edition}. Must be 'ce' or 'ee'.")
        await self.update_instance({"edition": edition})

    # Typed column names for dual access pattern
    _TYPED_CONFIG_KEYS = {"name", "api_token", "edition"}

    async def get_config(self, key: str, default: str | None = None) -> str | None:
        """Get a configuration value by key.

        Uses dual access pattern:
            - Keys in _TYPED_CONFIG_KEYS: read from typed columns
            - Other keys: read from JSON config column

        Args:
            key: Configuration key name.
            default: Default value if key not found.

        Returns:
            Configuration value as string, or default.

        Example:
            ::

                # Typed column
                name = await instance.get_config("name")

                # JSON config
                port = await instance.get_config("port", "8080")
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

        Uses dual access pattern:
            - Keys in _TYPED_CONFIG_KEYS: save to typed columns
            - Other keys: save to JSON config column

        Args:
            key: Configuration key name.
            value: Configuration value (string).

        Example:
            ::

                # Typed column
                await instance.set_config("name", "production")

                # JSON config
                await instance.set_config("host", "0.0.0.0")
        """
        if key in self._TYPED_CONFIG_KEYS:
            await self.update_instance({key: value})
        else:
            row = await self.ensure_instance()
            config = row.get("config") or {}
            config[key] = value
            await self.update_instance({"config": config})

    async def get_all_config(self) -> dict[str, Any]:
        """Get all configuration values merged.

        Returns typed columns and JSON config merged into a single dict.
        JSON config values override typed columns if same key exists.

        Returns:
            Dict with all configuration key-value pairs.

        Example:
            ::

                config = await instance.get_all_config()
                # Returns: {"name": "my-mailer", "edition": "ce", "host": "0.0.0.0", ...}
        """
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
