# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Unit tests for PEC account management."""

from __future__ import annotations

import pytest

from src.mail_proxy.mailproxy_db import MailProxyDb


async def make_db_with_tenant(tmp_path, tenant_id="test_tenant"):
    """Create a test database with a default tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()
    await db.add_tenant({"id": tenant_id, "name": "Test Tenant"})
    return db


@pytest.mark.asyncio
async def test_add_pec_account(tmp_path):
    """Test creating a PEC account with IMAP configuration."""
    db = await make_db_with_tenant(tmp_path)

    await db.add_pec_account({
        "id": "pec-account",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "user": "user@pec.example.com",
        "password": "secret",
        "imap_host": "imap.pec.example.com",
        "imap_port": 993,
    })

    account = await db.get_account("test_tenant", "pec-account")
    assert account["id"] == "pec-account"
    assert account["host"] == "smtp.pec.example.com"
    assert account["is_pec_account"] == 1
    assert account["imap_host"] == "imap.pec.example.com"
    assert account["imap_port"] == 993
    # Should default to SMTP credentials
    assert account["imap_user"] == "user@pec.example.com"
    assert account["imap_password"] == "secret"
    assert account["imap_folder"] == "INBOX"


@pytest.mark.asyncio
async def test_add_pec_account_with_separate_imap_credentials(tmp_path):
    """Test PEC account with different IMAP credentials."""
    db = await make_db_with_tenant(tmp_path)

    await db.add_pec_account({
        "id": "pec-separate",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "user": "smtp-user@pec.example.com",
        "password": "smtp-secret",
        "imap_host": "imap.pec.example.com",
        "imap_user": "imap-user@pec.example.com",
        "imap_password": "imap-secret",
        "imap_folder": "PEC",
    })

    account = await db.get_account("test_tenant", "pec-separate")
    assert account["imap_user"] == "imap-user@pec.example.com"
    assert account["imap_password"] == "imap-secret"
    assert account["imap_folder"] == "PEC"


@pytest.mark.asyncio
async def test_list_pec_accounts(tmp_path):
    """Test listing only PEC accounts."""
    db = await make_db_with_tenant(tmp_path)

    # Add regular account
    await db.add_account({
        "id": "regular-account",
        "tenant_id": "test_tenant",
        "host": "smtp.example.com",
        "port": 587,
    })

    # Add PEC accounts
    await db.add_pec_account({
        "id": "pec-1",
        "tenant_id": "test_tenant",
        "host": "smtp.pec1.example.com",
        "port": 465,
        "imap_host": "imap.pec1.example.com",
    })
    await db.add_pec_account({
        "id": "pec-2",
        "tenant_id": "test_tenant",
        "host": "smtp.pec2.example.com",
        "port": 465,
        "imap_host": "imap.pec2.example.com",
    })

    # list_accounts returns all
    all_accounts = await db.list_accounts()
    assert len(all_accounts) == 3

    # list_pec_accounts returns only PEC
    pec_accounts = await db.list_pec_accounts()
    assert len(pec_accounts) == 2
    assert {acc["id"] for acc in pec_accounts} == {"pec-1", "pec-2"}


@pytest.mark.asyncio
async def test_update_imap_sync_state(tmp_path):
    """Test updating IMAP sync state after processing receipts."""
    db = await make_db_with_tenant(tmp_path)

    await db.add_pec_account({
        "id": "pec-sync",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "imap_host": "imap.pec.example.com",
    })

    # Initial state
    account = await db.get_account("test_tenant", "pec-sync")
    assert account["imap_last_uid"] is None
    assert account["imap_uidvalidity"] is None

    # Update sync state
    await db.update_imap_sync_state("test_tenant", "pec-sync", last_uid=100, uidvalidity=12345)

    account = await db.get_account("test_tenant", "pec-sync")
    assert account["imap_last_uid"] == 100
    assert account["imap_uidvalidity"] == 12345
    assert account["imap_last_sync"] is not None

    # Update only last_uid
    await db.update_imap_sync_state("test_tenant", "pec-sync", last_uid=150)

    account = await db.get_account("test_tenant", "pec-sync")
    assert account["imap_last_uid"] == 150
    assert account["imap_uidvalidity"] == 12345  # unchanged


@pytest.mark.asyncio
async def test_pec_account_with_tenant(tmp_path):
    """Test PEC account associated with a tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()

    await db.add_tenant({
        "id": "acme",
        "client_base_url": "https://api.acme.com/sync",
    })

    await db.add_pec_account({
        "id": "acme-pec",
        "tenant_id": "acme",
        "host": "smtp.pec.example.com",
        "port": 465,
        "imap_host": "imap.pec.example.com",
    })

    account = await db.get_account("acme", "acme-pec")
    assert account["tenant_id"] == "acme"
    assert account["is_pec_account"] == 1

    # Should appear in tenant's account list
    tenant_accounts = await db.list_accounts(tenant_id="acme")
    assert len(tenant_accounts) == 1
    assert tenant_accounts[0]["id"] == "acme-pec"


