# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for InstanceTable - singleton instance configuration."""

import pytest

from mail_proxy.mailproxy_db import MailProxyDb


@pytest.mark.asyncio
async def test_instance_ensure_creates_singleton(tmp_path):
    """ensure_instance creates the singleton row if it doesn't exist."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # First call creates the instance
    instance = await p.instance.ensure_instance()
    assert instance is not None
    assert instance["id"] == 1
    assert instance["name"] == "mail-proxy"  # default value

    # Second call returns the same instance
    instance2 = await p.instance.ensure_instance()
    assert instance2["id"] == 1


@pytest.mark.asyncio
async def test_instance_created_by_init_db(tmp_path):
    """init_db creates instance singleton with edition set."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    instance = await p.instance.get_instance()
    # init_db now creates instance via _init_edition()
    assert instance is not None
    assert instance["edition"] in ("ce", "ee")


@pytest.mark.asyncio
async def test_instance_update(tmp_path):
    """update_instance updates the singleton configuration."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    await p.instance.update_instance({"name": "my-proxy"})

    instance = await p.instance.get_instance()
    assert instance["name"] == "my-proxy"


@pytest.mark.asyncio
async def test_instance_name_accessors(tmp_path):
    """get_name and set_name work correctly."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # Default name
    name = await p.instance.get_name()
    assert name == "mail-proxy"

    # Set name
    await p.instance.set_name("custom-name")
    name = await p.instance.get_name()
    assert name == "custom-name"


@pytest.mark.asyncio
async def test_instance_api_token_accessors(tmp_path):
    """get_api_token and set_api_token work correctly."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # No token initially
    token = await p.instance.get_api_token()
    assert token is None

    # Set token
    await p.instance.set_api_token("secret-token-123")
    token = await p.instance.get_api_token()
    assert token == "secret-token-123"


@pytest.mark.asyncio
async def test_bounce_config_disabled_by_default(tmp_path):
    """Bounce detection is disabled by default."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    enabled = await p.instance.is_bounce_enabled()
    assert enabled is False


@pytest.mark.asyncio
async def test_bounce_config_get_and_set(tmp_path):
    """get_bounce_config and set_bounce_config work correctly."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # Set bounce config
    await p.instance.set_bounce_config(
        enabled=True,
        imap_host="imap.example.com",
        imap_port=993,
        imap_user="bounce@example.com",
        imap_password="secret",
        imap_folder="INBOX",
        return_path="bounces@example.com",
    )

    # Verify enabled
    assert await p.instance.is_bounce_enabled() is True

    # Get full config
    config = await p.instance.get_bounce_config()
    assert config["enabled"] is True
    assert config["imap_host"] == "imap.example.com"
    assert config["imap_port"] == 993
    assert config["imap_user"] == "bounce@example.com"
    assert config["imap_folder"] == "INBOX"
    assert config["return_path"] == "bounces@example.com"
    assert config["last_uid"] is None
    assert config["uidvalidity"] is None


@pytest.mark.asyncio
async def test_bounce_config_partial_update(tmp_path):
    """set_bounce_config with partial values only updates specified fields."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # Initial setup
    await p.instance.set_bounce_config(
        enabled=True,
        imap_host="imap.example.com",
        imap_user="user@example.com",
    )

    # Partial update - only change host
    await p.instance.set_bounce_config(imap_host="imap.newhost.com")

    config = await p.instance.get_bounce_config()
    assert config["imap_host"] == "imap.newhost.com"
    assert config["imap_user"] == "user@example.com"  # unchanged
    assert config["enabled"] is True  # unchanged


@pytest.mark.asyncio
async def test_bounce_sync_state_update(tmp_path):
    """update_bounce_sync_state updates IMAP sync state."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # Update sync state
    await p.instance.update_bounce_sync_state(
        last_uid=12345,
        last_sync=1700000000,
        uidvalidity=987654321,
    )

    config = await p.instance.get_bounce_config()
    assert config["last_uid"] == 12345
    assert config["last_sync"] is not None
    assert config["uidvalidity"] == 987654321


@pytest.mark.asyncio
async def test_bounce_sync_state_without_uidvalidity(tmp_path):
    """update_bounce_sync_state works without uidvalidity."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # Set initial uidvalidity
    await p.instance.update_bounce_sync_state(
        last_uid=100,
        last_sync=1700000000,
        uidvalidity=999,
    )

    # Update without uidvalidity - should not change it
    await p.instance.update_bounce_sync_state(
        last_uid=200,
        last_sync=1700001000,
    )

    config = await p.instance.get_bounce_config()
    assert config["last_uid"] == 200
    assert config["uidvalidity"] == 999  # unchanged


@pytest.mark.asyncio
async def test_disable_bounce(tmp_path):
    """Bounce detection can be disabled."""
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()

    # Enable first
    await p.instance.set_bounce_config(enabled=True, imap_host="imap.example.com")
    assert await p.instance.is_bounce_enabled() is True

    # Disable
    await p.instance.set_bounce_config(enabled=False)
    assert await p.instance.is_bounce_enabled() is False

    # Config should still have the host
    config = await p.instance.get_bounce_config()
    assert config["imap_host"] == "imap.example.com"
