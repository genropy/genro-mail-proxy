# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Unit tests for MessageEventTable."""

from __future__ import annotations

import pytest
import pytest_asyncio

from core.mail_proxy.mailproxy_db import MailProxyDb


@pytest_asyncio.fixture
async def db(tmp_path):
    """Create a temporary database for testing."""
    db = MailProxyDb(str(tmp_path / "test.db"))
    await db.init_db()
    yield db
    await db.close()


@pytest_asyncio.fixture
async def db_with_message(db: MailProxyDb):
    """Database with a tenant, account, and message for event testing."""
    await db.table('tenants').add({"id": "test-tenant", "name": "Test Tenant", "active": True})
    await db.table('accounts').add({
        "id": "test-account",
        "tenant_id": "test-tenant",
        "host": "smtp.test.com",
        "port": 587,
        "user": "test",
        "password": "test",
    })
    inserted = await db.table('messages').insert_batch([{
        "id": "msg-001",
        "tenant_id": "test-tenant",
        "account_id": "test-account",
        "payload": {"from": "a@test.com", "to": ["b@test.com"], "subject": "Test"},
    }])
    # Store pk on db for tests that need it
    db._test_pk = inserted[0]["pk"]
    return db


class TestMessageEventTable:
    """Tests for MessageEventTable operations."""

    @pytest.mark.asyncio
    async def test_add_event_sent(self, db_with_message: MailProxyDb):
        """Test adding a 'sent' event."""
        pk = db_with_message._test_pk
        await db_with_message.table('message_events').add_event(
            message_pk=pk,
            event_type="sent",
            event_ts=1700000000,
        )

        events = await db_with_message.table('message_events').get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "sent"
        assert events[0]["event_ts"] == 1700000000
        assert events[0]["reported_ts"] is None

    @pytest.mark.asyncio
    async def test_add_event_error_with_description(self, db_with_message: MailProxyDb):
        """Test adding an 'error' event with description."""
        pk = db_with_message._test_pk
        await db_with_message.table('message_events').add_event(
            message_pk=pk,
            event_type="error",
            event_ts=1700000100,
            description="Connection refused",
        )

        events = await db_with_message.table('message_events').get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert events[0]["description"] == "Connection refused"

    @pytest.mark.asyncio
    async def test_add_event_bounce_with_metadata(self, db_with_message: MailProxyDb):
        """Test adding a 'bounce' event with metadata."""
        pk = db_with_message._test_pk
        await db_with_message.table('message_events').add_event(
            message_pk=pk,
            event_type="bounce",
            event_ts=1700000200,
            description="User unknown",
            metadata={"bounce_type": "hard", "bounce_code": "550"},
        )

        events = await db_with_message.table('message_events').get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "bounce"
        assert events[0]["metadata"]["bounce_type"] == "hard"
        assert events[0]["metadata"]["bounce_code"] == "550"

    @pytest.mark.asyncio
    async def test_add_event_deferred(self, db_with_message: MailProxyDb):
        """Test adding a 'deferred' event."""
        pk = db_with_message._test_pk
        await db_with_message.table('message_events').add_event(
            message_pk=pk,
            event_type="deferred",
            event_ts=1700000300,
            description="Rate limit exceeded",
        )

        events = await db_with_message.table('message_events').get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "deferred"
        assert events[0]["description"] == "Rate limit exceeded"

    @pytest.mark.asyncio
    async def test_fetch_unreported_events(self, db_with_message: MailProxyDb):
        """Test fetching unreported events with tenant info."""
        pk = db_with_message._test_pk
        await db_with_message.table('message_events').add_event(pk, "sent", 1700000000)
        await db_with_message.table('message_events').add_event(pk, "bounce", 1700000100, "User unknown")

        events = await db_with_message.table('message_events').fetch_unreported(limit=10)
        assert len(events) == 2
        # Should be ordered by event_ts
        assert events[0]["event_type"] == "sent"
        assert events[1]["event_type"] == "bounce"
        # Should include tenant info from join
        assert events[0]["tenant_id"] == "test-tenant"
        assert events[0]["account_id"] == "test-account"
        # Should include message_id (client-facing ID)
        assert events[0]["message_id"] == "msg-001"

    @pytest.mark.asyncio
    async def test_mark_events_reported(self, db_with_message: MailProxyDb):
        """Test marking events as reported."""
        pk = db_with_message._test_pk
        await db_with_message.table('message_events').add_event(pk, "sent", 1700000000)
        await db_with_message.table('message_events').add_event(pk, "bounce", 1700000100)

        # Get event IDs from the database
        events = await db_with_message.table('message_events').fetch_unreported(limit=10)
        assert len(events) == 2
        id1 = events[0]["event_id"]  # sent event (earlier timestamp)

        # Mark first event as reported
        await db_with_message.table('message_events').mark_reported([id1], 1700001000)

        # Only second event should be unreported
        events = await db_with_message.table('message_events').fetch_unreported(limit=10)
        assert len(events) == 1
        assert events[0]["event_type"] == "bounce"

    @pytest.mark.asyncio
    async def test_delete_events_for_message(self, db_with_message: MailProxyDb):
        """Test deleting all events for a message."""
        pk = db_with_message._test_pk
        await db_with_message.table('message_events').add_event(pk, "sent", 1700000000)
        await db_with_message.table('message_events').add_event(pk, "bounce", 1700000100)

        deleted = await db_with_message.table('message_events').delete_for_message(pk)
        assert deleted == 2

        events = await db_with_message.table('message_events').get_events_for_message(pk)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_multiple_messages_isolation(self, db_with_message: MailProxyDb):
        """Test that events are isolated per message."""
        pk1 = db_with_message._test_pk
        # Add another message
        inserted = await db_with_message.table('messages').insert_batch([{
            "id": "msg-002",
            "tenant_id": "test-tenant",
            "account_id": "test-account",
            "payload": {"from": "a@test.com", "to": ["c@test.com"], "subject": "Test 2"},
        }])
        pk2 = inserted[0]["pk"]

        await db_with_message.table('message_events').add_event(pk1, "sent", 1700000000)
        await db_with_message.table('message_events').add_event(pk2, "error", 1700000100, "Failed")

        events_1 = await db_with_message.table('message_events').get_events_for_message(pk1)
        events_2 = await db_with_message.table('message_events').get_events_for_message(pk2)

        assert len(events_1) == 1
        assert events_1[0]["event_type"] == "sent"
        assert len(events_2) == 1
        assert events_2[0]["event_type"] == "error"


