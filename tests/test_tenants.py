"""Tests for multi-tenant functionality."""

import pytest

from async_mail_service.mailproxy_db import MailProxyDb


@pytest.mark.asyncio
async def test_tenant_crud(tmp_path):
    """Test basic tenant CRUD operations."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create
    await db.add_tenant({
        "id": "acme",
        "name": "ACME Corp",
        "client_auth": {"method": "bearer", "token": "secret"},
        "client_base_url": "https://api.acme.com/sync",
        "rate_limits": {"hourly": 100, "daily": 1000},
        "active": True,
    })

    # Read
    tenant = await db.get_tenant("acme")
    assert tenant is not None
    assert tenant["id"] == "acme"
    assert tenant["name"] == "ACME Corp"
    assert tenant["client_base_url"] == "https://api.acme.com/sync"
    assert tenant["client_auth"]["method"] == "bearer"
    assert tenant["client_auth"]["token"] == "secret"
    assert tenant["rate_limits"]["hourly"] == 100
    assert tenant["active"] is True

    # Update
    success = await db.update_tenant("acme", {"name": "ACME Corporation", "active": False})
    assert success is True
    updated = await db.get_tenant("acme")
    assert updated["name"] == "ACME Corporation"
    assert updated["active"] is False

    # Delete
    deleted = await db.delete_tenant("acme")
    assert deleted is True
    assert await db.get_tenant("acme") is None


@pytest.mark.asyncio
async def test_tenant_list(tmp_path):
    """Test listing tenants with active_only filter."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "tenant1", "name": "Tenant 1", "active": True})
    await db.add_tenant({"id": "tenant2", "name": "Tenant 2", "active": False})
    await db.add_tenant({"id": "tenant3", "name": "Tenant 3", "active": True})

    # List all
    all_tenants = await db.list_tenants()
    assert len(all_tenants) == 3

    # List active only
    active_tenants = await db.list_tenants(active_only=True)
    assert len(active_tenants) == 2
    assert all(t["active"] for t in active_tenants)


@pytest.mark.asyncio
async def test_tenant_not_found(tmp_path):
    """Test handling of non-existent tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    tenant = await db.get_tenant("nonexistent")
    assert tenant is None

    success = await db.update_tenant("nonexistent", {"name": "Updated"})
    assert success is False

    deleted = await db.delete_tenant("nonexistent")
    assert deleted is False


@pytest.mark.asyncio
async def test_account_with_tenant(tmp_path):
    """Test creating accounts with tenant association."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create tenant first
    await db.add_tenant({"id": "acme", "name": "ACME"})

    # Create account for tenant
    await db.add_account({
        "id": "acme-main",
        "tenant_id": "acme",
        "host": "smtp.acme.com",
        "port": 587,
        "user": "mailer@acme.com",
        "password": "secret",
        "use_tls": True,
    })

    # Verify account has tenant_id
    accounts = await db.list_accounts(tenant_id="acme")
    assert len(accounts) == 1
    assert accounts[0]["id"] == "acme-main"
    assert accounts[0]["tenant_id"] == "acme"

    # List all accounts (no filter)
    all_accounts = await db.list_accounts()
    assert len(all_accounts) == 1


@pytest.mark.asyncio
async def test_get_tenant_for_account(tmp_path):
    """Test retrieving tenant configuration for an account."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({
        "id": "acme",
        "name": "ACME",
        "client_base_url": "https://api.acme.com/sync",
    })
    await db.add_account({
        "id": "acme-main",
        "tenant_id": "acme",
        "host": "smtp.acme.com",
        "port": 587,
    })

    tenant = await db.get_tenant_for_account("acme-main")
    assert tenant is not None
    assert tenant["id"] == "acme"
    assert tenant["client_base_url"] == "https://api.acme.com/sync"


@pytest.mark.asyncio
async def test_get_tenant_for_account_no_tenant(tmp_path):
    """Test account without tenant association."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_account({
        "id": "standalone",
        "host": "smtp.example.com",
        "port": 587,
    })

    tenant = await db.get_tenant_for_account("standalone")
    assert tenant is None


