# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Instance configuration table with typed columns.

Singleton table (id=1) for instance-level configuration.
Replaces the key-value instance_config table with typed columns.

Columns migrated from instance_config key-value:
    - name: Instance name
    - api_token: API authentication token

Columns for bounce detection (EE feature):
    - bounce_enabled: Enable bounce detection
    - bounce_imap_host/port/user/password/folder: IMAP connection
    - bounce_imap_ssl: Use SSL for IMAP connection (default: True)
    - bounce_poll_interval: Polling interval in seconds (default: 60)
    - bounce_return_path: Return-Path header for outgoing emails
    - bounce_last_uid: Last processed UID (IMAP sync state)
    - bounce_last_sync: Last sync timestamp
    - bounce_uidvalidity: IMAP UIDVALIDITY (detect mailbox reset)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ...sql import Integer, String, Table, Timestamp


class InstanceTable(Table):
    """Instance configuration table: singleton with typed columns."""

    name = "instance"

    def configure(self) -> None:
        c = self.columns
        # Singleton ID (always 1)
        c.column("id", Integer, primary_key=True)

        # General instance config (migrated from key-value)
        c.column("name", String, default="mail-proxy")
        c.column("api_token", String)

        # Bounce detection config (EE)
        c.column("bounce_enabled", Integer, default=0)
        c.column("bounce_imap_host", String)
        c.column("bounce_imap_port", Integer, default=993)
        c.column("bounce_imap_user", String)
        c.column("bounce_imap_password", String)
        c.column("bounce_imap_folder", String, default="INBOX")
        c.column("bounce_imap_ssl", Integer, default=1)  # Default: use SSL
        c.column("bounce_poll_interval", Integer, default=60)  # Default: 60 seconds
        c.column("bounce_return_path", String)

        # Bounce IMAP sync state
        c.column("bounce_last_uid", Integer)
        c.column("bounce_last_sync", Timestamp)
        c.column("bounce_uidvalidity", Integer)

        # Timestamps
        c.column("created_at", Timestamp, default="CURRENT_TIMESTAMP")
        c.column("updated_at", Timestamp, default="CURRENT_TIMESTAMP")

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
        await self.update(
            updates | {"updated_at": datetime.now(timezone.utc)},
            where={"id": 1},
        )

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

    # Bounce detection config

    async def is_bounce_enabled(self) -> bool:
        """Check if bounce detection is enabled."""
        row = await self.ensure_instance()
        return bool(row.get("bounce_enabled"))

    async def get_bounce_config(self) -> dict[str, Any]:
        """Get bounce detection configuration."""
        row = await self.ensure_instance()
        return {
            "enabled": bool(row.get("bounce_enabled")),
            "imap_host": row.get("bounce_imap_host"),
            "imap_port": row.get("bounce_imap_port") or 993,
            "imap_user": row.get("bounce_imap_user"),
            "imap_password": row.get("bounce_imap_password"),
            "imap_folder": row.get("bounce_imap_folder") or "INBOX",
            "imap_ssl": bool(row.get("bounce_imap_ssl", 1)),
            "poll_interval": row.get("bounce_poll_interval") or 60,
            "return_path": row.get("bounce_return_path"),
            "last_uid": row.get("bounce_last_uid"),
            "last_sync": row.get("bounce_last_sync"),
            "uidvalidity": row.get("bounce_uidvalidity"),
        }

    async def set_bounce_config(
        self,
        *,
        enabled: bool | None = None,
        imap_host: str | None = None,
        imap_port: int | None = None,
        imap_user: str | None = None,
        imap_password: str | None = None,
        imap_folder: str | None = None,
        imap_ssl: bool | None = None,
        poll_interval: int | None = None,
        return_path: str | None = None,
    ) -> None:
        """Set bounce detection configuration."""
        updates: dict[str, Any] = {}
        if enabled is not None:
            updates["bounce_enabled"] = 1 if enabled else 0
        if imap_host is not None:
            updates["bounce_imap_host"] = imap_host
        if imap_port is not None:
            updates["bounce_imap_port"] = imap_port
        if imap_user is not None:
            updates["bounce_imap_user"] = imap_user
        if imap_password is not None:
            updates["bounce_imap_password"] = imap_password
        if imap_folder is not None:
            updates["bounce_imap_folder"] = imap_folder
        if imap_ssl is not None:
            updates["bounce_imap_ssl"] = 1 if imap_ssl else 0
        if poll_interval is not None:
            updates["bounce_poll_interval"] = poll_interval
        if return_path is not None:
            updates["bounce_return_path"] = return_path
        if updates:
            await self.update_instance(updates)

    async def update_bounce_sync_state(
        self,
        *,
        last_uid: int,
        last_sync: int,
        uidvalidity: int | None = None,
    ) -> None:
        """Update bounce IMAP sync state after processing."""
        updates: dict[str, Any] = {
            "bounce_last_uid": last_uid,
            "bounce_last_sync": last_sync,
        }
        if uidvalidity is not None:
            updates["bounce_uidvalidity"] = uidvalidity
        await self.update_instance(updates)


__all__ = ["InstanceTable"]
