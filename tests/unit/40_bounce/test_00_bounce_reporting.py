# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for bounce detection integration with event-based reporting."""

import pytest

from mail_proxy.mailproxy_db import MailProxyDb


async def make_db_with_tenant(tmp_path, tenant_id="test_tenant"):
    """Create a test database with a default tenant."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()
    await db.add_tenant({"id": tenant_id, "name": "Test Tenant"})
    return db


@pytest.mark.asyncio
async def test_mark_bounced_creates_event(tmp_path):
    """Test that mark_bounced creates a bounce event in message_events table."""
    db = await make_db_with_tenant(tmp_path)

    # Create account
    await db.add_account({
        "id": "acc1",
        "tenant_id": "test_tenant",
        "host": "smtp.example.com",
        "port": 587,
    })

    # Insert message
    inserted = await db.insert_messages([{
        "id": "msg1",
        "tenant_id": "test_tenant",
        "account_id": "acc1",
        "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "Test", "body": "Hi"},
    }])
    pk = inserted[0]["pk"]

    # Mark as sent first
    sent_ts = 1700000000
    await db.mark_sent(pk, "msg1", sent_ts)

    # Mark as bounced
    bounce_ts = 1700000100
    await db.mark_bounced(
        "msg1",
        bounce_type="hard",
        bounce_code="550",
        bounce_reason="User unknown",
        bounce_ts=bounce_ts,
    )

    # Verify bounce event was created
    events = await db.get_events_for_message("msg1")
    bounce_events = [e for e in events if e["event_type"] == "bounce"]
    assert len(bounce_events) == 1

    bounce_event = bounce_events[0]
    assert bounce_event["event_ts"] == bounce_ts
    assert bounce_event["description"] == "User unknown"
    assert bounce_event["metadata"]["bounce_type"] == "hard"
    assert bounce_event["metadata"]["bounce_code"] == "550"


@pytest.mark.asyncio
async def test_bounce_event_in_unreported_events(tmp_path):
    """Test that bounce events are returned by fetch_unreported_events."""
    db = await make_db_with_tenant(tmp_path)

    # Create account and message
    await db.add_account({"id": "acc1", "tenant_id": "test_tenant", "host": "smtp.example.com", "port": 587})
    inserted = await db.insert_messages([{
        "id": "msg1",
        "tenant_id": "test_tenant",
        "account_id": "acc1",
        "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "Test", "body": "Hi"},
    }])
    pk = inserted[0]["pk"]

    # Mark as sent
    sent_ts = 1700000000
    await db.mark_sent(pk, "msg1", sent_ts)

    # Mark sent event as reported
    events = await db.fetch_unreported_events(limit=10)
    sent_event_ids = [e["event_id"] for e in events if e["event_type"] == "sent"]
    await db.mark_events_reported(sent_event_ids, sent_ts + 10)

    # Mark as bounced
    await db.mark_bounced(
        "msg1",
        bounce_type="hard",
        bounce_code="550",
        bounce_reason="User unknown",
        bounce_ts=1700000200,
    )

    # Bounce event should be in unreported events
    unreported = await db.fetch_unreported_events(limit=10)
    assert len(unreported) == 1
    assert unreported[0]["event_type"] == "bounce"
    assert unreported[0]["message_id"] == "msg1"


@pytest.mark.asyncio
async def test_mark_bounce_reported(tmp_path):
    """Test that bounce events can be marked as reported."""
    db = await make_db_with_tenant(tmp_path)

    # Setup
    await db.add_account({"id": "acc1", "tenant_id": "test_tenant", "host": "smtp.example.com", "port": 587})
    inserted = await db.insert_messages([{
        "id": "msg1",
        "tenant_id": "test_tenant",
        "account_id": "acc1",
        "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "Test", "body": "Hi"},
    }])
    pk = inserted[0]["pk"]

    # Mark as sent and reported
    sent_ts = 1700000000
    await db.mark_sent(pk, "msg1", sent_ts)

    events = await db.fetch_unreported_events(limit=10)
    sent_event_ids = [e["event_id"] for e in events if e["event_type"] == "sent"]
    await db.mark_events_reported(sent_event_ids, sent_ts + 10)

    # Add bounce
    await db.mark_bounced(
        "msg1",
        bounce_type="soft",
        bounce_code="421",
        bounce_reason="Try again later",
        bounce_ts=1700000200,
    )

    # Should have one unreported event (the bounce)
    unreported = await db.fetch_unreported_events(limit=10)
    assert len(unreported) == 1
    bounce_event_id = unreported[0]["event_id"]

    # Mark bounce as reported
    reported_ts = 1700000300
    await db.mark_events_reported([bounce_event_id], reported_ts)

    # No more unreported events
    unreported = await db.fetch_unreported_events(limit=10)
    assert len(unreported) == 0


@pytest.mark.asyncio
async def test_multiple_events_for_same_message(tmp_path):
    """Test that a message can have multiple events (sent + bounce)."""
    db = await make_db_with_tenant(tmp_path)

    # Setup
    await db.add_account({"id": "acc1", "tenant_id": "test_tenant", "host": "smtp.example.com", "port": 587})
    inserted = await db.insert_messages([{
        "id": "msg1",
        "tenant_id": "test_tenant",
        "account_id": "acc1",
        "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "Test", "body": "Hi"},
    }])
    pk = inserted[0]["pk"]

    # Mark as sent
    sent_ts = 1700000000
    await db.mark_sent(pk, "msg1", sent_ts)

    # Mark as bounced
    await db.mark_bounced(
        "msg1",
        bounce_type="hard",
        bounce_code="550",
        bounce_reason="User unknown",
        bounce_ts=1700000200,
    )

    # Should have 2 events for the message
    events = await db.get_events_for_message("msg1")
    assert len(events) == 2

    event_types = {e["event_type"] for e in events}
    assert event_types == {"sent", "bounce"}


@pytest.mark.asyncio
async def test_fetch_unreported_includes_both_new_and_bounce(tmp_path):
    """Test fetch_unreported_events returns both new messages and bounce updates."""
    db = await make_db_with_tenant(tmp_path)

    # Setup
    await db.add_account({"id": "acc1", "tenant_id": "test_tenant", "host": "smtp.example.com", "port": 587})
    inserted = await db.insert_messages([
        {
            "id": "msg-new",
            "tenant_id": "test_tenant",
            "account_id": "acc1",
            "payload": {"from": "a@b.com", "to": ["c@d.com"], "subject": "New", "body": "Hi"},
        },
        {
            "id": "msg-bounced",
            "tenant_id": "test_tenant",
            "account_id": "acc1",
            "payload": {"from": "a@b.com", "to": ["e@f.com"], "subject": "Bounced", "body": "Hi"},
        },
    ])
    pk_map = {m["id"]: m["pk"] for m in inserted}

    sent_ts = 1700000000

    # msg-new: just sent, not reported
    await db.mark_sent(pk_map["msg-new"], "msg-new", sent_ts)

    # msg-bounced: sent, reported, then bounced
    await db.mark_sent(pk_map["msg-bounced"], "msg-bounced", sent_ts)

    # Report only the sent event for msg-bounced
    events = await db.fetch_unreported_events(limit=10)
    bounced_sent_event = [e for e in events if e["message_id"] == "msg-bounced"][0]
    await db.mark_events_reported([bounced_sent_event["event_id"]], sent_ts + 10)

    # Add bounce to msg-bounced
    await db.mark_bounced(
        "msg-bounced",
        bounce_type="hard",
        bounce_code="550",
        bounce_reason="User unknown",
        bounce_ts=1700000200,
    )

    # Both should be in unreported events
    unreported = await db.fetch_unreported_events(limit=10)
    assert len(unreported) == 2

    message_ids = {e["message_id"] for e in unreported}
    assert message_ids == {"msg-new", "msg-bounced"}

    # Verify event types
    event_by_msg = {e["message_id"]: e for e in unreported}
    assert event_by_msg["msg-new"]["event_type"] == "sent"
    assert event_by_msg["msg-bounced"]["event_type"] == "bounce"
