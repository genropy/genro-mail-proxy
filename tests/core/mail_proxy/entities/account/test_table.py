# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for AccountsTable - all table methods."""

import pytest

from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with all tables initialized."""
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
    await proxy.init()
    # Create tenant for FK constraint
    await proxy.db.table("tenants").add({"id": "t1", "name": "Test Tenant"})
    yield proxy.db
    await proxy.close()


class TestAccountsTableAdd:
    """Tests for AccountsTable.add() method."""

    async def test_add_new_account(self, db):
        """Add a new SMTP account."""
        accounts = db.table("accounts")
        pk = await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })
        assert pk is not None
        assert len(pk) == 22  # Short UUID format

    async def test_add_account_with_all_fields(self, db):
        """Add account with all optional fields."""
        accounts = db.table("accounts")
        pk = await accounts.add({
            "id": "smtp2",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 465,
            "user": "testuser",
            "password": "secret",
            "use_tls": True,
            "ttl": 600,
            "batch_size": 100,
            "limit_per_minute": 10,
            "limit_per_hour": 100,
            "limit_per_day": 1000,
            "limit_behavior": "reject",
        })
        account = await accounts.get("t1", "smtp2")
        assert account["user"] == "testuser"
        assert account["use_tls"] is True
        assert account["ttl"] == 600
        assert account["limit_behavior"] == "reject"

    async def test_add_updates_existing_account(self, db):
        """Adding same account_id updates it (upsert)."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.old.com",
            "port": 25,
        })
        await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.new.com",
            "port": 587,
        })
        account = await accounts.get("t1", "smtp1")
        assert account["host"] == "smtp.new.com"
        assert account["port"] == 587

    async def test_add_use_tls_false(self, db):
        """use_tls=False is stored correctly."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "notls",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 25,
            "use_tls": False,
        })
        account = await accounts.get("t1", "notls")
        assert account["use_tls"] is False

    async def test_add_use_tls_none(self, db):
        """use_tls=None is stored as None."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "notls",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 25,
            "use_tls": None,
        })
        account = await accounts.get("t1", "notls")
        assert account["use_tls"] is None


class TestAccountsTableGet:
    """Tests for AccountsTable.get() method."""

    async def test_get_existing_account(self, db):
        """Get an existing account."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })
        account = await accounts.get("t1", "smtp1")
        assert account["id"] == "smtp1"
        assert account["tenant_id"] == "t1"
        assert account["host"] == "smtp.example.com"

    async def test_get_nonexistent_account_raises(self, db):
        """Get non-existent account raises ValueError."""
        accounts = db.table("accounts")
        with pytest.raises(ValueError, match="not found"):
            await accounts.get("t1", "nonexistent")

    async def test_get_wrong_tenant_raises(self, db):
        """Get account with wrong tenant_id raises."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "smtp1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })
        with pytest.raises(ValueError, match="not found"):
            await accounts.get("wrong_tenant", "smtp1")


class TestAccountsTableListAll:
    """Tests for AccountsTable.list_all() method."""

    async def test_list_all_empty(self, db):
        """List returns empty when no accounts."""
        accounts = db.table("accounts")
        result = await accounts.list_all(tenant_id="t1")
        assert result == []

    async def test_list_all_by_tenant(self, db):
        """List accounts filtered by tenant."""
        accounts = db.table("accounts")
        await accounts.add({"id": "a1", "tenant_id": "t1", "host": "h1", "port": 25})
        await accounts.add({"id": "a2", "tenant_id": "t1", "host": "h2", "port": 25})
        # Create another tenant
        await db.table("tenants").insert({"id": "t2", "name": "Tenant 2", "active": 1})
        await accounts.add({"id": "a3", "tenant_id": "t2", "host": "h3", "port": 25})

        result = await accounts.list_all(tenant_id="t1")
        assert len(result) == 2
        ids = [a["id"] for a in result]
        assert "a1" in ids
        assert "a2" in ids
        assert "a3" not in ids

    async def test_list_all_no_filter(self, db):
        """List all accounts without tenant filter."""
        accounts = db.table("accounts")
        await accounts.add({"id": "a1", "tenant_id": "t1", "host": "h1", "port": 25})
        await db.table("tenants").insert({"id": "t2", "name": "Tenant 2", "active": 1})
        await accounts.add({"id": "a2", "tenant_id": "t2", "host": "h2", "port": 25})

        result = await accounts.list_all()
        assert len(result) == 2

    async def test_list_all_ordered_by_id(self, db):
        """List returns accounts ordered by id."""
        accounts = db.table("accounts")
        await accounts.add({"id": "z", "tenant_id": "t1", "host": "h", "port": 25})
        await accounts.add({"id": "a", "tenant_id": "t1", "host": "h", "port": 25})
        await accounts.add({"id": "m", "tenant_id": "t1", "host": "h", "port": 25})

        result = await accounts.list_all(tenant_id="t1")
        ids = [a["id"] for a in result]
        assert ids == ["a", "m", "z"]