@pytest.mark.asyncio
async def test_delete_tenant_cascades(tmp_path):
    """Test that deleting a tenant removes associated accounts and messages."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create tenant with accounts and messages
    await db.add_tenant({"id": "acme", "name": "ACME"})
    await db.add_account({
        "id": "acme-main",
        "tenant_id": "acme",
        "host": "smtp.acme.com",
        "port": 587,
    })
    await db.insert_messages([{
        "id": "msg1",
        "account_id": "acme-main",
        "priority": 2,
        "payload": {"from": "test@acme.com", "to": ["dest@example.com"], "subject": "Test"},
    }])

    # Verify data exists
    assert len(await db.list_accounts(tenant_id="acme")) == 1
    assert len(await db.list_messages()) == 1

    # Delete tenant
    await db.delete_tenant("acme")

    # Verify cascade
    assert await db.get_tenant("acme") is None
    assert len(await db.list_accounts(tenant_id="acme")) == 0
    assert len(await db.list_messages()) == 0


@pytest.mark.asyncio
async def test_multiple_tenants_isolation(tmp_path):
    """Test that tenants are properly isolated."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create two tenants
    await db.add_tenant({"id": "tenant1", "name": "Tenant 1"})
    await db.add_tenant({"id": "tenant2", "name": "Tenant 2"})

    # Create accounts for each tenant
    await db.add_account({"id": "acc1", "tenant_id": "tenant1", "host": "smtp1.com", "port": 587})
    await db.add_account({"id": "acc2", "tenant_id": "tenant1", "host": "smtp1b.com", "port": 587})
    await db.add_account({"id": "acc3", "tenant_id": "tenant2", "host": "smtp2.com", "port": 587})

    # Verify isolation
    tenant1_accounts = await db.list_accounts(tenant_id="tenant1")
    tenant2_accounts = await db.list_accounts(tenant_id="tenant2")

    assert len(tenant1_accounts) == 2
    assert len(tenant2_accounts) == 1
    assert all(a["tenant_id"] == "tenant1" for a in tenant1_accounts)
    assert all(a["tenant_id"] == "tenant2" for a in tenant2_accounts)


@pytest.mark.asyncio
async def test_tenant_json_fields(tmp_path):
    """Test that JSON fields are properly serialized and deserialized."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({
        "id": "acme",
        "client_auth": {
            "method": "basic",
            "user": "admin",
            "password": "secret123",
        },
        "client_attachment_path": "https://api.acme.com/files",
        "rate_limits": {
            "hourly": 500,
            "daily": 5000,
        },
    })

    tenant = await db.get_tenant("acme")

    # Verify JSON fields are dicts, not strings
    assert isinstance(tenant["client_auth"], dict)
    assert tenant["client_auth"]["method"] == "basic"
    assert tenant["client_auth"]["user"] == "admin"

    assert tenant["client_attachment_path"] == "https://api.acme.com/files"

    assert isinstance(tenant["rate_limits"], dict)
    assert tenant["rate_limits"]["hourly"] == 500


@pytest.mark.asyncio
async def test_tenant_update_partial(tmp_path):
    """Test partial updates to tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({
        "id": "acme",
        "name": "ACME Corp",
        "client_base_url": "https://old.url.com",
        "active": True,
    })

    # Update only URL
    await db.update_tenant("acme", {"client_base_url": "https://new.url.com"})

    tenant = await db.get_tenant("acme")
    assert tenant["name"] == "ACME Corp"  # Unchanged
    assert tenant["client_base_url"] == "https://new.url.com"  # Changed
    assert tenant["active"] is True  # Unchanged


