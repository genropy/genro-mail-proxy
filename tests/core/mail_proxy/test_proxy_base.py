# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for MailProxyBase - foundation layer tests."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock
import pytest

from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


class TestMailProxyBaseEncryptionKey:
    """Tests for encryption key loading and management."""

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_encryption_key_none_when_not_configured(self, mock_db_cls):
        """Encryption key is None when no env var or secrets file."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            assert proxy.encryption_key is None

    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_encryption_key_from_env(self, mock_db_cls):
        """Load encryption key from environment variable."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        valid_key = b"0123456789abcdef0123456789abcdef"
        key_b64 = base64.b64encode(valid_key).decode()
        with patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": key_b64}):
            with patch("pathlib.Path.exists", return_value=False):
                proxy = MailProxyBase()
                assert proxy.encryption_key == valid_key

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_encryption_key_from_secrets_file(self, mock_db_cls):
        """Load encryption key from Docker/K8s secrets file."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        valid_key = b"0123456789abcdef0123456789abcdef"

        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_bytes", return_value=valid_key + b"\n"):
                proxy = MailProxyBase()
                assert proxy.encryption_key == valid_key

    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_encryption_key_env_invalid_base64(self, mock_db_cls):
        """Invalid base64 in env var is ignored."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": "not-valid-base64!"}):
            with patch("pathlib.Path.exists", return_value=False):
                proxy = MailProxyBase()
                assert proxy.encryption_key is None

    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_encryption_key_env_wrong_size(self, mock_db_cls):
        """Key from env with wrong size is ignored."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        short_key = b"too-short"
        key_b64 = base64.b64encode(short_key).decode()
        with patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": key_b64}):
            with patch("pathlib.Path.exists", return_value=False):
                proxy = MailProxyBase()
                assert proxy.encryption_key is None

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_encryption_key_secrets_file_wrong_size(self, mock_db_cls):
        """Key from secrets file with wrong size is ignored."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("pathlib.Path.exists", return_value=True):
            with patch("pathlib.Path.read_bytes", return_value=b"short"):
                proxy = MailProxyBase()
                assert proxy.encryption_key is None

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_set_encryption_key(self, mock_db_cls):
        """set_encryption_key sets key programmatically."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            assert proxy.encryption_key is None
            valid_key = b"0123456789abcdef0123456789abcdef"
            proxy.set_encryption_key(valid_key)
            assert proxy.encryption_key == valid_key

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_set_encryption_key_wrong_size_raises(self, mock_db_cls):
        """set_encryption_key raises for wrong size key."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            with pytest.raises(ValueError, match="must be 32 bytes"):
                proxy.set_encryption_key(b"short")


