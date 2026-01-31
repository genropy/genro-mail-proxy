# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for MessageEventTable - CE table methods."""

import time

import pytest

from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with schema and tenant/account/message for FK constraints."""
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
    await proxy.db.connect()
    await proxy.db.check_structure()
    # Create tenant and account for FK constraints
    await proxy.db.table("tenants").insert({"id": "t1", "name": "Test Tenant", "active": 1})
    await proxy.db.table("accounts").add({
        "id": "a1",
        "tenant_id": "t1",
        "host": "smtp.example.com",
        "port": 587,
    })
    yield proxy.db
    await proxy.close()


async def create_message(db, msg_id="msg1", tenant_id="t1", account_id="a1"):
    """Helper to create a message and return its pk."""
    from genro_toolbox import get_uuid
    pk = get_uuid()
    await db.table("messages").insert({
        "pk": pk,
        "id": msg_id,
        "tenant_id": tenant_id,
        "account_id": account_id,
        "payload": '{"to": "test@example.com"}',
    })
    return pk


class TestMessageEventTableAddEvent:
    """Tests for MessageEventTable.add_event() method."""

    async def test_add_event_basic(self, db):
        """add_event() inserts event record."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        result = await events.add_event(pk, "sent", ts)
        assert result == 1  # 1 row inserted

    async def test_add_event_with_description(self, db):
        """add_event() stores description."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "error", ts, description="Connection refused")
        event_list = await events.get_events_for_message(pk)
        assert len(event_list) == 1
        assert event_list[0]["description"] == "Connection refused"

    async def test_add_event_with_metadata(self, db):
        """add_event() serializes metadata as JSON."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "bounce", ts, metadata={"bounce_type": "hard", "code": 550})
        event_list = await events.get_events_for_message(pk)
        assert event_list[0]["metadata"] == {"bounce_type": "hard", "code": 550}

    async def test_add_event_multiple(self, db):
        """add_event() creates multiple events for same message."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "deferred", ts, description="Rate limited")
        await events.add_event(pk, "sent", ts + 60)
        event_list = await events.get_events_for_message(pk)
        assert len(event_list) == 2


class TestMessageEventTableGetEventsForMessage:
    """Tests for MessageEventTable.get_events_for_message() method."""

    async def test_get_events_empty(self, db):
        """get_events_for_message() returns empty list for no events."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")

        result = await events.get_events_for_message(pk)
        assert result == []

    async def test_get_events_ordered_by_ts(self, db):
        """get_events_for_message() returns events ordered chronologically."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts + 100)
        await events.add_event(pk, "deferred", ts)
        await events.add_event(pk, "error", ts + 50)

        result = await events.get_events_for_message(pk)
        types = [e["event_type"] for e in result]
        assert types == ["deferred", "error", "sent"]

    async def test_get_events_decodes_metadata(self, db):
        """get_events_for_message() decodes metadata JSON."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "bounce", ts, metadata={"type": "soft"})
        result = await events.get_events_for_message(pk)
        assert result[0]["metadata"] == {"type": "soft"}

    async def test_get_events_invalid_metadata_returns_none(self, db):
        """get_events_for_message() returns None for invalid JSON metadata."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        # Insert with invalid JSON directly
        await db.table("message_events").insert({
            "message_pk": pk,
            "event_type": "error",
            "event_ts": ts,
            "metadata": "not-valid-json{",
        })

        result = await events.get_events_for_message(pk)
        assert result[0]["metadata"] is None


class TestMessageEventTableFetchUnreported:
    """Tests for MessageEventTable.fetch_unreported() method."""

    async def test_fetch_unreported_empty(self, db):
        """fetch_unreported() returns empty list when no events."""
        events = db.table("message_events")
        result = await events.fetch_unreported(limit=10)
        assert result == []

    async def test_fetch_unreported_returns_unreported(self, db):
        """fetch_unreported() returns events with reported_ts=NULL."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts)
        result = await events.fetch_unreported(limit=10)
        assert len(result) == 1
        assert result[0]["event_type"] == "sent"

    async def test_fetch_unreported_excludes_reported(self, db):
        """fetch_unreported() excludes events with reported_ts set."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts)
        await events.add_event(pk, "error", ts + 10)

        # Mark first as reported
        unreported = await events.fetch_unreported(limit=10)
        await events.mark_reported([unreported[0]["event_id"]], ts + 20)

        result = await events.fetch_unreported(limit=10)
        assert len(result) == 1
        assert result[0]["event_type"] == "error"

    async def test_fetch_unreported_respects_limit(self, db):
        """fetch_unreported() respects limit parameter."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        for i in range(5):
            await events.add_event(pk, "deferred", ts + i)

        result = await events.fetch_unreported(limit=2)
        assert len(result) == 2

    async def test_fetch_unreported_includes_message_id(self, db):
        """fetch_unreported() includes message_id from messages table."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts)
        result = await events.fetch_unreported(limit=10)
        assert result[0]["message_id"] == "msg1"

    async def test_fetch_unreported_ordered_by_ts(self, db):
        """fetch_unreported() returns events ordered by event_ts."""
        events = db.table("message_events")
        pk1 = await create_message(db, "msg1")
        pk2 = await create_message(db, "msg2")
        ts = int(time.time())

        await events.add_event(pk2, "sent", ts + 100)
        await events.add_event(pk1, "sent", ts)

        result = await events.fetch_unreported(limit=10)
        assert result[0]["message_id"] == "msg1"
        assert result[1]["message_id"] == "msg2"


class TestMessageEventTableMarkReported:
    """Tests for MessageEventTable.mark_reported() method."""

    async def test_mark_reported_single(self, db):
        """mark_reported() sets reported_ts for single event."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts)
        unreported = await events.fetch_unreported(limit=10)
        event_id = unreported[0]["event_id"]

        await events.mark_reported([event_id], ts + 10)

        result = await events.fetch_unreported(limit=10)
        assert len(result) == 0

    async def test_mark_reported_multiple(self, db):
        """mark_reported() sets reported_ts for multiple events."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts)
        await events.add_event(pk, "bounce", ts + 10)

        unreported = await events.fetch_unreported(limit=10)
        event_ids = [e["event_id"] for e in unreported]

        await events.mark_reported(event_ids, ts + 20)

        result = await events.fetch_unreported(limit=10)
        assert len(result) == 0

    async def test_mark_reported_empty_list_no_error(self, db):
        """mark_reported() with empty list doesn't raise."""
        events = db.table("message_events")
        await events.mark_reported([], int(time.time()))  # Should not raise


