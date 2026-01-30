# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for SendLogTable - rate limiting log."""

import time

import pytest

from core.mail_proxy.proxy_base import MailProxyBase


@pytest.fixture
async def db(tmp_path):
    """Create database with schema."""
    db_path = tmp_path / "test.db"
    db = MailProxyDb(str(db_path))
    await db.connect()
    await db.check_structure()
    yield db
    await db.close()


class TestSendLogTableLog:
    """Tests for SendLogTable.log() method."""

    async def test_log_creates_entry(self, db):
        """log() creates a send log entry."""
        send_log = db.table("send_log")
        ts = int(time.time())

        await send_log.log("account1", ts)

        count = await send_log.count_since("account1", ts - 1)
        assert count == 1

    async def test_log_multiple_entries(self, db):
        """log() creates multiple entries for same account."""
        send_log = db.table("send_log")
        ts = int(time.time())

        await send_log.log("account1", ts)
        await send_log.log("account1", ts + 1)
        await send_log.log("account1", ts + 2)

        count = await send_log.count_since("account1", ts - 1)
        assert count == 3

    async def test_log_different_accounts(self, db):
        """log() tracks entries separately per account."""
        send_log = db.table("send_log")
        ts = int(time.time())

        await send_log.log("account1", ts)
        await send_log.log("account2", ts)

        count1 = await send_log.count_since("account1", ts - 1)
        count2 = await send_log.count_since("account2", ts - 1)
        assert count1 == 1
        assert count2 == 1


class TestSendLogTableCountSince:
    """Tests for SendLogTable.count_since() method."""

    async def test_count_since_empty(self, db):
        """count_since() returns 0 when no entries."""
        send_log = db.table("send_log")
        ts = int(time.time())

        count = await send_log.count_since("account1", ts - 1)
        assert count == 0

    async def test_count_since_filters_by_timestamp(self, db):
        """count_since() only counts entries after threshold."""
        send_log = db.table("send_log")
        ts = int(time.time())

        await send_log.log("account1", ts - 100)  # Old
        await send_log.log("account1", ts - 50)   # Old
        await send_log.log("account1", ts)        # Recent
        await send_log.log("account1", ts + 1)    # Recent

        count = await send_log.count_since("account1", ts - 60)
        assert count == 3  # -50, ts, ts+1

        count = await send_log.count_since("account1", ts - 1)
        assert count == 2  # ts, ts+1

    async def test_count_since_filters_by_account(self, db):
        """count_since() only counts entries for specified account."""
        send_log = db.table("send_log")
        ts = int(time.time())

        await send_log.log("account1", ts)
        await send_log.log("account1", ts + 1)
        await send_log.log("account2", ts)

        count = await send_log.count_since("account1", ts - 1)
        assert count == 2

    async def test_count_since_nonexistent_account(self, db):
        """count_since() returns 0 for account with no entries."""
        send_log = db.table("send_log")
        ts = int(time.time())

        await send_log.log("account1", ts)

        count = await send_log.count_since("nonexistent", ts - 1)
        assert count == 0


class TestSendLogTablePurgeForAccount:
    """Tests for SendLogTable.purge_for_account() method."""

    async def test_purge_for_account_removes_all(self, db):
        """purge_for_account() removes all entries for account."""
        send_log = db.table("send_log")
        ts = int(time.time())

        await send_log.log("account1", ts)
        await send_log.log("account1", ts + 1)
        await send_log.log("account1", ts + 2)

        deleted = await send_log.purge_for_account("account1")
        assert deleted == 3

        count = await send_log.count_since("account1", ts - 1)
        assert count == 0

    async def test_purge_for_account_nonexistent(self, db):
        """purge_for_account() returns 0 for nonexistent account."""
        send_log = db.table("send_log")

        deleted = await send_log.purge_for_account("nonexistent")
        assert deleted == 0

    async def test_purge_for_account_preserves_others(self, db):
        """purge_for_account() doesn't affect other accounts."""
        send_log = db.table("send_log")
        ts = int(time.time())

        await send_log.log("account1", ts)
        await send_log.log("account2", ts)

        await send_log.purge_for_account("account1")

        count1 = await send_log.count_since("account1", ts - 1)
        count2 = await send_log.count_since("account2", ts - 1)
        assert count1 == 0
        assert count2 == 1


class TestSendLogTableRateLimitingScenarios:
    """Integration tests for rate limiting scenarios."""

    async def test_rate_limit_per_minute(self, db):
        """Simulate per-minute rate limit check."""
        send_log = db.table("send_log")
        now = int(time.time())
        one_minute_ago = now - 60

        # Simulate 5 sends in the last minute
        for i in range(5):
            await send_log.log("account1", now - i * 10)

        # Check rate
        count = await send_log.count_since("account1", one_minute_ago)
        assert count == 5

        # If limit is 10 per minute, we're under
        limit = 10
        assert count < limit

    async def test_rate_limit_per_hour(self, db):
        """Simulate per-hour rate limit check."""
        send_log = db.table("send_log")
        now = int(time.time())
        one_hour_ago = now - 3600

        # Simulate sends spread over the hour
        for i in range(20):
            await send_log.log("account1", now - i * 60)

        # Check rate
        count = await send_log.count_since("account1", one_hour_ago)
        assert count == 20

    async def test_rate_limit_per_day(self, db):
        """Simulate per-day rate limit check."""
        send_log = db.table("send_log")
        now = int(time.time())
        one_day_ago = now - 86400

        # Simulate sends spread over the day
        for i in range(100):
            await send_log.log("account1", now - i * 600)  # Every 10 minutes

        # Check rate
        count = await send_log.count_since("account1", one_day_ago)
        assert count == 100
