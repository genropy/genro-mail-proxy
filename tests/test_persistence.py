import time

import pytest

from async_mail_service.mailproxy_db import MailProxyDb


@pytest.mark.asyncio
async def test_account_crud(tmp_path):
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    await p.add_account(
        {
            "id": "gmail",
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
    acc = await p.get_account("gmail")
    assert acc["use_tls"] is False
    await p.delete_account("gmail")
    lst = await p.list_accounts()
    assert len(lst) == 0


@pytest.mark.asyncio
async def test_messages_lifecycle(tmp_path):
    db = tmp_path / "messages.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    now = int(time.time())
    inserted = await p.insert_messages(
        [
            {
                "id": "msg1",
                "account_id": "acc",
                "priority": 2,
                "payload": {"id": "msg1", "from": "a@example.com", "to": "b@example.com", "body": "hello"},
            }
        ]
    )
    assert inserted == ["msg1"]
    ready = await p.fetch_ready_messages(limit=10, now_ts=now)
    assert len(ready) == 1
    assert ready[0]["id"] == "msg1"
    await p.set_deferred("msg1", now + 60)
    assert await p.fetch_ready_messages(limit=10, now_ts=now) == []
    await p.clear_deferred("msg1")
    ready = await p.fetch_ready_messages(limit=10, now_ts=now)
    assert len(ready) == 1
    await p.mark_error("msg1", now, "boom")
    assert await p.fetch_ready_messages(limit=10, now_ts=now + 120) == []
    reports = await p.fetch_reports(10)
    assert reports[0]["error"] == "boom"
    await p.mark_sent("msg1", now + 1)
    reports = await p.fetch_reports(10)
    assert reports[0]["sent_ts"] == now + 1
    await p.mark_reported(["msg1"], now + 2)
    removed = await p.remove_reported_before(now + 10)
    assert removed == 1
    assert await p.list_messages() == []


@pytest.mark.asyncio
async def test_existing_ids(tmp_path):
    db = tmp_path / "existing.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    await p.insert_messages(
        [
            {
                "id": "msg1",
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
    with pytest.raises(ValueError):
        await p.get_account("unknown")


@pytest.mark.asyncio
async def test_fetch_ready_messages_priority_filter(tmp_path):
    """Test fetch_ready_messages with priority and min_priority filters."""
    db = tmp_path / "priority.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    now = int(time.time())

    # Insert messages with different priorities
    await p.insert_messages([
        {"id": "immediate1", "account_id": "acc", "priority": 0, "payload": {"id": "immediate1", "body": "a"}},
        {"id": "immediate2", "account_id": "acc", "priority": 0, "payload": {"id": "immediate2", "body": "b"}},
        {"id": "high", "account_id": "acc", "priority": 1, "payload": {"id": "high", "body": "c"}},
        {"id": "normal", "account_id": "acc", "priority": 2, "payload": {"id": "normal", "body": "d"}},
        {"id": "low", "account_id": "acc", "priority": 3, "payload": {"id": "low", "body": "e"}},
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