class TestMailProxyBaseEndpoint:
    """Tests for endpoint() method."""

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_endpoint_not_found_raises(self, mock_db_cls):
        """endpoint() raises ValueError for unknown endpoint."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            with pytest.raises(ValueError, match="not found"):
                proxy.endpoint("nonexistent")


class TestMailProxyBaseDiscovery:
    """Tests for table and endpoint discovery."""

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_find_entity_modules_import_error(self, mock_db_cls):
        """_find_entity_modules returns empty dict on ImportError."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            result = proxy._find_entity_modules("nonexistent.package", "table")
            assert result == {}

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_find_entity_modules_no_path(self, mock_db_cls):
        """_find_entity_modules returns empty dict when package has no __path__."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_module = MagicMock()
        del mock_module.__path__  # Ensure no __path__
        with patch("pathlib.Path.exists", return_value=False):
            with patch("importlib.import_module", return_value=mock_module):
                proxy = MailProxyBase()
                result = proxy._find_entity_modules("fake.package", "table")
                assert result == {}

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_find_entity_modules_skips_non_packages(self, mock_db_cls):
        """_find_entity_modules skips non-package modules."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_package = MagicMock()
        mock_package.__path__ = ["/fake/path"]
        with patch("pathlib.Path.exists", return_value=False):
            with patch("importlib.import_module", return_value=mock_package):
                with patch("pkgutil.iter_modules", return_value=[
                    (None, "not_a_package", False),  # is_pkg=False
                ]):
                    proxy = MailProxyBase()
                    result = proxy._find_entity_modules("fake.package", "table")
                    assert result == {}

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_find_entity_modules_import_error_on_submodule(self, mock_db_cls):
        """_find_entity_modules handles ImportError on submodule import."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            # Test directly with nonexistent entity module
            result = proxy._find_entity_modules("core.mail_proxy.entities", "nonexistent_module")
            assert result == {}

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_get_class_from_module_no_table(self, mock_db_cls):
        """_get_class_from_module returns None when no Table class found."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        import types
        mock_module = types.SimpleNamespace()
        mock_module.SomeOtherClass = "not a class"
        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            result = proxy._get_class_from_module(mock_module, "Table")
            assert result is None

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_get_class_from_module_skips_base_table(self, mock_db_cls):
        """_get_class_from_module skips 'Table' base class."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        class Table:
            name = "base"

        mock_module = MagicMock()
        mock_module.Table = Table

        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            # Only 'Table' exists, should be skipped
            result = proxy._get_class_from_module(mock_module, "Table")
            assert result is None

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_get_class_from_module_skips_private(self, mock_db_cls):
        """_get_class_from_module skips private classes."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        class _PrivateTable:
            name = "private"

        mock_module = MagicMock()
        mock_module._PrivateTable = _PrivateTable

        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            result = proxy._get_class_from_module(mock_module, "Table")
            assert result is None

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_get_ee_mixin_from_module(self, mock_db_cls):
        """_get_ee_mixin_from_module extracts EE mixin class."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db

        class AccountsTable_EE:
            pass

        mock_module = MagicMock()
        mock_module.AccountsTable_EE = AccountsTable_EE

        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            result = proxy._get_ee_mixin_from_module(mock_module, "_EE")
            assert result is AccountsTable_EE

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_get_ee_mixin_from_module_skips_private(self, mock_db_cls):
        """_get_ee_mixin_from_module skips private classes."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        mock_module = MagicMock()
        # Only private class
        mock_module._SomePrivateEE = type("_SomePrivateEE", (), {})

        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            result = proxy._get_ee_mixin_from_module(mock_module, "_EE")
            assert result is None


class TestMailProxyBaseInit:
    """Tests for init() method and migrations."""

    async def test_init_runs_migrations(self, tmp_path):
        """init() runs legacy schema migrations."""
        proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
        await proxy.init()
        # Verify tables are accessible
        tenants = await proxy.db.table("tenants").list_all()
        assert isinstance(tenants, list)
        await proxy.close()

    async def test_init_logs_migration_messages(self, tmp_path, caplog):
        """init() logs migration messages when migrations run."""
        import logging
        caplog.set_level(logging.INFO)

        # Create legacy schema with all required columns
        import aiosqlite
        db_path = str(tmp_path / "legacy.db")
        async with aiosqlite.connect(db_path) as conn:
            await conn.execute("""
                CREATE TABLE accounts (
                    id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    host TEXT NOT NULL,
                    port INTEGER NOT NULL,
                    user TEXT,
                    password TEXT,
                    ttl INTEGER DEFAULT 300,
                    limit_per_minute INTEGER,
                    limit_per_hour INTEGER,
                    limit_per_day INTEGER,
                    limit_behavior TEXT,
                    use_tls INTEGER,
                    batch_size INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_pec_account INTEGER DEFAULT 0,
                    imap_host TEXT,
                    imap_port INTEGER DEFAULT 993,
                    imap_user TEXT,
                    imap_password TEXT,
                    imap_folder TEXT DEFAULT 'INBOX',
                    imap_last_uid INTEGER,
                    imap_last_sync TIMESTAMP,
                    imap_uidvalidity INTEGER,
                    PRIMARY KEY (tenant_id, id)
                )
            """)
            await conn.execute("""
                CREATE TABLE messages (
                    id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    account_id TEXT,
                    batch_code TEXT,
                    payload TEXT,
                    status TEXT DEFAULT 'pending',
                    priority INTEGER DEFAULT 2,
                    deferred_ts INTEGER,
                    smtp_ts INTEGER,
                    error TEXT,
                    is_pec INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (tenant_id, id)
                )
            """)
            await conn.commit()

        proxy = MailProxyBase(ProxyConfig(db_path=db_path))
        await proxy.init()
        await proxy.close()

        assert "Migrated accounts table" in caplog.text
        assert "Migrated messages table" in caplog.text


