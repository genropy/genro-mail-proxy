import pytest

from mail_proxy.mailproxy_db import MailProxyDb
from mail_proxy.rate_limit import RateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_defer(tmp_path):
    db = tmp_path / "test.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    limiter = RateLimiter(p)
    acc = {"id":"acc1","limit_per_minute":1}
    assert await limiter.check_and_plan(acc) is None
    await limiter.log_send("acc1")
    assert await limiter.check_and_plan(acc) is not None


@pytest.mark.asyncio
async def test_rate_limiter_ignores_zero_limits(tmp_path):
    db = tmp_path / "zero.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    limiter = RateLimiter(p)

    acc = {"id": "acc0", "limit_per_minute": 0, "limit_per_hour": 0, "limit_per_day": 0}
    await limiter.log_send("acc0")
    assert await limiter.check_and_plan(acc) is None

@pytest.mark.asyncio
async def test_rate_limiter_hour_and_day(tmp_path, monkeypatch):
    db = tmp_path / "limits.db"
    p = MailProxyDb(str(db))
    await p.init_db()
    limiter = RateLimiter(p)

    current_time = 3600 * 10 + 30  # hour boundary plus 30 seconds
    monkeypatch.setattr("mail_proxy.rate_limit.time.time", lambda: current_time)

    await limiter.log_send("acc2")
    await p.log_send("acc2", current_time - 10)
    await p.log_send("acc2", current_time - 3500)
    await p.log_send("acc2", current_time - 86000)

    acc = {"id": "acc2", "limit_per_hour": 2, "limit_per_day": 3}
    defer_until = await limiter.check_and_plan(acc)
    assert defer_until == ((current_time // 3600) + 1) * 3600

    # Relax hourly limit but keep daily cap hit
    acc["limit_per_hour"] = None
    defer_until = await limiter.check_and_plan(acc)
    assert defer_until == ((current_time // 86400) + 1) * 86400