class TestAccountsTableRemove:
    """Tests for AccountsTable.remove() method."""

    async def test_remove_existing_account(self, db):
        """Remove an existing account."""
        accounts = db.table("accounts")
        await accounts.add({"id": "smtp1", "tenant_id": "t1", "host": "h", "port": 25})
        await accounts.remove("t1", "smtp1")

        with pytest.raises(ValueError, match="not found"):
            await accounts.get("t1", "smtp1")

    async def test_remove_nonexistent_no_error(self, db):
        """Remove non-existent account doesn't raise."""
        accounts = db.table("accounts")
        # Should not raise
        await accounts.remove("t1", "nonexistent")

    async def test_remove_wrong_tenant_no_effect(self, db):
        """Remove with wrong tenant doesn't affect account."""
        accounts = db.table("accounts")
        await accounts.add({"id": "smtp1", "tenant_id": "t1", "host": "h", "port": 25})
        await accounts.remove("wrong_tenant", "smtp1")

        # Account still exists
        account = await accounts.get("t1", "smtp1")
        assert account["id"] == "smtp1"


class TestAccountsTableSyncSchema:
    """Tests for AccountsTable.sync_schema() method."""

    async def test_sync_schema_creates_index(self, db):
        """sync_schema creates unique index."""
        accounts = db.table("accounts")
        await accounts.sync_schema()
        # Should not raise on duplicate check
        await accounts.add({"id": "a1", "tenant_id": "t1", "host": "h", "port": 25})

        # Try to insert duplicate via raw SQL - should fail
        with pytest.raises(Exception):
            await db.adapter.execute(
                "INSERT INTO accounts (pk, id, tenant_id, host, port) VALUES ('pk2', 'a1', 't1', 'h', 25)"
            )


