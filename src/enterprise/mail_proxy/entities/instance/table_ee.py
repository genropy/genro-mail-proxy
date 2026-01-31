# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition extensions for InstanceTable.

This module adds bounce detection configuration to the base InstanceTable.
Bounce detection monitors a dedicated IMAP mailbox for bounce notifications
and DSN (Delivery Status Notification) messages.

Bounce detection workflow:
1. Outgoing emails use a dedicated Return-Path address
2. Bounces are delivered to the bounce mailbox
3. IMAP poller reads and parses bounce messages
4. Bounce events are recorded in message_events

Usage:
    class InstanceTable(InstanceTable_EE, InstanceTableBase):
        pass
"""

from __future__ import annotations

from typing import Any

from sql import Integer, String, Timestamp


class InstanceTable_EE:
    """Enterprise Edition: Bounce detection configuration.

    Adds:
    - Bounce IMAP columns via configure()
    - Methods for bounce detection management
    - Sync state tracking
    """

    def configure(self) -> None:
        """Add EE columns for bounce detection after CE columns."""
        super().configure()  # type: ignore[misc]
        c = self.columns  # type: ignore[attr-defined]
        # Bounce detection config
        c.column("bounce_enabled", Integer, default=0)
        c.column("bounce_imap_host", String)
        c.column("bounce_imap_port", Integer, default=993)
        c.column("bounce_imap_user", String)
        c.column("bounce_imap_password", String, encrypted=True)
        c.column("bounce_imap_folder", String, default="INBOX")
        c.column("bounce_imap_ssl", Integer, default=1)
        c.column("bounce_poll_interval", Integer, default=60)
        c.column("bounce_return_path", String)
        # Bounce IMAP sync state
        c.column("bounce_last_uid", Integer)
        c.column("bounce_last_sync", Timestamp)
        c.column("bounce_uidvalidity", Integer)

    async def is_bounce_enabled(self) -> bool:
        """Check if bounce detection is enabled.

        Returns:
            True if bounce_enabled=1 in instance config.
        """
        row = await self.ensure_instance()  # type: ignore[attr-defined]
        return bool(row.get("bounce_enabled"))

    async def get_bounce_config(self) -> dict[str, Any]:
        """Get bounce detection configuration.

        Returns:
            Dict with bounce settings:
            - enabled: bool
            - imap_host, imap_port, imap_user, imap_password, imap_folder
            - imap_ssl: bool
            - poll_interval: seconds between polls
            - return_path: Return-Path header for outgoing emails
            - last_uid, last_sync, uidvalidity: sync state
        """
        row = await self.ensure_instance()  # type: ignore[attr-defined]
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
        """Set bounce detection configuration.

        Only provided fields are updated. Pass None to skip a field.

        Args:
            enabled: Enable/disable bounce detection.
            imap_host: IMAP server hostname.
            imap_port: IMAP port (default 993).
            imap_user: IMAP username.
            imap_password: IMAP password.
            imap_folder: Folder to monitor (default "INBOX").
            imap_ssl: Use SSL/TLS (default True).
            poll_interval: Seconds between polls (default 60).
            return_path: Return-Path header for outgoing emails.
        """
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
            await self.update_instance(updates)  # type: ignore[attr-defined]

    async def update_bounce_sync_state(
        self,
        *,
        last_uid: int,
        last_sync: int,
        uidvalidity: int | None = None,
    ) -> None:
        """Update bounce IMAP sync state after processing.

        Called after polling the bounce mailbox to track progress.
        Next poll will start from last_uid + 1.

        Args:
            last_uid: Last processed UID.
            last_sync: Unix timestamp of this sync.
            uidvalidity: IMAP UIDVALIDITY (detects mailbox reset).
        """
        updates: dict[str, Any] = {
            "bounce_last_uid": last_uid,
            "bounce_last_sync": last_sync,
        }
        if uidvalidity is not None:
            updates["bounce_uidvalidity"] = uidvalidity
        await self.update_instance(updates)  # type: ignore[attr-defined]


__all__ = ["InstanceTable_EE"]
