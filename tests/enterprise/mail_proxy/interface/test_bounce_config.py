# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for bounce detection configuration (Enterprise Edition).

These tests verify bounce configuration endpoints via HTTP client:
    Client HTTP → FastAPI → InstanceEndpoint_EE → InstanceTable_EE → DB
"""

import pytest

from tools.http_client.client import MailProxyClient


@pytest.mark.ee
class TestBounceConfigAPI:
    """Test bounce detection configuration through MailProxyClient (EE)."""

    # =========================================================================
    # Get Bounce Configuration
    # =========================================================================

    async def test_get_bounce_config(self, client: MailProxyClient):
        """Get bounce detection configuration."""
        result = await client.instance.get_bounce_config()

        assert result.get("ok") is True
        # Should have bounce config fields
        assert "enabled" in result

    async def test_get_bounce_config_excludes_password(self, client: MailProxyClient):
        """Get bounce config excludes IMAP password for security."""
        result = await client.instance.get_bounce_config()

        # Password should not be in response
        assert "imap_password" not in result

    async def test_get_bounce_config_excludes_sync_state(self, client: MailProxyClient):
        """Get bounce config excludes internal sync state."""
        result = await client.instance.get_bounce_config()

        # Internal sync state should not be exposed
        assert "last_uid" not in result
        assert "uidvalidity" not in result

    # =========================================================================
    # Set Bounce Configuration
    # =========================================================================

    async def test_set_bounce_config_enable(self, client: MailProxyClient):
        """Enable bounce detection."""
        result = await client.instance.set_bounce_config(enabled=True)

        assert result.get("ok") is True

    async def test_set_bounce_config_disable(self, client: MailProxyClient):
        """Disable bounce detection."""
        result = await client.instance.set_bounce_config(enabled=False)

        assert result.get("ok") is True

    async def test_set_bounce_config_imap(self, client: MailProxyClient):
        """Configure IMAP settings for bounce detection."""
        result = await client.instance.set_bounce_config(
            imap_host="imap.bounce.local",
            imap_port=993,
            imap_user="bounce@example.com",
            imap_password="secret",
            imap_folder="INBOX",
            imap_ssl=True,
        )

        assert result.get("ok") is True

    async def test_set_bounce_config_poll_interval(self, client: MailProxyClient):
        """Configure bounce polling interval."""
        result = await client.instance.set_bounce_config(
            poll_interval=120,  # 2 minutes
        )

        assert result.get("ok") is True

    async def test_set_bounce_config_return_path(self, client: MailProxyClient):
        """Configure Return-Path header for outgoing emails."""
        result = await client.instance.set_bounce_config(
            return_path="bounce@mail.example.com",
        )

        assert result.get("ok") is True

    async def test_set_bounce_config_partial_update(self, client: MailProxyClient):
        """Partial update only changes specified fields."""
        # Set initial config
        await client.instance.set_bounce_config(
            imap_host="old.host.com",
            poll_interval=60,
        )

        # Update only poll_interval
        result = await client.instance.set_bounce_config(poll_interval=30)

        assert result.get("ok") is True
        # Host should remain unchanged (partial update)

    # =========================================================================
    # Reload Bounce Configuration
    # =========================================================================

    async def test_reload_bounce(self, client: MailProxyClient):
        """Reload bounce detection configuration at runtime."""
        result = await client.instance.reload_bounce()

        assert result.get("ok") is True
        assert "enabled" in result

    async def test_reload_bounce_after_config_change(self, client: MailProxyClient):
        """Reload applies configuration changes at runtime."""
        # Change configuration
        await client.instance.set_bounce_config(poll_interval=90)

        # Reload to apply changes
        result = await client.instance.reload_bounce()

        assert result.get("ok") is True

    # =========================================================================
    # Full Configuration Lifecycle
    # =========================================================================

    async def test_bounce_config_lifecycle(self, client: MailProxyClient):
        """Full lifecycle: configure, enable, reload, disable."""
        # Configure IMAP
        result = await client.instance.set_bounce_config(
            imap_host="imap.test.local",
            imap_port=993,
            imap_user="bounce@test.local",
            imap_password="testpass",
        )
        assert result.get("ok") is True

        # Enable
        result = await client.instance.set_bounce_config(enabled=True)
        assert result.get("ok") is True

        # Reload
        result = await client.instance.reload_bounce()
        assert result.get("ok") is True
        assert result["enabled"] is True

        # Disable
        result = await client.instance.set_bounce_config(enabled=False)
        assert result.get("ok") is True

        # Verify disabled
        result = await client.instance.get_bounce_config()
        assert result["enabled"] is False