class TestMailProxyBaseInitEdition:
    """Tests for _init_edition() method."""

    async def test_init_edition_ce_mode_creates_default_tenant(self, tmp_path):
        """In CE mode with no tenants, creates default tenant."""
        with patch("core.mail_proxy.HAS_ENTERPRISE", False):
            proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "ce.db")))
            await proxy.init()
            tenants = await proxy.db.table("tenants").list_all()
            assert len(tenants) == 1
            assert tenants[0]["id"] == "default"

            edition = await proxy.db.table("instance").get_edition()
            assert edition == "ce"

            await proxy.close()

    async def test_init_edition_ee_mode_with_enterprise(self, tmp_path):
        """In EE mode with enterprise package, sets EE edition."""
        with patch("core.mail_proxy.HAS_ENTERPRISE", True):
            proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "ee.db")))
            await proxy.init()
            # No default tenant in EE mode
            tenants = await proxy.db.table("tenants").list_all()
            assert len(tenants) == 0

            edition = await proxy.db.table("instance").get_edition()
            assert edition == "ee"

            await proxy.close()

    async def test_init_edition_ee_mode_multiple_tenants(self, tmp_path):
        """With multiple tenants, sets EE edition."""
        proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "multi.db")))
        await proxy.db.connect()
        await proxy.db.check_structure()

        # Create multiple tenants
        await proxy.db.table("tenants").add({"id": "t1", "name": "Tenant 1"})
        await proxy.db.table("tenants").add({"id": "t2", "name": "Tenant 2"})

        # Run edition detection
        await proxy._init_edition()

        edition = await proxy.db.table("instance").get_edition()
        assert edition == "ee"

        await proxy.close()

    async def test_init_edition_ee_mode_non_default_tenant(self, tmp_path):
        """With single non-default tenant, sets EE edition."""
        proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "custom.db")))
        await proxy.db.connect()
        await proxy.db.check_structure()

        # Create single non-default tenant
        await proxy.db.table("tenants").add({"id": "custom", "name": "Custom Tenant"})

        # Run edition detection
        await proxy._init_edition()

        edition = await proxy.db.table("instance").get_edition()
        assert edition == "ee"

        await proxy.close()


class TestMailProxyBaseCli:
    """Tests for CLI creation."""

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_cli_property_creates_cli(self, mock_db_cls):
        """cli property creates Click CLI on first access."""
        mock_db = MagicMock()
        mock_db_cls.return_value = mock_db
        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            cli = proxy.cli
            assert cli is not None
            # Second access returns same instance
            assert proxy.cli is cli


class TestMailProxyBaseApi:
    """Tests for API creation."""

    @patch.dict("os.environ", {"MAIL_PROXY_ENCRYPTION_KEY": ""}, clear=True)
    @patch("core.mail_proxy.proxy_base.SqlDb")
    def test_api_property_creates_api(self, mock_db_cls):
        """api property creates FastAPI app on first access."""
        mock_db = MagicMock()
        mock_db.tables = {}
        mock_db_cls.return_value = mock_db
        with patch("pathlib.Path.exists", return_value=False):
            proxy = MailProxyBase()
            api = proxy.api
            assert api is not None
            # Second access returns same instance
            assert proxy.api is api