class TestAccountsTableMigration:
    """Tests for AccountsTable.migrate_from_legacy_schema() method."""

    async def test_migration_skips_when_pk_exists(self, db):
        """Migration returns False when pk column already exists."""
        accounts = db.table("accounts")
        # pk column exists in current schema
        result = await accounts.migrate_from_legacy_schema()
        assert result is False

    async def test_migration_skips_when_table_not_exists(self, tmp_path):
        """Migration returns False when accounts table doesn't exist."""
        from core.mail_proxy.proxy_base import MailProxyBase
        from core.mail_proxy.proxy_config import ProxyConfig

        # Create a fresh database without initializing tables
        db_path = str(tmp_path / "empty.db")
        proxy = MailProxyBase(ProxyConfig(db_path=db_path))
        # Don't call init() - just connect to create the db file
        await proxy.db.adapter.connect()

        accounts = proxy.db.table("accounts")
        # Table doesn't exist
        result = await accounts.migrate_from_legacy_schema()
        assert result is False

        await proxy.close()

    async def test_migration_from_legacy_schema(self, tmp_path):
        """Migration converts legacy composite PK to UUID PK."""
        from core.mail_proxy.proxy_base import MailProxyBase
        from core.mail_proxy.proxy_config import ProxyConfig

        db_path = str(tmp_path / "legacy.db")
        proxy = MailProxyBase(ProxyConfig(db_path=db_path))
        await proxy.db.adapter.connect()

        # Create legacy schema (no pk column)
        await proxy.db.adapter.execute("""
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

        # Insert legacy data
        await proxy.db.adapter.execute(
            """INSERT INTO accounts
               (id, tenant_id, host, port, user, is_pec_account)
               VALUES ('acc1', 't1', 'smtp.example.com', 587, 'user1', 0)"""
        )
        await proxy.db.adapter.execute(
            """INSERT INTO accounts
               (id, tenant_id, host, port, user, is_pec_account)
               VALUES ('acc2', 't1', 'smtp2.example.com', 465, 'user2', 1)"""
        )

        accounts = proxy.db.table("accounts")
        result = await accounts.migrate_from_legacy_schema()

        # Migration should have been performed
        assert result is True

        # Verify pk column now exists
        row = await proxy.db.adapter.fetch_one("SELECT pk FROM accounts LIMIT 1")
        assert row is not None
        assert row["pk"] is not None
        assert len(row["pk"]) == 22  # Short UUID format

        # Verify data was preserved
        rows = await proxy.db.adapter.fetch_all("SELECT * FROM accounts ORDER BY id")
        assert len(rows) == 2
        assert rows[0]["id"] == "acc1"
        assert rows[0]["host"] == "smtp.example.com"
        assert rows[1]["id"] == "acc2"
        assert rows[1]["is_pec_account"] == 1

        # Verify UNIQUE constraint on (tenant_id, id) still works
        with pytest.raises(Exception):
            await proxy.db.adapter.execute(
                """INSERT INTO accounts
                   (pk, id, tenant_id, host, port)
                   VALUES ('pk99', 'acc1', 't1', 'dup.com', 25)"""
            )

        await proxy.close()


class TestAccountsTableDecodeBoolean:
    """Tests for boolean field decoding in AccountsTable."""

    async def test_list_all_decodes_is_pec_account(self, db):
        """list_all decodes is_pec_account to boolean."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "pec1",
            "tenant_id": "t1",
            "host": "pec.example.com",
            "port": 465,
            "is_pec_account": True,
        })

        result = await accounts.list_all(tenant_id="t1")
        assert len(result) == 1
        assert result[0]["is_pec_account"] is True

    async def test_list_all_decodes_is_pec_account_false(self, db):
        """list_all decodes is_pec_account=0 to False."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "regular",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
            "is_pec_account": False,
        })

        result = await accounts.list_all(tenant_id="t1")
        assert len(result) == 1
        assert result[0]["is_pec_account"] is False


class TestAccountsTableImapFields:
    """Tests for IMAP fields in AccountsTable (EE fields)."""

    async def test_add_with_imap_fields(self, db):
        """Add account with IMAP configuration."""
        accounts = db.table("accounts")
        pk = await accounts.add({
            "id": "pec1",
            "tenant_id": "t1",
            "host": "smtp.pec.example.com",
            "port": 465,
            "user": "user@pec.example.com",
            "password": "secret",
            "is_pec_account": True,
            "imap_host": "imap.pec.example.com",
            "imap_port": 993,
            "imap_user": "imap_user@pec.example.com",
            "imap_password": "imap_secret",
            "imap_folder": "INBOX/Receipts",
        })

        account = await accounts.get("t1", "pec1")
        assert account["imap_host"] == "imap.pec.example.com"
        assert account["imap_port"] == 993
        assert account["imap_user"] == "imap_user@pec.example.com"
        assert account["imap_folder"] == "INBOX/Receipts"

    async def test_add_imap_user_defaults_to_user(self, db):
        """IMAP user defaults to SMTP user if not specified."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "pec2",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 465,
            "user": "smtp_user",
            "password": "smtp_pass",
            "imap_host": "imap.example.com",
            # imap_user not specified, should default to "user"
        })

        account = await accounts.get("t1", "pec2")
        assert account["imap_user"] == "smtp_user"
