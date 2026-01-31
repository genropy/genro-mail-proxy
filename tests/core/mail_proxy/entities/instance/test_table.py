# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for InstanceTable - all table methods."""

import pytest

from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with schema only (no init logic).

    Uses check_structure() instead of init() to test table methods
    in isolation without init's edition auto-detection logic.
    """
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
    await proxy.db.connect()
    await proxy.db.check_structure()
    yield proxy.db
    await proxy.close()


class TestInstanceTableGetInstance:
    """Tests for InstanceTable.get_instance() method."""

    async def test_get_instance_returns_none_when_empty(self, db):
        """get_instance returns None when no instance exists."""
        instance = db.table("instance")
        result = await instance.get_instance()
        assert result is None

    async def test_get_instance_returns_row_after_ensure(self, db):
        """get_instance returns row after ensure_instance."""
        instance = db.table("instance")
        await instance.ensure_instance()
        result = await instance.get_instance()
        assert result is not None
        assert result["id"] == 1

    async def test_get_instance_has_default_values(self, db):
        """get_instance returns column defaults."""
        instance = db.table("instance")
        await instance.ensure_instance()
        result = await instance.get_instance()
        assert result["name"] == "mail-proxy"
        assert result["edition"] == "ce"


class TestInstanceTableEnsureInstance:
    """Tests for InstanceTable.ensure_instance() method."""

    async def test_ensure_instance_creates_if_missing(self, db):
        """ensure_instance creates singleton if not exists."""
        instance = db.table("instance")
        result = await instance.ensure_instance()
        assert result is not None
        assert result["id"] == 1

    async def test_ensure_instance_returns_existing(self, db):
        """ensure_instance returns existing if already created."""
        instance = db.table("instance")
        # Update the existing instance (created by init_db)
        await instance.update_instance({"name": "existing"})
        result = await instance.ensure_instance()
        assert result["name"] == "existing"

    async def test_ensure_instance_idempotent(self, db):
        """Multiple calls to ensure_instance don't create duplicates."""
        instance = db.table("instance")
        await instance.ensure_instance()
        await instance.ensure_instance()
        await instance.ensure_instance()
        # Should still have only one row
        result = await instance.get_instance()
        assert result["id"] == 1


class TestInstanceTableUpdateInstance:
    """Tests for InstanceTable.update_instance() method."""

    async def test_update_instance_creates_if_missing(self, db):
        """update_instance creates singleton then updates."""
        instance = db.table("instance")
        await instance.update_instance({"name": "updated"})
        result = await instance.get_instance()
        assert result["name"] == "updated"

    async def test_update_instance_updates_existing(self, db):
        """update_instance modifies existing row."""
        instance = db.table("instance")
        # Singleton exists from init_db, just update it
        await instance.update_instance({"name": "modified"})
        result = await instance.get_instance()
        assert result["name"] == "modified"

    async def test_update_instance_sets_updated_at(self, db):
        """update_instance sets updated_at timestamp."""
        instance = db.table("instance")
        await instance.ensure_instance()
        original = await instance.get_instance()
        original_updated = original["updated_at"]

        await instance.update_instance({"name": "new"})
        result = await instance.get_instance()
        # updated_at should be set (may or may not be different depending on timing)
        assert result["updated_at"] is not None


class TestInstanceTableNameMethods:
    """Tests for InstanceTable name getter/setter."""

    async def test_get_name_default(self, db):
        """get_name returns default 'mail-proxy'."""
        instance = db.table("instance")
        name = await instance.get_name()
        assert name == "mail-proxy"

    async def test_get_name_custom(self, db):
        """get_name returns custom name after set."""
        instance = db.table("instance")
        await instance.set_name("my-instance")
        name = await instance.get_name()
        assert name == "my-instance"

    async def test_set_name(self, db):
        """set_name persists the name."""
        instance = db.table("instance")
        await instance.set_name("production-mailer")
        result = await instance.get_instance()
        assert result["name"] == "production-mailer"


class TestInstanceTableApiTokenMethods:
    """Tests for InstanceTable API token getter/setter."""

    async def test_get_api_token_default_none(self, db):
        """get_api_token returns None initially."""
        instance = db.table("instance")
        token = await instance.get_api_token()
        assert token is None

    async def test_set_api_token(self, db):
        """set_api_token persists the token."""
        instance = db.table("instance")
        await instance.set_api_token("secret-token-123")
        token = await instance.get_api_token()
        assert token == "secret-token-123"

    async def test_api_token_can_be_updated(self, db):
        """API token can be changed."""
        instance = db.table("instance")
        await instance.set_api_token("old-token")
        await instance.set_api_token("new-token")
        token = await instance.get_api_token()
        assert token == "new-token"


