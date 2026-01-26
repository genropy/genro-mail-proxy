"""Tests for multi-tenant functionality."""

import pytest

from mail_proxy.mailproxy_db import MailProxyDb


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
async def test_account_requires_tenant_id(tmp_path):
    """Test that accounts require tenant_id."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Accounts now require tenant_id
    with pytest.raises(KeyError):
        await db.add_account({
            "id": "standalone",
            "host": "smtp.example.com",
            "port": 587,
        })


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
async def test_events_for_tenant_account(tmp_path):
    """Test that events can be fetched and tenant context retrieved via account."""
    import time

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
    sent_ts = int(time.time())
    await db.mark_sent("msg1", sent_ts)

    # Fetch unreported events
    events = await db.fetch_unreported_events(limit=10)
    assert len(events) == 1
    assert events[0]["message_id"] == "msg1"

    # Get message and verify tenant association via account
    msg = await db.get_message("msg1")
    assert msg["account_id"] == "acme-main"

    # Verify tenant can be retrieved from account
    tenant = await db.get_tenant_for_account("acme-main")
    assert tenant["id"] == "acme"


@pytest.mark.asyncio
async def test_events_multiple_tenants(tmp_path):
    """Test events are created for messages from multiple tenants."""
    import time

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
    sent_ts = int(time.time())
    for msg_id in ["msg1", "msg2", "msg3"]:
        await db.mark_sent(msg_id, sent_ts)

    # Fetch unreported events
    events = await db.fetch_unreported_events(limit=10)
    assert len(events) == 3

    # Group events by message_id and verify tenant via account lookup
    msg_ids = {e["message_id"] for e in events}
    assert msg_ids == {"msg1", "msg2", "msg3"}

    # Verify tenant distribution via account lookup
    tenant1_msgs = []
    tenant2_msgs = []
    for e in events:
        msg = await db.get_message(e["message_id"])
        tenant = await db.get_tenant_for_account(msg["account_id"])
        if tenant and tenant["id"] == "tenant1":
            tenant1_msgs.append(e["message_id"])
        elif tenant and tenant["id"] == "tenant2":
            tenant2_msgs.append(e["message_id"])

    assert len(tenant1_msgs) == 2
    assert len(tenant2_msgs) == 1


# ----------------------------------------------------------------- API Key Tests


@pytest.mark.asyncio
async def test_create_api_key(tmp_path):
    """Test creating an API key for a tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})

    raw_key = await db.tenants.create_api_key("acme")

    assert raw_key is not None
    assert len(raw_key) > 20  # secrets.token_urlsafe(32) generates ~43 chars

    # Verify hash is saved in DB
    tenant = await db.get_tenant("acme")
    assert tenant["api_key_hash"] is not None
    assert tenant["api_key_expires_at"] is None


@pytest.mark.asyncio
async def test_create_api_key_with_expiration(tmp_path):
    """Test creating an API key with expiration."""
    import time

    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})

    expires_at = int(time.time()) + 3600  # 1 hour from now
    raw_key = await db.tenants.create_api_key("acme", expires_at=expires_at)

    assert raw_key is not None

    tenant = await db.get_tenant("acme")
    assert tenant["api_key_expires_at"] == expires_at


@pytest.mark.asyncio
async def test_create_api_key_nonexistent_tenant(tmp_path):
    """Test create_api_key returns None for nonexistent tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    raw_key = await db.tenants.create_api_key("nonexistent")

    assert raw_key is None


@pytest.mark.asyncio
async def test_get_tenant_by_token(tmp_path):
    """Test looking up tenant by API token."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})
    raw_key = await db.tenants.create_api_key("acme")

    tenant = await db.tenants.get_tenant_by_token(raw_key)

    assert tenant is not None
    assert tenant["id"] == "acme"
    assert tenant["name"] == "ACME Corp"