@pytest.mark.asyncio
async def test_get_pec_account_ids(tmp_path):
    """Test getting set of PEC account IDs."""
    db = await make_db_with_tenant(tmp_path)

    # Add regular and PEC accounts
    await db.add_account({
        "id": "regular",
        "tenant_id": "test_tenant",
        "host": "smtp.example.com",
        "port": 587,
    })
    await db.add_pec_account({
        "id": "pec-1",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "imap_host": "imap.pec.example.com",
    })
    await db.add_pec_account({
        "id": "pec-2",
        "tenant_id": "test_tenant",
        "host": "smtp.pec2.example.com",
        "port": 465,
        "imap_host": "imap.pec2.example.com",
    })

    pec_ids = await db.get_pec_account_ids()
    assert pec_ids == {"pec-1", "pec-2"}


@pytest.mark.asyncio
async def test_insert_messages_auto_sets_is_pec(tmp_path):
    """Test that messages for PEC accounts get is_pec=1 automatically."""
    db = await make_db_with_tenant(tmp_path)

    # Create PEC account
    await db.add_pec_account({
        "id": "pec-account",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "imap_host": "imap.pec.example.com",
    })

    # Create regular account
    await db.add_account({
        "id": "regular-account",
        "tenant_id": "test_tenant",
        "host": "smtp.example.com",
        "port": 587,
    })

    # Insert messages for both accounts
    msg_ids = await db.insert_messages([
        {
            "id": "msg-pec-001",
            "tenant_id": "test_tenant",
            "account_id": "pec-account",
            "payload": {"from": "a@pec.it", "to": "b@pec.it", "subject": "PEC"},
        },
        {
            "id": "msg-regular-001",
            "tenant_id": "test_tenant",
            "account_id": "regular-account",
            "payload": {"from": "a@mail.it", "to": "b@mail.it", "subject": "Normal"},
        },
    ])

    assert len(msg_ids) == 2

    # Fetch ready messages and check is_pec flag
    ready = await db.fetch_ready_messages(limit=10, now_ts=9999999999)
    pec_msg = next(m for m in ready if m["id"] == "msg-pec-001")
    regular_msg = next(m for m in ready if m["id"] == "msg-regular-001")

    assert pec_msg["is_pec"] == 1
    assert regular_msg["is_pec"] == 0


@pytest.mark.asyncio
async def test_clear_pec_flag(tmp_path):
    """Test clearing the is_pec flag on a message."""
    db = await make_db_with_tenant(tmp_path)

    # Create PEC account
    await db.add_pec_account({
        "id": "pec-account",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "imap_host": "imap.pec.example.com",
    })

    # Insert message for PEC account
    msg_ids = await db.insert_messages([
        {
            "id": "msg-pec-clear",
            "tenant_id": "test_tenant",
            "account_id": "pec-account",
            "payload": {"from": "a@pec.it", "to": "b@normal.it", "subject": "Test"},
        },
    ])

    # Verify is_pec is set
    ready = await db.fetch_ready_messages(limit=10, now_ts=9999999999)
    assert ready[0]["is_pec"] == 1

    # Clear the flag using pk from insert result
    await db.clear_pec_flag(msg_ids[0]["pk"])

    # Verify flag is cleared
    ready = await db.fetch_ready_messages(limit=10, now_ts=9999999999)
    assert ready[0]["is_pec"] == 0


@pytest.mark.asyncio
async def test_insert_messages_without_auto_pec(tmp_path):
    """Test inserting messages with auto_pec=False."""
    db = await make_db_with_tenant(tmp_path)

    # Create PEC account
    await db.add_pec_account({
        "id": "pec-account",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "imap_host": "imap.pec.example.com",
    })

    # Insert message with auto_pec=False
    await db.insert_messages(
        [{
            "id": "msg-no-auto-pec",
            "tenant_id": "test_tenant",
            "account_id": "pec-account",
            "payload": {"from": "a@pec.it", "to": "b@pec.it", "subject": "Test"},
        }],
        auto_pec=False,
    )

    # Verify is_pec is NOT set
    ready = await db.fetch_ready_messages(limit=10, now_ts=9999999999)
    assert ready[0]["is_pec"] == 0
