# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Unit tests for PEC timeout functionality."""

from __future__ import annotations

import time

import pytest

from core.mail_proxy.mailproxy_db import MailProxyDb


async def make_db_with_tenant(tmp_path, tenant_id="test_tenant"):
    """Create a test database with a default tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()
    await db.table('tenants').add({"id": tenant_id, "name": "Test Tenant"})
    return db


@pytest.mark.asyncio
async def test_get_pec_messages_without_acceptance(tmp_path):
    """Test finding PEC messages that timed out without acceptance."""
    db = await make_db_with_tenant(tmp_path)

    # Create PEC account
    await db.table('accounts').add_pec_account({
        "id": "pec-account",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "imap_host": "imap.pec.example.com",
    })

    # Insert PEC message
    inserted = await db.insert_messages([{
        "id": "msg-pec-timeout",
        "tenant_id": "test_tenant",
        "account_id": "pec-account",
        "payload": {"from": "a@pec.it", "to": "b@pec.it", "subject": "Test"},
    }])
    pk = inserted[0]["pk"]

    # Mark as sent 40 minutes ago
    old_ts = int(time.time()) - 40 * 60
    await db.mark_sent(pk, old_ts)

    # Check for timed out messages (cutoff 30 min ago)
    cutoff_ts = int(time.time()) - 30 * 60
    timed_out = await db.get_pec_messages_without_acceptance(cutoff_ts)

    assert len(timed_out) == 1
    assert timed_out[0]["id"] == "msg-pec-timeout"
    assert timed_out[0]["pk"] == pk


@pytest.mark.asyncio
async def test_pec_message_with_acceptance_not_timed_out(tmp_path):
    """PEC message with acceptance event should not be in timeout list."""
    db = await make_db_with_tenant(tmp_path)

    # Create PEC account
    await db.table('accounts').add_pec_account({
        "id": "pec-account",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "imap_host": "imap.pec.example.com",
    })

    # Insert PEC message
    inserted = await db.insert_messages([{
        "id": "msg-pec-accepted",
        "tenant_id": "test_tenant",
        "account_id": "pec-account",
        "payload": {"from": "a@pec.it", "to": "b@pec.it", "subject": "Test"},
    }])
    pk = inserted[0]["pk"]

    # Mark as sent 40 minutes ago
    old_ts = int(time.time()) - 40 * 60
    await db.mark_sent(pk, old_ts)

    # Add acceptance event (using pk)
    await db.add_event(
        message_pk=pk,
        event_type="pec_acceptance",
        event_ts=old_ts + 60,  # Accepted 1 min after sending
        description="PEC accettazione",
    )

    # Check for timed out messages
    cutoff_ts = int(time.time()) - 30 * 60
    timed_out = await db.get_pec_messages_without_acceptance(cutoff_ts)

    # Should not include the accepted message
    assert len(timed_out) == 0


@pytest.mark.asyncio
async def test_recent_pec_message_not_timed_out(tmp_path):
    """Recently sent PEC message should not be in timeout list."""
    db = await make_db_with_tenant(tmp_path)

    # Create PEC account
    await db.table('accounts').add_pec_account({
        "id": "pec-account",
        "tenant_id": "test_tenant",
        "host": "smtp.pec.example.com",
        "port": 465,
        "imap_host": "imap.pec.example.com",
    })

    # Insert PEC message
    inserted = await db.insert_messages([{
        "id": "msg-pec-recent",
        "tenant_id": "test_tenant",
        "account_id": "pec-account",
        "payload": {"from": "a@pec.it", "to": "b@pec.it", "subject": "Test"},
    }])
    pk = inserted[0]["pk"]

    # Mark as sent 10 minutes ago (within timeout window)
    recent_ts = int(time.time()) - 10 * 60
    await db.mark_sent(pk, recent_ts)

    # Check for timed out messages (cutoff 30 min ago)
    cutoff_ts = int(time.time()) - 30 * 60
    timed_out = await db.get_pec_messages_without_acceptance(cutoff_ts)

    # Should not include the recent message
    assert len(timed_out) == 0


@pytest.mark.asyncio
async def test_non_pec_message_not_in_timeout_list(tmp_path):
    """Non-PEC messages should not appear in timeout list."""
    db = await make_db_with_tenant(tmp_path)

    # Create regular account
    await db.table('accounts').add({
        "id": "regular-account",
        "tenant_id": "test_tenant",
        "host": "smtp.example.com",
        "port": 587,
    })

    # Insert regular message
    inserted = await db.insert_messages([{
        "id": "msg-regular",
        "tenant_id": "test_tenant",
        "account_id": "regular-account",
        "payload": {"from": "a@mail.it", "to": "b@mail.it", "subject": "Test"},
    }])
    pk = inserted[0]["pk"]

    # Mark as sent 40 minutes ago
    old_ts = int(time.time()) - 40 * 60
    await db.mark_sent(pk, old_ts)

    # Check for timed out messages
    cutoff_ts = int(time.time()) - 30 * 60
    timed_out = await db.get_pec_messages_without_acceptance(cutoff_ts)

    # Should not include regular message (is_pec=0)
    assert len(timed_out) == 0