class TestInstanceTableEditionMethods:
    """Tests for InstanceTable edition management."""

    async def test_get_edition_default_ce(self, db):
        """get_edition returns 'ce' by default (set by init_db)."""
        instance = db.table("instance")
        edition = await instance.get_edition()
        assert edition == "ce"

    async def test_set_edition_ee(self, db):
        """set_edition can set to 'ee'."""
        instance = db.table("instance")
        await instance.set_edition("ee")
        edition = await instance.get_edition()
        assert edition == "ee"

    async def test_set_edition_back_to_ce(self, db):
        """set_edition can set back to 'ce'."""
        instance = db.table("instance")
        await instance.set_edition("ee")
        await instance.set_edition("ce")
        edition = await instance.get_edition()
        assert edition == "ce"

    async def test_set_edition_invalid_raises(self, db):
        """set_edition raises for invalid edition."""
        instance = db.table("instance")
        with pytest.raises(ValueError, match="Invalid edition"):
            await instance.set_edition("invalid")

    async def test_is_enterprise_false_by_default(self, db):
        """is_enterprise returns False for CE edition (set by init_db)."""
        instance = db.table("instance")
        assert await instance.is_enterprise() is False

    async def test_is_enterprise_true_for_ee(self, db):
        """is_enterprise returns True for EE edition."""
        instance = db.table("instance")
        await instance.set_edition("ee")
        assert await instance.is_enterprise() is True


class TestInstanceTableConfigMethods:
    """Tests for InstanceTable generic config getter/setter."""

    async def test_get_config_typed_key(self, db):
        """get_config returns typed column value for known keys."""
        instance = db.table("instance")
        await instance.set_name("typed-name")
        value = await instance.get_config("name")
        assert value == "typed-name"

    async def test_get_config_json_key(self, db):
        """get_config returns JSON config value for unknown keys."""
        instance = db.table("instance")
        await instance.set_config("custom_key", "custom_value")
        value = await instance.get_config("custom_key")
        assert value == "custom_value"

    async def test_get_config_default_value(self, db):
        """get_config returns default for missing key."""
        instance = db.table("instance")
        value = await instance.get_config("nonexistent", "default")
        assert value == "default"

    async def test_get_config_missing_returns_none(self, db):
        """get_config returns None for missing key without default."""
        instance = db.table("instance")
        await instance.ensure_instance()
        value = await instance.get_config("nonexistent")
        assert value is None

    async def test_set_config_typed_key(self, db):
        """set_config updates typed column for known keys."""
        instance = db.table("instance")
        await instance.set_config("name", "via-config")
        result = await instance.get_instance()
        assert result["name"] == "via-config"

    async def test_set_config_json_key(self, db):
        """set_config stores in JSON config for unknown keys."""
        instance = db.table("instance")
        await instance.set_config("host", "localhost")
        await instance.set_config("port", "8080")
        result = await instance.get_instance()
        config = result["config"]
        assert config["host"] == "localhost"
        assert config["port"] == "8080"

    async def test_get_all_config_empty(self, db):
        """get_all_config returns typed defaults when nothing set."""
        instance = db.table("instance")
        all_config = await instance.get_all_config()
        # Only typed keys with non-None values
        assert "name" in all_config
        assert all_config["name"] == "mail-proxy"

    async def test_get_all_config_merged(self, db):
        """get_all_config merges typed columns and JSON config."""
        instance = db.table("instance")
        await instance.set_name("merged-instance")
        await instance.set_config("custom", "value")
        all_config = await instance.get_all_config()
        assert all_config["name"] == "merged-instance"
        assert all_config["custom"] == "value"

    async def test_get_all_config_json_overrides_typed(self, db):
        """JSON config values override typed columns in get_all_config."""
        instance = db.table("instance")
        await instance.set_name("typed-name")
        # Manually set same key in JSON config
        row = await instance.ensure_instance()
        config = row.get("config") or {}
        config["name"] = "json-name"
        await instance.update_instance({"config": config})

        all_config = await instance.get_all_config()
        # JSON config should override
        assert all_config["name"] == "json-name"