@pytest.mark.asyncio
async def test_tenant_update_json_fields(tmp_path):
    """Test updating JSON fields."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({
        "id": "acme",
        "rate_limits": {"hourly": 100, "daily": 1000},
    })

    await db.update_tenant("acme", {
        "rate_limits": {"hourly": 200, "daily": 2000},
    })

    tenant = await db.get_tenant("acme")
    assert tenant["rate_limits"]["hourly"] == 200
    assert tenant["rate_limits"]["daily"] == 2000


@pytest.mark.asyncio
async def test_fetch_reports_includes_tenant_id(tmp_path):
    """Test that fetch_reports includes tenant_id from account."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create tenant and account
    await db.add_tenant({
        "id": "acme",
        "client_base_url": "https://api.acme.com/sync",
    })
    await db.add_account({
        "id": "acme-main",
        "tenant_id": "acme",
        "host": "smtp.acme.com",
        "port": 587,
    })

    # Insert a message
    await db.insert_messages([{
        "id": "msg1",
        "account_id": "acme-main",
        "priority": 2,
        "payload": {"from": "test@acme.com", "to": ["dest@example.com"], "subject": "Test"},
    }])

    # Mark message as sent
    import time
    sent_ts = int(time.time())
    await db.mark_sent("msg1", sent_ts)

    # Fetch reports
    reports = await db.fetch_reports(limit=10)
    assert len(reports) == 1
    assert reports[0]["id"] == "msg1"
    assert reports[0]["account_id"] == "acme-main"
    assert reports[0]["tenant_id"] == "acme"


@pytest.mark.asyncio
async def test_fetch_reports_no_tenant(tmp_path):
    """Test fetch_reports for messages without tenant (backward compatibility)."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create account without tenant
    await db.add_account({
        "id": "standalone",
        "host": "smtp.example.com",
        "port": 587,
    })

    # Insert a message
    await db.insert_messages([{
        "id": "msg1",
        "account_id": "standalone",
        "priority": 2,
        "payload": {"from": "test@example.com", "to": ["dest@example.com"], "subject": "Test"},
    }])

    # Mark message as sent
    import time
    sent_ts = int(time.time())
    await db.mark_sent("msg1", sent_ts)

    # Fetch reports - tenant_id should be None
    reports = await db.fetch_reports(limit=10)
    assert len(reports) == 1
    assert reports[0]["tenant_id"] is None


@pytest.mark.asyncio
async def test_fetch_reports_multiple_tenants(tmp_path):
    """Test fetch_reports groups correctly by tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create two tenants with accounts
    await db.add_tenant({"id": "tenant1", "client_base_url": "https://api1.com/sync"})
    await db.add_tenant({"id": "tenant2", "client_base_url": "https://api2.com/sync"})
    await db.add_account({"id": "acc1", "tenant_id": "tenant1", "host": "smtp1.com", "port": 587})
    await db.add_account({"id": "acc2", "tenant_id": "tenant2", "host": "smtp2.com", "port": 587})

    # Insert messages for each tenant
    await db.insert_messages([
        {"id": "msg1", "account_id": "acc1", "priority": 2, "payload": {"from": "a@1.com", "to": ["b@1.com"], "subject": "T1"}},
        {"id": "msg2", "account_id": "acc2", "priority": 2, "payload": {"from": "a@2.com", "to": ["b@2.com"], "subject": "T2"}},
        {"id": "msg3", "account_id": "acc1", "priority": 2, "payload": {"from": "c@1.com", "to": ["d@1.com"], "subject": "T3"}},
    ])

    # Mark all as sent
    import time
    sent_ts = int(time.time())
    for msg_id in ["msg1", "msg2", "msg3"]:
        await db.mark_sent(msg_id, sent_ts)

    # Fetch reports
    reports = await db.fetch_reports(limit=10)
    assert len(reports) == 3

    # Group by tenant
    by_tenant = {}
    for r in reports:
        tid = r["tenant_id"]
        by_tenant.setdefault(tid, []).append(r)

    assert len(by_tenant["tenant1"]) == 2
    assert len(by_tenant["tenant2"]) == 1