@pytest.mark.asyncio
async def test_get_tenant_by_token_invalid(tmp_path):
    """Test get_tenant_by_token returns None for invalid token."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})
    await db.tenants.create_api_key("acme")

    tenant = await db.tenants.get_tenant_by_token("invalid-token-12345")

    assert tenant is None


@pytest.mark.asyncio
async def test_get_tenant_by_token_expired(tmp_path):
    """Test get_tenant_by_token returns None for expired token."""
    import time

    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})

    # Create key with past expiration
    expires_at = int(time.time()) - 3600  # 1 hour ago
    raw_key = await db.tenants.create_api_key("acme", expires_at=expires_at)

    tenant = await db.tenants.get_tenant_by_token(raw_key)

    assert tenant is None


@pytest.mark.asyncio
async def test_revoke_api_key(tmp_path):
    """Test revoking an API key."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})
    raw_key = await db.tenants.create_api_key("acme")

    # Key works before revocation
    tenant = await db.tenants.get_tenant_by_token(raw_key)
    assert tenant is not None

    # Revoke
    result = await db.tenants.revoke_api_key("acme")
    assert result is True

    # Key no longer works
    tenant = await db.tenants.get_tenant_by_token(raw_key)
    assert tenant is None

    # DB fields are cleared
    tenant_data = await db.get_tenant("acme")
    assert tenant_data["api_key_hash"] is None
    assert tenant_data["api_key_expires_at"] is None


@pytest.mark.asyncio
async def test_revoke_api_key_nonexistent(tmp_path):
    """Test revoke_api_key returns False for nonexistent tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    result = await db.tenants.revoke_api_key("nonexistent")

    assert result is False


# --------------------------------------------------------------------------
# Batch suspension tests
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_suspend_all_tenant(tmp_path):
    """Test suspending all batches for a tenant (batch_code=None -> '*')."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})

    # Suspend all
    result = await db.tenants.suspend_batch("acme")
    assert result is True

    # Verify suspended_batches = "*"
    tenant = await db.get_tenant("acme")
    assert tenant["suspended_batches"] == "*"

    # Check helper method
    suspended = await db.tenants.get_suspended_batches("acme")
    assert suspended == {"*"}


@pytest.mark.asyncio
async def test_suspend_single_batch(tmp_path):
    """Test suspending a single batch."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})

    # Suspend single batch
    result = await db.tenants.suspend_batch("acme", "NL-2026-01")
    assert result is True

    tenant = await db.get_tenant("acme")
    assert tenant["suspended_batches"] == "NL-2026-01"

    suspended = await db.tenants.get_suspended_batches("acme")
    assert suspended == {"NL-2026-01"}


@pytest.mark.asyncio
async def test_suspend_multiple_batches(tmp_path):
    """Test suspending multiple batches accumulates them."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})

    # Suspend first batch
    await db.tenants.suspend_batch("acme", "NL-01")
    # Suspend second batch
    await db.tenants.suspend_batch("acme", "NL-02")
    # Suspend third batch
    await db.tenants.suspend_batch("acme", "promo-jan")

    suspended = await db.tenants.get_suspended_batches("acme")
    assert suspended == {"NL-01", "NL-02", "promo-jan"}


@pytest.mark.asyncio
async def test_activate_single_batch(tmp_path):
    """Test activating a single batch removes it from suspended list."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})

    # Suspend multiple batches
    await db.tenants.suspend_batch("acme", "NL-01")
    await db.tenants.suspend_batch("acme", "NL-02")

    # Activate one
    result = await db.tenants.activate_batch("acme", "NL-01")
    assert result is True

    # Only NL-02 remains suspended
    suspended = await db.tenants.get_suspended_batches("acme")
    assert suspended == {"NL-02"}


@pytest.mark.asyncio
async def test_activate_all(tmp_path):
    """Test activating all batches clears the suspended list."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})

    # Suspend multiple batches
    await db.tenants.suspend_batch("acme", "NL-01")
    await db.tenants.suspend_batch("acme", "NL-02")

    # Activate all
    result = await db.tenants.activate_batch("acme")
    assert result is True

    # No batches suspended
    suspended = await db.tenants.get_suspended_batches("acme")
    assert suspended == set()

    tenant = await db.get_tenant("acme")
    assert tenant["suspended_batches"] is None


