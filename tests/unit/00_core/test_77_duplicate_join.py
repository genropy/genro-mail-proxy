# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""Tests for issue #77: JOIN accounts without tenant_id causes row duplication.

When accounts exist with the same `id` but different `tenant_id`, JOINs between
messages and accounts that use only `m.account_id = a.id` (without filtering by
tenant_id) cause duplicate rows to be returned.

These tests verify that the fix (adding `AND m.tenant_id = a.tenant_id` to JOINs)
correctly prevents row duplication.
"""

import pytest

from core.mail_proxy.mailproxy_db import MailProxyDb


@pytest.mark.asyncio
async def test_fetch_ready_no_duplicate_with_same_account_id(tmp_path):
    """Test that fetch_ready doesn't return duplicates when same account_id exists on multiple tenants."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create two tenants
    await db.table('tenants').add({"id": "tenant_a", "name": "Tenant A"})
    await db.table('tenants').add({"id": "tenant_b", "name": "Tenant B"})

    # Create accounts with SAME id but different tenant_id
    shared_account_id = "shared-smtp-account"
    await db.table('accounts').add({
        "id": shared_account_id,
        "tenant_id": "tenant_a",
        "host": "smtp.a.com",
        "port": 587,
    })
    await db.table('accounts').add({
        "id": shared_account_id,
        "tenant_id": "tenant_b",
        "host": "smtp.b.com",
        "port": 587,
    })

    # Add a message for tenant_a
    await db.insert_messages([{
        "id": "msg-001",
        "tenant_id": "tenant_a",
        "account_id": shared_account_id,
        "payload": {"from": "a@a.com", "to": "x@example.com", "subject": "Test"},
    }])

    # Fetch ready messages
    ready = await db.fetch_ready_messages(limit=10, now_ts=9999999999)

    # Should return exactly 1 message, not 2 (the bug would return 2)
    assert len(ready) == 1
    assert ready[0]["id"] == "msg-001"
    assert ready[0]["tenant_id"] == "tenant_a"

    await db.close()


@pytest.mark.asyncio
async def test_fetch_ready_multiple_messages_no_cross_tenant_leak(tmp_path):
    """Test that messages from different tenants with same account_id are handled correctly."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create two tenants
    await db.table('tenants').add({"id": "tenant_a", "name": "Tenant A"})
    await db.table('tenants').add({"id": "tenant_b", "name": "Tenant B"})

    # Create accounts with SAME id
    shared_account_id = "shared-smtp"
    await db.table('accounts').add({
        "id": shared_account_id,
        "tenant_id": "tenant_a",
        "host": "smtp.a.com",
        "port": 587,
    })
    await db.table('accounts').add({
        "id": shared_account_id,
        "tenant_id": "tenant_b",
        "host": "smtp.b.com",
        "port": 587,
    })

    # Add messages for both tenants
    await db.insert_messages([
        {
            "id": "msg-a-001",
            "tenant_id": "tenant_a",
            "account_id": shared_account_id,
            "payload": {"from": "a@a.com", "to": "x@example.com", "subject": "From A"},
        },
        {
            "id": "msg-b-001",
            "tenant_id": "tenant_b",
            "account_id": shared_account_id,
            "payload": {"from": "b@b.com", "to": "y@example.com", "subject": "From B"},
        },
    ])

    # Fetch ready messages
    ready = await db.fetch_ready_messages(limit=10, now_ts=9999999999)

    # Should return exactly 2 messages (one from each tenant), not 4
    assert len(ready) == 2

    msg_ids = {m["id"] for m in ready}
    assert msg_ids == {"msg-a-001", "msg-b-001"}

    # Verify each message has correct tenant_id
    for msg in ready:
        if msg["id"] == "msg-a-001":
            assert msg["tenant_id"] == "tenant_a"
        else:
            assert msg["tenant_id"] == "tenant_b"

    await db.close()


@pytest.mark.asyncio
async def test_count_pending_for_tenant_no_inflation(tmp_path):
    """Test that count_pending_for_tenant returns correct count with duplicate account IDs."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create two tenants
    await db.table('tenants').add({"id": "tenant_a", "name": "Tenant A"})
    await db.table('tenants').add({"id": "tenant_b", "name": "Tenant B"})

    # Create accounts with SAME id
    shared_account_id = "shared-smtp"
    await db.table('accounts').add({
        "id": shared_account_id,
        "tenant_id": "tenant_a",
        "host": "smtp.a.com",
        "port": 587,
    })
    await db.table('accounts').add({
        "id": shared_account_id,
        "tenant_id": "tenant_b",
        "host": "smtp.b.com",
        "port": 587,
    })

    # Add 3 messages for tenant_a
    await db.insert_messages([
        {
            "id": f"msg-a-{i}",
            "tenant_id": "tenant_a",
            "account_id": shared_account_id,
            "payload": {"from": "a@a.com", "to": "x@example.com", "subject": f"Test {i}"},
        }
        for i in range(3)
    ])

    # Count pending for tenant_a
    count = await db.table('messages').count_pending_for_tenant("tenant_a")

    # Should be 3, not 6 (the bug would return 6 due to cross-join)
    assert count == 3

    await db.close()


@pytest.mark.asyncio
async def test_get_ids_for_tenant_no_duplication(tmp_path):
    """Test that get_ids_for_tenant returns correct set with duplicate account IDs."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    # Create two tenants
    await db.table('tenants').add({"id": "tenant_a", "name": "Tenant A"})
    await db.table('tenants').add({"id": "tenant_b", "name": "Tenant B"})

    # Create accounts with SAME id
    shared_account_id = "shared-smtp"
    await db.table('accounts').add({
        "id": shared_account_id,
        "tenant_id": "tenant_a",
        "host": "smtp.a.com",
        "port": 587,
    })
    await db.table('accounts').add({
        "id": shared_account_id,
        "tenant_id": "tenant_b",
        "host": "smtp.b.com",
        "port": 587,
    })

    # Add messages for tenant_a
    await db.insert_messages([
        {
            "id": "msg-a-001",
            "tenant_id": "tenant_a",
            "account_id": shared_account_id,
            "payload": {"from": "a@a.com", "to": "x@example.com", "subject": "Test"},
        },
    ])

    # Add messages for tenant_b with different id
    await db.insert_messages([
        {
            "id": "msg-b-001",
            "tenant_id": "tenant_b",
            "account_id": shared_account_id,
            "payload": {"from": "b@b.com", "to": "y@example.com", "subject": "Test"},
        },
    ])

    # Get IDs for tenant_a with a list that includes both IDs
    ids = await db.table('messages').get_ids_for_tenant(["msg-a-001", "msg-b-001"], "tenant_a")

    # Should only return msg-a-001, not both
    assert ids == {"msg-a-001"}

    await db.close()
