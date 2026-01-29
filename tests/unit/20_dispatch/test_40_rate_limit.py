import pytest

from core.mail_proxy.mailproxy_db import MailProxyDb
from core.mail_proxy.rate_limit import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_defer(tmp_path):
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    limiter = RateLimiter(p)
    acc = {"id": "acc1", "limit_per_minute": 1}
    deferred_until, should_reject = await limiter.check_and_plan(acc)
    assert deferred_until is None
    assert should_reject is False
    await limiter.log_send("acc1")
    deferred_until, should_reject = await limiter.check_and_plan(acc)
    assert deferred_until is not None
    assert should_reject is False  # default behavior is defer


@pytest.mark.asyncio
async def test_rate_limiter_ignores_zero_limits(tmp_path):
    db = tmp_path / "zero.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    limiter = RateLimiter(p)

    acc = {"id": "acc0", "limit_per_minute": 0, "limit_per_hour": 0, "limit_per_day": 0}
    await limiter.log_send("acc0")
    deferred_until, should_reject = await limiter.check_and_plan(acc)
    assert deferred_until is None  # zero limits are ignored
    assert should_reject is False


@pytest.mark.asyncio
async def test_rate_limiter_hour_and_day(tmp_path, monkeypatch):
    db = tmp_path / "limits.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    limiter = RateLimiter(p)

    current_time = 3600 * 10 + 30  # hour boundary plus 30 seconds
    monkeypatch.setattr("core.mail_proxy.rate_limit.time.time", lambda: current_time)

    await limiter.log_send("acc2")
    await p.log_send("acc2", current_time - 10)
    await p.log_send("acc2", current_time - 3500)
    await p.log_send("acc2", current_time - 86000)

    acc = {"id": "acc2", "limit_per_hour": 2, "limit_per_day": 3}
    deferred_until, should_reject = await limiter.check_and_plan(acc)
    assert deferred_until == ((current_time // 3600) + 1) * 3600
    assert should_reject is False

    # Relax hourly limit but keep daily cap hit
    acc["limit_per_hour"] = None
    deferred_until, should_reject = await limiter.check_and_plan(acc)
    assert deferred_until == ((current_time // 86400) + 1) * 86400
    assert should_reject is False


@pytest.mark.asyncio
async def test_rate_limiter_reject_behavior(tmp_path):
    """Test that limit_behavior='reject' causes should_reject=True."""
    db = tmp_path / "reject.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    limiter = RateLimiter(p)

    acc = {"id": "acc-reject", "limit_per_minute": 1, "limit_behavior": "reject"}

    # First call should pass
    deferred_until, should_reject = await limiter.check_and_plan(acc)
    assert deferred_until is None
    assert should_reject is False

    await limiter.log_send("acc-reject")

    # Second call should hit limit and reject
    deferred_until, should_reject = await limiter.check_and_plan(acc)
    assert deferred_until is not None
    assert should_reject is True  # reject behavior