@pytest.mark.asyncio
async def test_cannot_activate_single_from_full_suspension(tmp_path):
    """Test that activating a single batch fails when fully suspended."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({"id": "acme", "name": "ACME Corp"})

    # Suspend all
    await db.tenants.suspend_batch("acme")

    # Try to activate single batch - should fail
    result = await db.tenants.activate_batch("acme", "NL-01")
    assert result is False

    # Still fully suspended
    suspended = await db.tenants.get_suspended_batches("acme")
    assert suspended == {"*"}


@pytest.mark.asyncio
async def test_is_batch_suspended_helper(tmp_path):
    """Test the is_batch_suspended helper method."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    table = db.tenants

    # No suspension
    assert table.is_batch_suspended(None, "NL-01") is False
    assert table.is_batch_suspended(None, None) is False
    assert table.is_batch_suspended("", "NL-01") is False

    # Full suspension (*)
    assert table.is_batch_suspended("*", "NL-01") is True
    assert table.is_batch_suspended("*", None) is True

    # Specific batches suspended
    assert table.is_batch_suspended("NL-01,NL-02", "NL-01") is True
    assert table.is_batch_suspended("NL-01,NL-02", "NL-02") is True
    assert table.is_batch_suspended("NL-01,NL-02", "NL-03") is False
    assert table.is_batch_suspended("NL-01,NL-02", None) is False


@pytest.mark.asyncio
async def test_suspend_nonexistent_tenant(tmp_path):
    """Test suspend_batch returns False for nonexistent tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    result = await db.tenants.suspend_batch("nonexistent")
    assert result is False


@pytest.mark.asyncio
async def test_fetch_ready_excludes_suspended_batches(tmp_path):
    """Test that fetch_ready excludes messages from suspended batches."""
    import time

    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Setup tenant and account
    await db.add_tenant({"id": "acme", "name": "ACME Corp"})
    await db.add_account({
        "id": "acme-smtp",
        "tenant_id": "acme",
        "host": "smtp.acme.com",
        "port": 587,
    })

    now_ts = int(time.time())

    # Insert messages with different batch codes
    await db.insert_messages([
        {"id": "msg-1", "account_id": "acme-smtp", "priority": 2, "batch_code": "NL-01",
         "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "NL-01"}},
        {"id": "msg-2", "account_id": "acme-smtp", "priority": 2, "batch_code": "NL-02",
         "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "NL-02"}},
        {"id": "msg-3", "account_id": "acme-smtp", "priority": 2, "batch_code": None,
         "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "No batch"}},
    ])

    # All messages should be ready initially
    ready = await db.messages.fetch_ready(limit=10, now_ts=now_ts)
    assert len(ready) == 3

    # Suspend NL-01 batch
    await db.tenants.suspend_batch("acme", "NL-01")

    # Only 2 messages ready (NL-02 and no-batch)
    ready = await db.messages.fetch_ready(limit=10, now_ts=now_ts)
    assert len(ready) == 2
    assert {r["id"] for r in ready} == {"msg-2", "msg-3"}

    # Suspend all
    await db.tenants.suspend_batch("acme")

    # No messages ready
    ready = await db.messages.fetch_ready(limit=10, now_ts=now_ts)
    assert len(ready) == 0


@pytest.mark.asyncio
async def test_count_pending_for_tenant(tmp_path):
    """Test counting pending messages for a tenant with batch filter."""
    import time

    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Setup tenant and account
    await db.add_tenant({"id": "acme", "name": "ACME Corp"})
    await db.add_account({
        "id": "acme-smtp",
        "tenant_id": "acme",
        "host": "smtp.acme.com",
        "port": 587,
    })

    now_ts = int(time.time())

    # Insert messages
    await db.insert_messages([
        {"id": "msg-1", "account_id": "acme-smtp", "priority": 2, "batch_code": "NL-01",
         "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "NL-01 msg 1"}},
        {"id": "msg-2", "account_id": "acme-smtp", "priority": 2, "batch_code": "NL-01",
         "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "NL-01 msg 2"}},
        {"id": "msg-3", "account_id": "acme-smtp", "priority": 2, "batch_code": "NL-02",
         "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "NL-02"}},
        {"id": "msg-4", "account_id": "acme-smtp", "priority": 2, "batch_code": None,
         "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "No batch"}},
    ])

    # Count all pending for tenant
    count = await db.count_pending_messages("acme")
    assert count == 4

    # Count specific batch
    count_nl01 = await db.count_pending_messages("acme", "NL-01")
    assert count_nl01 == 2

    count_nl02 = await db.count_pending_messages("acme", "NL-02")
    assert count_nl02 == 1

    # Mark one as sent
    await db.mark_sent("msg-1", now_ts)

    # Count updated
    count_nl01 = await db.count_pending_messages("acme", "NL-01")
    assert count_nl01 == 1
