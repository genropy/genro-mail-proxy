# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Tests for AccountsTable EE fields (PEC/IMAP)."""

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


class TestAccountsTablePecFields:
    """Tests for PEC/IMAP fields in AccountsTable (EE)."""

    async def test_add_pec_account(self, db):
        """Add account with PEC/IMAP configuration."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "pec1",
            "tenant_id": "t1",
            "host": "smtp.pec.it",
            "port": 465,
            "is_pec_account": True,
            "imap_host": "imap.pec.it",
            "imap_port": 993,
            "imap_user": "pecuser",
            "imap_password": "pecpass",
            "imap_folder": "INBOX",
        })
        account = await accounts.get("t1", "pec1")
        assert account["is_pec_account"] == 1  # stored as int
        assert account["imap_host"] == "imap.pec.it"
        assert account["imap_port"] == 993

    async def test_list_decodes_is_pec_account(self, db):
        """list_all decodes is_pec_account to bool."""
        accounts = db.table("accounts")
        await accounts.add({
            "id": "pec1",
            "tenant_id": "t1",
            "host": "smtp.pec.it",
            "port": 465,
            "is_pec_account": True,
            "imap_host": "imap.pec.it",
        })
        result = await accounts.list_all(tenant_id="t1")
        assert result[0]["is_pec_account"] is True