class TestEventTriggersUpdateMessages:
    """Test that events trigger message state updates."""

    @pytest.mark.asyncio
    async def test_sent_event_triggers_mark_sent(self, db_with_message: MailProxyDb):
        """Adding a 'sent' event should update message.smtp_ts via trigger."""
        pk = db_with_message._test_pk
        # Add sent event - trigger should call mark_sent on messages
        await db_with_message.table('message_events').add_event(pk, "sent", 1700000000)

        events = await db_with_message.table('message_events').get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "sent"
        assert events[0]["event_ts"] == 1700000000

        # Verify message state was updated by trigger
        msg = await db_with_message.table('messages').get_by_pk(pk)
        assert msg["smtp_ts"] == 1700000000

    @pytest.mark.asyncio
    async def test_error_event_triggers_mark_error(self, db_with_message: MailProxyDb):
        """Adding an 'error' event should update message.smtp_ts via trigger."""
        pk = db_with_message._test_pk
        # Add error event - trigger should call mark_error on messages
        await db_with_message.table('message_events').add_event(
            pk, "error", 1700000000,
            description="SMTP error 550"
        )

        events = await db_with_message.table('message_events').get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert events[0]["description"] == "SMTP error 550"

        # Verify message state was updated by trigger
        msg = await db_with_message.table('messages').get_by_pk(pk)
        assert msg["smtp_ts"] == 1700000000

    @pytest.mark.asyncio
    async def test_deferred_event_updates_message(self, db_with_message: MailProxyDb):
        """Adding a 'deferred' event should update message.deferred_ts via trigger."""
        pk = db_with_message._test_pk
        # Add deferred event - trigger should update message.deferred_ts
        await db_with_message.table('message_events').add_event(
            pk, "deferred", 1700000060,
            description="Rate limit",
            metadata={"deferred_ts": 1700000120}  # Actual retry time
        )

        events = await db_with_message.table('message_events').get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "deferred"
        assert events[0]["description"] == "Rate limit"

        # Verify message state was updated by trigger
        msg = await db_with_message.table('messages').get_by_pk(pk)
        assert msg["deferred_ts"] == 1700000120

    @pytest.mark.asyncio
    async def test_bounce_event_recorded(self, db_with_message: MailProxyDb):
        """Bounce events should be recorded with metadata."""
        pk = db_with_message._test_pk
        await db_with_message.table('message_events').add_event(
            pk,
            event_type="bounce",
            event_ts=1700000200,
            description="User unknown",
            metadata={"bounce_type": "hard", "bounce_code": "550"},
        )

        events = await db_with_message.table('message_events').get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "bounce"
        assert events[0]["description"] == "User unknown"
        assert events[0]["metadata"]["bounce_type"] == "hard"
        assert events[0]["metadata"]["bounce_code"] == "550"
