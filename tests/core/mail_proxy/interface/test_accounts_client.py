# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for AccountsAPI via HTTP client.

These tests verify the full pipeline:
    Client HTTP → FastAPI (api_base) → AccountEndpoint → AccountsTable → DB
"""

import pytest

from tools.http_client.client import MailProxyClient, Account


class TestAccountsAPI:
    """Test AccountsAPI through MailProxyClient."""

    # =========================================================================
    # Basic CRUD Operations
    # =========================================================================

    async def test_add_account_minimal(self, client: MailProxyClient, setup_tenant):
        """Add account with minimal required fields."""
        result = await client.accounts.add(
            id="smtp-minimal",
            tenant_id=setup_tenant,
            host="smtp.example.com",
            port=587,
        )

        assert result["id"] == "smtp-minimal"
        assert result["host"] == "smtp.example.com"
        assert result["port"] == 587

    async def test_add_account_full(self, client: MailProxyClient, setup_tenant):
        """Add account with all fields."""
        result = await client.accounts.add(
            id="smtp-full",
            tenant_id=setup_tenant,
            host="smtp.example.com",
            port=465,
            user="user@example.com",
            password="secret",
            use_tls=True,
            batch_size=50,
            ttl=600,
            limit_per_minute=10,
            limit_per_hour=100,
            limit_per_day=1000,
            limit_behavior="reject",
        )

        assert result["id"] == "smtp-full"
        assert result["user"] == "user@example.com"
        assert result["batch_size"] == 50
        assert result["limit_per_minute"] == 10

    async def test_get_account(self, client: MailProxyClient, setup_tenant):
        """Get account by tenant and account ID."""
        # Create account
        await client.accounts.add(
            id="smtp-get",
            tenant_id=setup_tenant,
            host="smtp.test.local",
            port=25,
        )

        # Get account
        account = await client.accounts.get(tenant_id=setup_tenant, account_id="smtp-get")

        assert isinstance(account, Account)
        assert account.id == "smtp-get"
        assert account.host == "smtp.test.local"
        assert account.port == 25

    async def test_list_accounts_empty(self, client: MailProxyClient, setup_tenant):
        """List accounts returns empty list when none exist."""
        accounts = await client.accounts.list(tenant_id=setup_tenant)

        assert accounts == []

    async def test_list_accounts(self, client: MailProxyClient, setup_tenant):
        """List all accounts for a tenant."""
        # Create multiple accounts
        await client.accounts.add(
            id="smtp-1",
            tenant_id=setup_tenant,
            host="a.com",
            port=25,
        )
        await client.accounts.add(
            id="smtp-2",
            tenant_id=setup_tenant,
            host="b.com",
            port=587,
        )

        accounts = await client.accounts.list(tenant_id=setup_tenant)

        assert len(accounts) == 2
        assert all(isinstance(a, Account) for a in accounts)
        ids = {a.id for a in accounts}
        assert ids == {"smtp-1", "smtp-2"}

    async def test_delete_account(self, client: MailProxyClient, setup_tenant):
        """Delete an account."""
        # Create account
        await client.accounts.add(
            id="smtp-delete",
            tenant_id=setup_tenant,
            host="delete.local",
            port=25,
        )

        # Delete
        success = await client.accounts.delete(tenant_id=setup_tenant, account_id="smtp-delete")
        assert success is True

        # Verify deleted
        accounts = await client.accounts.list(tenant_id=setup_tenant)
        assert all(a.id != "smtp-delete" for a in accounts)

    # =========================================================================
    # Update/Upsert Behavior
    # =========================================================================

    async def test_add_account_updates_existing(self, client: MailProxyClient, setup_tenant):
        """Adding account with same ID updates existing."""
        # Create account
        await client.accounts.add(
            id="smtp-upsert",
            tenant_id=setup_tenant,
            host="old.host.com",
            port=25,
        )

        # Update via add
        result = await client.accounts.add(
            id="smtp-upsert",
            tenant_id=setup_tenant,
            host="new.host.com",
            port=587,
        )

        assert result["host"] == "new.host.com"
        assert result["port"] == 587

        # Verify only one account exists
        accounts = await client.accounts.list(tenant_id=setup_tenant)
        assert len(accounts) == 1
        assert accounts[0].host == "new.host.com"

    # =========================================================================
    # Rate Limiting Configuration
    # =========================================================================

    async def test_account_rate_limits(self, client: MailProxyClient, setup_tenant):
        """Configure rate limiting for an account."""
        result = await client.accounts.add(
            id="smtp-limited",
            tenant_id=setup_tenant,
            host="smtp.limited.com",
            port=587,
            limit_per_minute=5,
            limit_per_hour=50,
            limit_per_day=500,
            limit_behavior="defer",
        )

        assert result["limit_per_minute"] == 5
        assert result["limit_per_hour"] == 50
        assert result["limit_per_day"] == 500
        assert result["limit_behavior"] == "defer"

    async def test_account_limit_behavior_reject(self, client: MailProxyClient, setup_tenant):
        """Configure account to reject when rate limited."""
        result = await client.accounts.add(
            id="smtp-reject",
            tenant_id=setup_tenant,
            host="smtp.reject.com",
            port=587,
            limit_per_minute=1,
            limit_behavior="reject",
        )

        assert result["limit_behavior"] == "reject"

    # =========================================================================
    # TLS Configuration
    # =========================================================================

    async def test_account_tls_enabled(self, client: MailProxyClient, setup_tenant):
        """Account with TLS enabled."""
        result = await client.accounts.add(
            id="smtp-tls",
            tenant_id=setup_tenant,
            host="smtp.secure.com",
            port=587,
            use_tls=True,
        )

        assert result["use_tls"] is True

    async def test_account_tls_disabled(self, client: MailProxyClient, setup_tenant):
        """Account with TLS disabled."""
        result = await client.accounts.add(
            id="smtp-notls",
            tenant_id=setup_tenant,
            host="smtp.insecure.local",
            port=25,
            use_tls=False,
        )

        assert result["use_tls"] is False

    # =========================================================================
    # Multi-Tenant Isolation
    # =========================================================================

    async def test_accounts_isolated_by_tenant(self, client: MailProxyClient):
        """Accounts are isolated by tenant."""
        # Create two tenants
        await client.tenants.add(id="tenant-a")
        await client.tenants.add(id="tenant-b")

        # Create account for each tenant
        await client.accounts.add(
            id="shared-name",
            tenant_id="tenant-a",
            host="a.com",
            port=25,
        )
        await client.accounts.add(
            id="shared-name",
            tenant_id="tenant-b",
            host="b.com",
            port=587,
        )

        # List for tenant-a
        accounts_a = await client.accounts.list(tenant_id="tenant-a")
        assert len(accounts_a) == 1
        assert accounts_a[0].host == "a.com"

        # List for tenant-b
        accounts_b = await client.accounts.list(tenant_id="tenant-b")
        assert len(accounts_b) == 1
        assert accounts_b[0].host == "b.com"
