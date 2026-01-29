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
    await db.add_tenant({"id": "test-tenant", "name": "Test Tenant", "active": True})
    await db.add_account({
        "id": "test-account",
        "tenant_id": "test-tenant",
        "host": "smtp.test.com",
        "port": 587,
        "user": "test",
        "password": "test",
    })
    inserted = await db.insert_messages([{
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
        await db_with_message.add_event(
            message_pk=pk,
            event_type="sent",
            event_ts=1700000000,
        )

        events = await db_with_message.get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "sent"
        assert events[0]["event_ts"] == 1700000000
        assert events[0]["reported_ts"] is None

    @pytest.mark.asyncio
    async def test_add_event_error_with_description(self, db_with_message: MailProxyDb):
        """Test adding an 'error' event with description."""
        pk = db_with_message._test_pk
        await db_with_message.add_event(
            message_pk=pk,
            event_type="error",
            event_ts=1700000100,
            description="Connection refused",
        )

        events = await db_with_message.get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert events[0]["description"] == "Connection refused"

    @pytest.mark.asyncio
    async def test_add_event_bounce_with_metadata(self, db_with_message: MailProxyDb):
        """Test adding a 'bounce' event with metadata."""
        pk = db_with_message._test_pk
        await db_with_message.add_event(
            message_pk=pk,
            event_type="bounce",
            event_ts=1700000200,
            description="User unknown",
            metadata={"bounce_type": "hard", "bounce_code": "550"},
        )

        events = await db_with_message.get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "bounce"
        assert events[0]["metadata"]["bounce_type"] == "hard"
        assert events[0]["metadata"]["bounce_code"] == "550"

    @pytest.mark.asyncio
    async def test_add_event_deferred(self, db_with_message: MailProxyDb):
        """Test adding a 'deferred' event."""
        pk = db_with_message._test_pk
        await db_with_message.add_event(
            message_pk=pk,
            event_type="deferred",
            event_ts=1700000300,
            description="Rate limit exceeded",
        )

        events = await db_with_message.get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "deferred"
        assert events[0]["description"] == "Rate limit exceeded"

    @pytest.mark.asyncio
    async def test_fetch_unreported_events(self, db_with_message: MailProxyDb):
        """Test fetching unreported events with tenant info."""
        pk = db_with_message._test_pk
        await db_with_message.add_event(pk, "sent", 1700000000)
        await db_with_message.add_event(pk, "bounce", 1700000100, "User unknown")

        events = await db_with_message.fetch_unreported_events(limit=10)
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
        await db_with_message.add_event(pk, "sent", 1700000000)
        await db_with_message.add_event(pk, "bounce", 1700000100)

        # Get event IDs from the database
        events = await db_with_message.fetch_unreported_events(limit=10)
        assert len(events) == 2
        id1 = events[0]["event_id"]  # sent event (earlier timestamp)

        # Mark first event as reported
        await db_with_message.mark_events_reported([id1], 1700001000)

        # Only second event should be unreported
        events = await db_with_message.fetch_unreported_events(limit=10)
        assert len(events) == 1
        assert events[0]["event_type"] == "bounce"

    @pytest.mark.asyncio
    async def test_delete_events_for_message(self, db_with_message: MailProxyDb):
        """Test deleting all events for a message."""
        pk = db_with_message._test_pk
        await db_with_message.add_event(pk, "sent", 1700000000)
        await db_with_message.add_event(pk, "bounce", 1700000100)

        deleted = await db_with_message.delete_events_for_message(pk)
        assert deleted == 2

        events = await db_with_message.get_events_for_message(pk)
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_multiple_messages_isolation(self, db_with_message: MailProxyDb):
        """Test that events are isolated per message."""
        pk1 = db_with_message._test_pk
        # Add another message
        inserted = await db_with_message.insert_messages([{
            "id": "msg-002",
            "tenant_id": "test-tenant",
            "account_id": "test-account",
            "payload": {"from": "a@test.com", "to": ["c@test.com"], "subject": "Test 2"},
        }])
        pk2 = inserted[0]["pk"]

        await db_with_message.add_event(pk1, "sent", 1700000000)
        await db_with_message.add_event(pk2, "error", 1700000100, "Failed")

        events_1 = await db_with_message.get_events_for_message(pk1)
        events_2 = await db_with_message.get_events_for_message(pk2)

        assert len(events_1) == 1
        assert events_1[0]["event_type"] == "sent"
        assert len(events_2) == 1
        assert events_2[0]["event_type"] == "error"


class TestDbMethodsCreateEvents:
    """Test that db methods (mark_sent, mark_error, etc.) create events."""

    @pytest.mark.asyncio
    async def test_mark_sent_creates_event(self, db_with_message: MailProxyDb):
        """mark_sent should create a 'sent' event."""
        pk = db_with_message._test_pk
        await db_with_message.mark_sent(pk, 1700000000)

        events = await db_with_message.get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "sent"
        assert events[0]["event_ts"] == 1700000000

    @pytest.mark.asyncio
    async def test_mark_error_creates_event(self, db_with_message: MailProxyDb):
        """mark_error should create an 'error' event."""
        pk = db_with_message._test_pk
        await db_with_message.mark_error(pk, 1700000000, "SMTP error 550")

        events = await db_with_message.get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "error"
        assert events[0]["description"] == "SMTP error 550"

    @pytest.mark.asyncio
    async def test_set_deferred_creates_event(self, db_with_message: MailProxyDb):
        """set_deferred should create a 'deferred' event."""
        pk = db_with_message._test_pk
        await db_with_message.set_deferred(pk, 1700000060, "Rate limit")

        events = await db_with_message.get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "deferred"
        assert events[0]["description"] == "Rate limit"

    @pytest.mark.asyncio
    async def test_mark_bounced_creates_event(self, db_with_message: MailProxyDb):
        """mark_bounced should create a 'bounce' event."""
        pk = db_with_message._test_pk
        await db_with_message.mark_bounced(
            pk,
            bounce_type="hard",
            bounce_code="550",
            bounce_reason="User unknown",
            bounce_ts=1700000200,
        )

        events = await db_with_message.get_events_for_message(pk)
        assert len(events) == 1
        assert events[0]["event_type"] == "bounce"
        assert events[0]["description"] == "User unknown"
        assert events[0]["metadata"]["bounce_type"] == "hard"
        assert events[0]["metadata"]["bounce_code"] == "550"