class TestMessageEventTableDeleteForMessage:
    """Tests for MessageEventTable.delete_for_message() method."""

    async def test_delete_for_message_existing(self, db):
        """delete_for_message() removes all events for message."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts)
        await events.add_event(pk, "bounce", ts + 10)

        deleted = await events.delete_for_message(pk)
        assert deleted == 2

        result = await events.get_events_for_message(pk)
        assert result == []

    async def test_delete_for_message_nonexistent(self, db):
        """delete_for_message() returns 0 for no events."""
        events = db.table("message_events")
        deleted = await events.delete_for_message("nonexistent-pk")
        assert deleted == 0

    async def test_delete_for_message_preserves_others(self, db):
        """delete_for_message() doesn't affect other messages."""
        events = db.table("message_events")
        pk1 = await create_message(db, "msg1")
        pk2 = await create_message(db, "msg2")
        ts = int(time.time())

        await events.add_event(pk1, "sent", ts)
        await events.add_event(pk2, "sent", ts)

        await events.delete_for_message(pk1)

        result1 = await events.get_events_for_message(pk1)
        result2 = await events.get_events_for_message(pk2)
        assert len(result1) == 0
        assert len(result2) == 1


class TestMessageEventTableCountUnreportedForMessage:
    """Tests for MessageEventTable.count_unreported_for_message() method."""

    async def test_count_unreported_zero(self, db):
        """count_unreported_for_message() returns 0 when no events."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")

        count = await events.count_unreported_for_message(pk)
        assert count == 0

    async def test_count_unreported_all(self, db):
        """count_unreported_for_message() counts unreported events."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts)
        await events.add_event(pk, "bounce", ts + 10)

        count = await events.count_unreported_for_message(pk)
        assert count == 2

    async def test_count_unreported_partial(self, db):
        """count_unreported_for_message() excludes reported events."""
        events = db.table("message_events")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts)
        await events.add_event(pk, "bounce", ts + 10)

        # Mark first as reported
        unreported = await events.fetch_unreported(limit=1)
        await events.mark_reported([unreported[0]["event_id"]], ts + 20)

        count = await events.count_unreported_for_message(pk)
        assert count == 1


class TestMessageEventTableTrigger:
    """Tests for MessageEventTable.trigger_on_inserted() - message status updates."""

    async def test_trigger_sent_updates_message(self, db):
        """Adding 'sent' event updates message smtp_ts."""
        events = db.table("message_events")
        messages = db.table("messages")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "sent", ts)

        msg = await messages.get_by_pk(pk)
        assert msg["smtp_ts"] == ts

    async def test_trigger_error_updates_message(self, db):
        """Adding 'error' event updates message smtp_ts."""
        events = db.table("message_events")
        messages = db.table("messages")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "error", ts)

        msg = await messages.get_by_pk(pk)
        assert msg["smtp_ts"] == ts

    async def test_trigger_deferred_updates_message(self, db):
        """Adding 'deferred' event updates message deferred_ts."""
        events = db.table("message_events")
        messages = db.table("messages")
        pk = await create_message(db, "msg1")
        ts = int(time.time())
        retry_ts = ts + 300

        await events.add_event(pk, "deferred", ts, metadata={"deferred_ts": retry_ts})

        msg = await messages.get_by_pk(pk)
        assert msg["deferred_ts"] == retry_ts

    async def test_trigger_deferred_no_metadata_uses_event_ts(self, db):
        """Adding 'deferred' event without metadata uses event_ts."""
        events = db.table("message_events")
        messages = db.table("messages")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "deferred", ts)

        msg = await messages.get_by_pk(pk)
        assert msg["deferred_ts"] == ts

    async def test_trigger_other_events_no_update(self, db):
        """Other event types don't update message status."""
        events = db.table("message_events")
        messages = db.table("messages")
        pk = await create_message(db, "msg1")
        ts = int(time.time())

        await events.add_event(pk, "bounce", ts)
        await events.add_event(pk, "pec_acceptance", ts)

        msg = await messages.get_by_pk(pk)
        assert msg["smtp_ts"] is None
        assert msg["deferred_ts"] is None
