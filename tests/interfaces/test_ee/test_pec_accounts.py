# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for PEC account endpoints (Enterprise Edition).

These tests verify PEC-specific functionality via HTTP client:
    Client HTTP → FastAPI → AccountEndpoint_EE → AccountsTable_EE → DB
"""

import pytest

from tools.http_client.client import MailProxyClient, Account


@pytest.mark.ee
class TestPECAccountsAPI:
    """Test PEC account management through MailProxyClient (EE)."""

    # =========================================================================
    # PEC Account CRUD
    # =========================================================================

    async def test_add_pec_account(self, client: MailProxyClient, setup_tenant):
        """Add a PEC account with IMAP configuration."""
        result = await client.accounts.add_pec(
            id="pec-account-1",
            tenant_id=setup_tenant,
            host="smtps.pec.provider.it",
            port=465,
            imap_host="imaps.pec.provider.it",
            imap_port=993,
            user="user@pec.example.it",
            password="secret",
        )

        assert result["id"] == "pec-account-1"
        assert result.get("is_pec_account") is True

    async def test_add_pec_account_full_config(self, client: MailProxyClient, setup_tenant):
        """Add PEC account with full IMAP configuration."""
        result = await client.accounts.add_pec(
            id="pec-full",
            tenant_id=setup_tenant,
            host="smtps.pec.provider.it",
            port=465,
            imap_host="imaps.pec.provider.it",
            imap_port=993,
            imap_user="imap@pec.example.it",
            imap_password="imap-secret",
            imap_folder="INBOX",
            imap_ssl=True,
            user="smtp@pec.example.it",
            password="smtp-secret",
            use_tls=True,
        )

        assert result["id"] == "pec-full"

    async def test_list_pec_accounts(self, client: MailProxyClient, setup_tenant):
        """List all PEC accounts across all tenants."""
        # Create a PEC account first
        await client.accounts.add_pec(
            id="pec-list-test",
            tenant_id=setup_tenant,
            host="smtp.pec.it",
            port=465,
            imap_host="imap.pec.it",
        )

        pec_accounts = await client.accounts.list_pec()

        assert isinstance(pec_accounts, list)
        assert all(isinstance(a, Account) for a in pec_accounts)
        # All returned accounts should be PEC accounts
        for account in pec_accounts:
            assert account.is_pec_account is True

    async def test_get_pec_ids(self, client: MailProxyClient, setup_tenant):
        """Get set of account IDs that are PEC accounts."""
        # Create a PEC account
        await client.accounts.add_pec(
            id="pec-ids-test",
            tenant_id=setup_tenant,
            host="smtp.pec.it",
            port=465,
            imap_host="imap.pec.it",
        )

        pec_ids = await client.accounts.get_pec_ids()

        assert isinstance(pec_ids, set)
        assert "pec-ids-test" in pec_ids

    # =========================================================================
    # PEC vs Regular Account Distinction
    # =========================================================================

    async def test_pec_and_regular_accounts_coexist(self, client: MailProxyClient, setup_tenant):
        """PEC and regular accounts can coexist for same tenant."""
        # Create regular account
        await client.accounts.add(
            id="regular-smtp",
            tenant_id=setup_tenant,
            host="smtp.regular.com",
            port=587,
        )

        # Create PEC account
        await client.accounts.add_pec(
            id="pec-smtp",
            tenant_id=setup_tenant,
            host="smtp.pec.it",
            port=465,
            imap_host="imap.pec.it",
        )

        # List all accounts
        all_accounts = await client.accounts.list(tenant_id=setup_tenant)
        assert len(all_accounts) >= 2

        # List only PEC accounts
        pec_accounts = await client.accounts.list_pec()
        pec_ids = {a.id for a in pec_accounts if a.tenant_id == setup_tenant}

        assert "pec-smtp" in pec_ids
        assert "regular-smtp" not in pec_ids

    async def test_regular_account_not_in_pec_list(self, client: MailProxyClient, setup_tenant):
        """Regular accounts don't appear in PEC account list."""
        await client.accounts.add(
            id="not-pec",
            tenant_id=setup_tenant,
            host="smtp.regular.com",
            port=587,
        )

        pec_ids = await client.accounts.get_pec_ids()

        assert "not-pec" not in pec_ids
