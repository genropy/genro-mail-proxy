# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Enterprise Edition extensions for InstanceEndpoint.

This module adds bounce detection configuration to the base InstanceEndpoint.
Bounce detection monitors a dedicated IMAP mailbox for bounce notifications.

Bounce features:
- Get/set bounce detection configuration
- Runtime reload of bounce config

Usage:
    class InstanceEndpoint(InstanceEndpoint_EE, InstanceEndpointBase):
        pass
"""

from __future__ import annotations

from core.mail_proxy.interface.endpoint_base import POST


class InstanceEndpoint_EE:
    """Enterprise Edition: Bounce detection configuration.

    Adds methods for:
    - Getting bounce detection configuration
    - Setting bounce detection configuration
    - Reloading bounce config at runtime
    """

    async def get_bounce_config(self) -> dict:
        """Get bounce detection configuration.

        Returns:
            Dict with bounce settings including:
            - enabled: bool
            - imap_host, imap_port, imap_user, imap_folder
            - imap_ssl: bool
            - poll_interval: seconds between polls
            - return_path: Return-Path header for outgoing emails
        """
        config = await self.table.get_bounce_config()  # type: ignore[attr-defined]
        # Remove password from response for security
        config.pop("imap_password", None)
        # Remove sync state from user-facing response
        config.pop("last_uid", None)
        config.pop("last_sync", None)
        config.pop("uidvalidity", None)
        return {"ok": True, **config}

    @POST
    async def set_bounce_config(
        self,
        enabled: bool | None = None,
        imap_host: str | None = None,
        imap_port: int | None = None,
        imap_user: str | None = None,
        imap_password: str | None = None,
        imap_folder: str | None = None,
        imap_ssl: bool | None = None,
        poll_interval: int | None = None,
        return_path: str | None = None,
    ) -> dict:
        """Set bounce detection configuration.

        Only provided fields are updated. Pass None to skip a field.
        Use reload_bounce() to apply changes to running poller.

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

        Returns:
            Dict with ok=True.
        """
        await self.table.set_bounce_config(  # type: ignore[attr-defined]
            enabled=enabled,
            imap_host=imap_host,
            imap_port=imap_port,
            imap_user=imap_user,
            imap_password=imap_password,
            imap_folder=imap_folder,
            imap_ssl=imap_ssl,
            poll_interval=poll_interval,
            return_path=return_path,
        )
        return {"ok": True, "message": "Bounce config updated. Use reload_bounce() to apply."}

    @POST
    async def reload_bounce(self) -> dict:
        """Reload bounce detection configuration at runtime.

        Stops the current bounce poller (if running) and restarts it
        with the latest configuration from the database.

        Returns:
            Dict with ok=True and enabled status.
        """
        config = await self.table.get_bounce_config()  # type: ignore[attr-defined]
        enabled = config.get("enabled", False)

        # If proxy has bounce_receiver (EE runtime), reload it
        if self.proxy is not None:  # type: ignore[attr-defined]
            bounce_receiver = getattr(self.proxy, "bounce_receiver", None)  # type: ignore[attr-defined]
            if bounce_receiver is not None:
                await bounce_receiver.reload_config()

        return {"ok": True, "enabled": enabled, "message": "Config reloaded"}


__all__ = ["InstanceEndpoint_EE"]
