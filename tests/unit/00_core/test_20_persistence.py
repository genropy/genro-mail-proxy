import time

import pytest

from core.mail_proxy.mailproxy_db import MailProxyDb


@pytest.mark.asyncio
async def test_account_crud(tmp_path):
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    # Create a tenant first - accounts require tenant_id
    await p.table('tenants').add({"id": "test_tenant", "name": "Test"})
    await p.add_account(
        {
            "id": "gmail",
            "tenant_id": "test_tenant",
            "host": "smtp.gmail.com",
            "port": 587,
            "user": "a",
            "password": "b",
            "ttl": 300,
            "use_tls": False,
        }
    )
    lst = await p.list_accounts()
    assert len(lst) == 1
    assert lst[0]["use_tls"] is False
    acc = await p.get_account("test_tenant", "gmail")
    assert acc["use_tls"] is False
    await p.delete_account("test_tenant", "gmail")
    lst = await p.list_accounts()
    assert len(lst) == 0


@pytest.mark.asyncio
async def test_messages_lifecycle(tmp_path):
    """Test message lifecycle: insert, defer, send, error, and cleanup via events."""
    db = tmp_path / "messages.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    # Create tenant first - messages require tenant_id
    await p.table('tenants').add({"id": "test_tenant", "name": "Test"})
    now = int(time.time())
    inserted = await p.insert_messages(
        [
            {
                "id": "msg1",
                "tenant_id": "test_tenant",
                "account_id": "acc",
                "priority": 2,
                "payload": {"id": "msg1", "from": "a@example.com", "to": "b@example.com", "body": "hello"},
            }
        ]
    )
    # insert_messages now returns list of {"id": msg_id, "pk": pk}
    assert len(inserted) == 1
    assert inserted[0]["id"] == "msg1"
    pk = inserted[0]["pk"]

    ready = await p.fetch_ready_messages(limit=10, now_ts=now)
    assert len(ready) == 1
    assert ready[0]["id"] == "msg1"
    assert ready[0]["pk"] == pk

    # Test deferral - use pk for internal operations
    await p.set_deferred(pk, now + 60)
    assert await p.fetch_ready_messages(limit=10, now_ts=now) == []
    await p.clear_deferred(pk)
    ready = await p.fetch_ready_messages(limit=10, now_ts=now)
    assert len(ready) == 1

    # Test error - marks message as processed with smtp_ts
    await p.mark_error(pk, now, "boom")
    # Message is no longer ready (smtp_ts is set)
    assert await p.fetch_ready_messages(limit=10, now_ts=now + 120) == []

    # Verify error event was created
    events = await p.fetch_unreported_events(limit=10)
    error_events = [e for e in events if e["event_type"] == "error"]
    assert len(error_events) == 1
    assert error_events[0]["description"] == "boom"

    # Test sent - updates smtp_ts
    await p.mark_sent(pk, now + 1)

    # Verify sent event was created
    events = await p.fetch_unreported_events(limit=10)
    sent_events = [e for e in events if e["event_type"] == "sent"]
    assert len(sent_events) == 1
    assert sent_events[0]["event_ts"] == now + 1

    # Mark events as reported
    event_ids = [e["event_id"] for e in events]
    await p.mark_events_reported(event_ids, now + 2)

    # Retention cleanup via events
    removed = await p.remove_fully_reported_before(now + 10)
    assert removed == 1
    assert await p.list_messages("test_tenant") == []


@pytest.mark.asyncio
async def test_existing_ids(tmp_path):
    db = tmp_path / "existing.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    await p.table('tenants').add({"id": "test_tenant", "name": "Test"})
    await p.insert_messages(
        [
            {
                "id": "msg1",
                "tenant_id": "test_tenant",
                "account_id": None,
                "priority": 2,
                "payload": {"id": "msg1", "from": "a", "to": "b", "body": "hi"},
            }
        ]
    )
    existing = await p.existing_message_ids(["msg1", "msg2"])
    assert existing == {"msg1"}


@pytest.mark.asyncio
async def test_send_log_and_counts(tmp_path):
    db = tmp_path / "log.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    await p.log_send("acc", 10)
    await p.log_send("acc", 20)
    await p.log_send("other", 25)
    assert await p.count_sends_since("acc", 15) == 1
    assert await p.count_sends_since("acc", 5) == 2
    assert await p.count_sends_since("acc", 25) == 0


@pytest.mark.asyncio
async def test_get_account_missing_raises(tmp_path):
    db = tmp_path / "missing.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    await p.table('tenants').add({"id": "test_tenant", "name": "Test"})
    with pytest.raises(ValueError):
        await p.get_account("test_tenant", "unknown")


@pytest.mark.asyncio
async def test_fetch_ready_messages_priority_filter(tmp_path):
    """Test fetch_ready_messages with priority and min_priority filters."""
    db = tmp_path / "priority.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    await p.table('tenants').add({"id": "test_tenant", "name": "Test"})
    now = int(time.time())

    # Insert messages with different priorities
    await p.insert_messages([
        {"id": "immediate1", "tenant_id": "test_tenant", "account_id": "acc", "priority": 0, "payload": {"id": "immediate1", "body": "a"}},
        {"id": "immediate2", "tenant_id": "test_tenant", "account_id": "acc", "priority": 0, "payload": {"id": "immediate2", "body": "b"}},
        {"id": "high", "tenant_id": "test_tenant", "account_id": "acc", "priority": 1, "payload": {"id": "high", "body": "c"}},
        {"id": "normal", "tenant_id": "test_tenant", "account_id": "acc", "priority": 2, "payload": {"id": "normal", "body": "d"}},
        {"id": "low", "tenant_id": "test_tenant", "account_id": "acc", "priority": 3, "payload": {"id": "low", "body": "e"}},
    ])

    # Fetch only immediate priority (priority=0)
    immediate = await p.fetch_ready_messages(limit=10, now_ts=now, priority=0)
    assert len(immediate) == 2
    assert all(m["priority"] == 0 for m in immediate)

    # Fetch only min_priority >= 1 (non-immediate)
    regular = await p.fetch_ready_messages(limit=10, now_ts=now, min_priority=1)
    assert len(regular) == 3
    assert all(m["priority"] >= 1 for m in regular)

    # Verify ordering by priority
    assert regular[0]["priority"] == 1  # high
    assert regular[1]["priority"] == 2  # normal
    assert regular[2]["priority"] == 3  # low

    # Fetch all (no filter)
    all_msgs = await p.fetch_ready_messages(limit=10, now_ts=now)
    assert len(all_msgs) == 5
