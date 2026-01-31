# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Enterprise Edition entity tests with PostgreSQL backend.

These tests verify that all EE table operations work correctly with PostgreSQL,
covering:
- TenantsTable_EE: Multi-tenant management and API key authentication
- AccountsTable_EE: PEC account support with IMAP configuration
- InstanceTable_EE: Bounce detection configuration
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime, timezone

import pytest

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def unix_to_datetime(ts: int) -> datetime:
    """Convert Unix timestamp to datetime for PostgreSQL TIMESTAMP columns."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


class TestTenantsTableEE:
    """Test TenantsTable_EE with PostgreSQL."""

    async def test_add_tenant_generates_api_key(self, pg_db):
        """Adding a new tenant generates an API key."""
        tenants = pg_db.table("tenants")

        raw_key = await tenants.add({"id": "t1", "name": "Test Tenant"})

        # Should return the raw API key
        assert raw_key
        assert len(raw_key) > 20  # token_urlsafe(32) generates ~43 chars

        # Verify tenant was created with hashed key
        tenant = await tenants.get("t1")
        assert tenant is not None
        assert tenant["name"] == "Test Tenant"
        assert tenant["api_key_hash"] == hashlib.sha256(raw_key.encode()).hexdigest()

    async def test_add_existing_tenant_no_new_key(self, pg_db):
        """Updating existing tenant doesn't change API key."""
        tenants = pg_db.table("tenants")

        # Create tenant
        original_key = await tenants.add({"id": "t1", "name": "Original"})
        original_hash = hashlib.sha256(original_key.encode()).hexdigest()

        # Update tenant
        result = await tenants.add({"id": "t1", "name": "Updated"})

        # Should return empty string (key unchanged)
        assert result == ""

        # Verify key is unchanged
        tenant = await tenants.get("t1")
        assert tenant["name"] == "Updated"
        assert tenant["api_key_hash"] == original_hash

    async def test_list_all_tenants(self, pg_db):
        """List all tenants."""
        tenants = pg_db.table("tenants")

        await tenants.add({"id": "t1", "name": "Tenant 1", "active": True})
        await tenants.add({"id": "t2", "name": "Tenant 2", "active": False})

        all_tenants = await tenants.list_all()
        assert len(all_tenants) == 2

        active_only = await tenants.list_all(active_only=True)
        assert len(active_only) == 1
        assert active_only[0]["id"] == "t1"

    async def test_update_fields(self, pg_db):
        """Update tenant fields."""
        tenants = pg_db.table("tenants")

        await tenants.add({"id": "t1", "name": "Original"})

        result = await tenants.update_fields("t1", {
            "name": "Updated Name",
            "client_base_url": "https://example.com",
        })

        assert result is True
        tenant = await tenants.get("t1")
        assert tenant["name"] == "Updated Name"
        assert tenant["client_base_url"] == "https://example.com"

    async def test_update_fields_nonexistent(self, pg_db):
        """Update fields for nonexistent tenant returns False."""
        tenants = pg_db.table("tenants")

        result = await tenants.update_fields("nonexistent", {"name": "Test"})
        assert result is False

    async def test_remove_tenant_cascades(self, pg_db):
        """Removing tenant cascades to accounts and messages."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")
        messages = pg_db.table("messages")

        # Setup
        await tenants.add({"id": "t1", "name": "Test"})
        await accounts.add({
            "id": "acc1",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })
        await messages.insert_batch([{
            "id": "msg1",
            "tenant_id": "t1",
            "account_id": "acc1",
            "payload": {"to": "a@b.com"},
        }])

        # Remove tenant
        result = await tenants.remove("t1")
        assert result is True

        # Verify cascade - tenant deleted
        assert await tenants.get("t1") is None

        # Verify cascade - accounts deleted (check via list_all)
        all_accounts = await accounts.list_all(tenant_id="t1")
        assert len(all_accounts) == 0

        # Verify cascade - messages deleted
        all_msgs = await messages.list_all()
        assert len([m for m in all_msgs if m["tenant_id"] == "t1"]) == 0

    async def test_create_api_key(self, pg_db):
        """Create new API key for tenant."""
        tenants = pg_db.table("tenants")

        original_key = await tenants.add({"id": "t1", "name": "Test"})

        # Create new key
        new_key = await tenants.create_api_key("t1")

        assert new_key is not None
        assert new_key != original_key

        # Verify new key works
        tenant = await tenants.get("t1")
        assert tenant["api_key_hash"] == hashlib.sha256(new_key.encode()).hexdigest()

    async def test_create_api_key_with_expiration(self, pg_db):
        """Create API key with expiration."""
        tenants = pg_db.table("tenants")

        await tenants.add({"id": "t1", "name": "Test"})
        expires_ts = int(time.time()) + 3600  # 1 hour
        expires_at = unix_to_datetime(expires_ts)

        new_key = await tenants.create_api_key("t1", expires_at=expires_at)

        assert new_key is not None
        tenant = await tenants.get("t1")
        # PostgreSQL returns datetime, verify it's set
        assert tenant["api_key_expires_at"] is not None

    async def test_create_api_key_nonexistent(self, pg_db):
        """Create API key for nonexistent tenant returns None."""
        tenants = pg_db.table("tenants")

        result = await tenants.create_api_key("nonexistent")
        assert result is None

    async def test_get_tenant_by_token(self, pg_db):
        """Look up tenant by API token."""
        tenants = pg_db.table("tenants")

        raw_key = await tenants.add({"id": "t1", "name": "Test"})

        tenant = await tenants.get_tenant_by_token(raw_key)

        assert tenant is not None
        assert tenant["id"] == "t1"
        assert tenant["name"] == "Test"

    async def test_get_tenant_by_token_invalid(self, pg_db):
        """Invalid token returns None."""
        tenants = pg_db.table("tenants")

        await tenants.add({"id": "t1", "name": "Test"})

        tenant = await tenants.get_tenant_by_token("invalid-token")
        assert tenant is None

    async def test_get_tenant_by_token_expired(self, pg_db):
        """Expired token returns None."""
        tenants = pg_db.table("tenants")

        await tenants.add({"id": "t1", "name": "Test"})
        expires_ts = int(time.time()) - 3600  # Expired 1 hour ago
        expires_at = unix_to_datetime(expires_ts)

        new_key = await tenants.create_api_key("t1", expires_at=expires_at)

        tenant = await tenants.get_tenant_by_token(new_key)
        assert tenant is None

    async def test_revoke_api_key(self, pg_db):
        """Revoke API key."""
        tenants = pg_db.table("tenants")

        raw_key = await tenants.add({"id": "t1", "name": "Test"})

        result = await tenants.revoke_api_key("t1")
        assert result is True

        # Key should no longer work
        tenant = await tenants.get_tenant_by_token(raw_key)
        assert tenant is None

        # Tenant should still exist
        tenant = await tenants.get("t1")
        assert tenant is not None
        assert tenant["api_key_hash"] is None


class TestAccountsTableEE:
    """Test AccountsTable_EE (PEC accounts) with PostgreSQL."""

    async def _setup_tenant(self, pg_db):
        """Create a tenant for account tests."""
        await pg_db.table("tenants").add({"id": "t1", "name": "Test"})

    async def test_add_pec_account(self, pg_db):
        """Add a PEC account with IMAP configuration."""
        await self._setup_tenant(pg_db)
        accounts = pg_db.table("accounts")

        pk = await accounts.add_pec_account({
            "id": "pec1",
            "tenant_id": "t1",
            "host": "smtp.pec.example.com",
            "port": 465,
            "user": "user@pec.example.com",
            "imap_host": "imap.pec.example.com",
            "imap_port": 993,
            "imap_user": "user@pec.example.com",
            "imap_folder": "INBOX",
        })

        assert pk is not None

        account = await accounts.get("t1", "pec1")
        assert account is not None
        assert account["is_pec_account"] == 1
        assert account["imap_host"] == "imap.pec.example.com"
        assert account["imap_port"] == 993

    async def test_list_pec_accounts(self, pg_db):
        """List only PEC accounts."""
        await self._setup_tenant(pg_db)
        accounts = pg_db.table("accounts")

        # Add regular account
        await accounts.add({
            "id": "regular",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })

        # Add PEC account
        await accounts.add_pec_account({
            "id": "pec1",
            "tenant_id": "t1",
            "host": "smtp.pec.example.com",
            "port": 465,
            "imap_host": "imap.pec.example.com",
        })

        pec_accounts = await accounts.list_pec_accounts()
        assert len(pec_accounts) == 1
        assert pec_accounts[0]["id"] == "pec1"

    async def test_get_pec_account_ids(self, pg_db):
        """Get set of PEC account IDs."""
        await self._setup_tenant(pg_db)
        accounts = pg_db.table("accounts")

        await accounts.add({
            "id": "regular",
            "tenant_id": "t1",
            "host": "smtp.example.com",
            "port": 587,
        })
        await accounts.add_pec_account({
            "id": "pec1",
            "tenant_id": "t1",
            "host": "smtp.pec1.com",
            "port": 465,
            "imap_host": "imap.pec1.com",
        })
        await accounts.add_pec_account({
            "id": "pec2",
            "tenant_id": "t1",
            "host": "smtp.pec2.com",
            "port": 465,
            "imap_host": "imap.pec2.com",
        })

        pec_ids = await accounts.get_pec_account_ids()
        assert pec_ids == {"pec1", "pec2"}

    async def test_update_imap_sync_state(self, pg_db):
        """Update IMAP sync state after processing."""
        await self._setup_tenant(pg_db)
        accounts = pg_db.table("accounts")

        await accounts.add_pec_account({
            "id": "pec1",
            "tenant_id": "t1",
            "host": "smtp.pec.example.com",
            "port": 465,
            "imap_host": "imap.pec.example.com",
        })

        # Update sync state
        await accounts.update_imap_sync_state(
            tenant_id="t1",
            account_id="pec1",
            last_uid=12345,
            uidvalidity=98765,
        )

        account = await accounts.get("t1", "pec1")
        assert account["imap_last_uid"] == 12345
        assert account["imap_uidvalidity"] == 98765
        assert account["imap_last_sync"] is not None

    async def test_update_imap_sync_state_without_uidvalidity(self, pg_db):
        """Update IMAP sync state without changing uidvalidity."""
        await self._setup_tenant(pg_db)
        accounts = pg_db.table("accounts")

        await accounts.add_pec_account({
            "id": "pec1",
            "tenant_id": "t1",
            "host": "smtp.pec.example.com",
            "port": 465,
            "imap_host": "imap.pec.example.com",
        })

        # Set initial uidvalidity
        await accounts.update_imap_sync_state(
            tenant_id="t1",
            account_id="pec1",
            last_uid=100,
            uidvalidity=12345,
        )

        # Update without uidvalidity
        await accounts.update_imap_sync_state(
            tenant_id="t1",
            account_id="pec1",
            last_uid=200,
        )

        account = await accounts.get("t1", "pec1")
        assert account["imap_last_uid"] == 200
        assert account["imap_uidvalidity"] == 12345  # Unchanged


class TestInstanceTableEE:
    """Test InstanceTable_EE (bounce detection) with PostgreSQL."""

    async def test_is_bounce_enabled_default(self, pg_db):
        """Bounce detection is disabled by default."""
        instance = pg_db.table("instance")

        enabled = await instance.is_bounce_enabled()
        assert enabled is False

    async def test_get_bounce_config_defaults(self, pg_db):
        """Get bounce config with defaults."""
        instance = pg_db.table("instance")

        config = await instance.get_bounce_config()

        assert config["enabled"] is False
        assert config["imap_port"] == 993
        assert config["imap_folder"] == "INBOX"
        assert config["imap_ssl"] is True
        assert config["poll_interval"] == 60

    async def test_set_bounce_config(self, pg_db):
        """Set bounce detection configuration."""
        instance = pg_db.table("instance")

        await instance.set_bounce_config(
            enabled=True,
            imap_host="imap.bounce.example.com",
            imap_port=993,
            imap_user="bounce@example.com",
            imap_password="secret123",
            imap_folder="Bounces",
            imap_ssl=True,
            poll_interval=120,
            return_path="bounce@example.com",
        )

        config = await instance.get_bounce_config()

        assert config["enabled"] is True
        assert config["imap_host"] == "imap.bounce.example.com"
        assert config["imap_port"] == 993
        assert config["imap_user"] == "bounce@example.com"
        assert config["imap_password"] == "secret123"
        assert config["imap_folder"] == "Bounces"
        assert config["imap_ssl"] is True
        assert config["poll_interval"] == 120
        assert config["return_path"] == "bounce@example.com"

    async def test_set_bounce_config_partial(self, pg_db):
        """Set only some bounce config fields."""
        instance = pg_db.table("instance")

        # Set initial config
        await instance.set_bounce_config(
            enabled=True,
            imap_host="imap.example.com",
            imap_user="user@example.com",
        )

        # Update only some fields
        await instance.set_bounce_config(
            poll_interval=300,
        )

        config = await instance.get_bounce_config()
        assert config["enabled"] is True  # Unchanged
        assert config["imap_host"] == "imap.example.com"  # Unchanged
        assert config["poll_interval"] == 300  # Updated

    async def test_is_bounce_enabled_after_enable(self, pg_db):
        """Check bounce enabled after enabling."""
        instance = pg_db.table("instance")

        await instance.set_bounce_config(enabled=True)

        enabled = await instance.is_bounce_enabled()
        assert enabled is True

    async def test_update_bounce_sync_state(self, pg_db):
        """Update bounce IMAP sync state."""
        instance = pg_db.table("instance")

        sync_ts = int(time.time())
        sync_dt = unix_to_datetime(sync_ts)
        await instance.update_bounce_sync_state(
            last_uid=5000,
            last_sync=sync_dt,
            uidvalidity=123456,
        )

        config = await instance.get_bounce_config()
        assert config["last_uid"] == 5000
        assert config["last_sync"] is not None  # PostgreSQL returns datetime
        assert config["uidvalidity"] == 123456

    async def test_update_bounce_sync_state_without_uidvalidity(self, pg_db):
        """Update bounce sync state without uidvalidity."""
        instance = pg_db.table("instance")

        # Set initial
        await instance.update_bounce_sync_state(
            last_uid=100,
            last_sync=unix_to_datetime(int(time.time())),
            uidvalidity=12345,
        )

        # Update without uidvalidity
        new_ts = int(time.time()) + 60
        await instance.update_bounce_sync_state(
            last_uid=200,
            last_sync=unix_to_datetime(new_ts),
        )

        config = await instance.get_bounce_config()
        assert config["last_uid"] == 200
        assert config["last_sync"] is not None
        assert config["uidvalidity"] == 12345  # Unchanged


class TestEECrossEntity:
    """Test EE features across multiple entities with PostgreSQL."""

    async def test_pec_account_with_api_key_tenant(self, pg_db):
        """PEC account works with API key authenticated tenant."""
        tenants = pg_db.table("tenants")
        accounts = pg_db.table("accounts")

        # Create tenant with API key
        api_key = await tenants.add({"id": "pec_tenant", "name": "PEC Tenant"})

        # Lookup tenant by key
        tenant = await tenants.get_tenant_by_token(api_key)
        assert tenant is not None

        # Add PEC account to this tenant
        await accounts.add_pec_account({
            "id": "pec_account",
            "tenant_id": tenant["id"],
            "host": "smtp.pec.it",
            "port": 465,
            "imap_host": "imap.pec.it",
        })

        # Verify
        pec_accounts = await accounts.list_pec_accounts()
        assert len(pec_accounts) == 1
        assert pec_accounts[0]["tenant_id"] == "pec_tenant"

    async def test_bounce_detection_with_multitenant(self, pg_db):
        """Bounce detection config is instance-wide, not per-tenant."""
        tenants = pg_db.table("tenants")
        instance = pg_db.table("instance")

        # Create multiple tenants
        await tenants.add({"id": "t1", "name": "Tenant 1"})
        await tenants.add({"id": "t2", "name": "Tenant 2"})

        # Configure bounce detection (instance-level)
        await instance.set_bounce_config(
            enabled=True,
            imap_host="bounce.example.com",
            return_path="bounces@example.com",
        )

        # Both tenants share the same bounce config
        config = await instance.get_bounce_config()
        assert config["enabled"] is True

        # Tenants exist independently
        all_tenants = await tenants.list_all()
        assert len(all_tenants) == 2
