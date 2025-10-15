import pytest
from async_mail_service.persistence import Persistence

@pytest.mark.asyncio
async def test_account_crud(tmp_path):
    db = tmp_path / "test.db"
    p = Persistence(str(db))
    await p.init_db()
    await p.add_account({"id":"gmail","host":"smtp.gmail.com","port":587,"user":"a","password":"b","ttl":300,"use_tls":False})
    lst = await p.list_accounts()
    assert len(lst) == 1
    assert lst[0]["use_tls"] is False
    acc = await p.get_account("gmail")
    assert acc["use_tls"] is False
    await p.delete_account("gmail")
    lst = await p.list_accounts()
    assert len(lst) == 0

@pytest.mark.asyncio
async def test_pending_crud(tmp_path):
    db = tmp_path / "pending.db"
    p = Persistence(str(db))
    await p.init_db()
    await p.add_pending("msg1", "to@example.com", "Subject", "acc")
    pending = await p.list_pending()
    assert pending[0]["id"] == "msg1"
    assert pending[0]["account_id"] == "acc"
    await p.remove_pending("msg1")
    assert await p.list_pending() == []

@pytest.mark.asyncio
async def test_deferred_crud(tmp_path):
    db = tmp_path / "deferred.db"
    p = Persistence(str(db))
    await p.init_db()
    await p.set_deferred("msg1", "acc", 123)
    assert await p.get_deferred_until("msg1", "acc") == 123
    deferred = await p.list_deferred()
    assert deferred[0]["account_id"] == "acc"
    await p.clear_deferred("msg1")
    assert await p.get_deferred_until("msg1", "acc") is None

@pytest.mark.asyncio
async def test_send_log_and_counts(tmp_path):
    db = tmp_path / "log.db"
    p = Persistence(str(db))
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
    p = Persistence(str(db))
    await p.init_db()
    with pytest.raises(ValueError):
        await p.get_account("unknown")


@pytest.mark.asyncio
async def test_schedule_rules_crud(tmp_path):
    db = tmp_path / "rules.db"
    p = Persistence(str(db))
    await p.init_db()
    assert await p.list_rules() == []
    rule = await p.add_rule({"name": "default", "interval_minutes": 2, "days": [1, 2]})
    assert rule["priority"] == 0
    rules = await p.list_rules()
    assert len(rules) == 1
    await p.set_rule_enabled(rule["id"], False)
    rules = await p.list_rules()
    assert rules[0]["enabled"] is False
    await p.delete_rule(rule["id"])
    assert await p.list_rules() == []


@pytest.mark.asyncio
async def test_delivery_reports_persistence(tmp_path):
    db = tmp_path / "delivery.db"
    p = Persistence(str(db))
    await p.init_db()
    report_id = await p.save_delivery_report({"id": "msg1", "status": "sent"})
    reports = await p.list_delivery_reports()
    assert len(reports) == 1
    assert reports[0]["id"] == report_id
    assert reports[0]["payload"]["status"] == "sent"
    await p.increment_report_retry(report_id)
    reports = await p.list_delivery_reports()
    assert reports[0]["retry_count"] == 1
    await p.delete_delivery_report(report_id)
    assert await p.list_delivery_reports() == []
