# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for RateLimiter."""

import time
from unittest.mock import patch

import pytest

from core.mail_proxy.smtp.rate_limiter import (
    RateLimiter,
    WINDOW_MINUTE,
    WINDOW_HOUR,
    WINDOW_DAY,
)


class TestRateLimiter:
    """Tests for in-memory sliding-window rate limiter."""

    @pytest.fixture
    def rate_limiter(self):
        """Create a fresh RateLimiter for each test."""
        return RateLimiter()

    # =========================================================================
    # Basic functionality
    # =========================================================================

    async def test_no_limits_always_allows(self, rate_limiter):
        """Account without limits always allows sending."""
        account = {"id": "test-account"}
        deferred_until, should_reject = await rate_limiter.check_and_plan(account)
        assert deferred_until is None
        assert should_reject is False

    async def test_first_send_always_allowed(self, rate_limiter):
        """First send is always allowed when limits configured."""
        account = {"id": "test-account", "limit_per_minute": 10}
        deferred_until, should_reject = await rate_limiter.check_and_plan(account)
        assert deferred_until is None
        assert should_reject is False

    async def test_log_send_records_timestamp(self, rate_limiter):
        """log_send adds entry to send history."""
        await rate_limiter.log_send("test-account")
        assert "test-account" in rate_limiter._send_history
        assert len(rate_limiter._send_history["test-account"]) == 1

    async def test_release_slot_decrements_in_flight(self, rate_limiter):
        """release_slot decrements in-flight counter."""
        account = {"id": "test-account", "limit_per_minute": 10}
        await rate_limiter.check_and_plan(account)
        assert rate_limiter._in_flight.get("test-account", 0) == 1

        await rate_limiter.release_slot("test-account")
        assert rate_limiter._in_flight.get("test-account", 0) == 0

    async def test_release_slot_nonexistent_account(self, rate_limiter):
        """release_slot on nonexistent account is safe no-op."""
        await rate_limiter.release_slot("nonexistent")

    # =========================================================================
    # Per-minute limit
    # =========================================================================

    async def test_minute_limit_exceeded_defers(self, rate_limiter):
        """When minute limit exceeded, message is deferred."""
        account = {"id": "test-account", "limit_per_minute": 2}

        # First two should be allowed
        result1 = await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")

        result2 = await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")

        assert result1[0] is None
        assert result2[0] is None

        # Third should be deferred
        deferred_until, should_reject = await rate_limiter.check_and_plan(account)
        assert deferred_until is not None
        assert should_reject is False

    async def test_minute_limit_with_reject_behavior(self, rate_limiter):
        """When minute limit exceeded with reject behavior, should_reject=True."""
        account = {"id": "test-account", "limit_per_minute": 1, "limit_behavior": "reject"}

        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")

        deferred_until, should_reject = await rate_limiter.check_and_plan(account)
        assert deferred_until is not None
        assert should_reject is True

    async def test_minute_limit_respects_window(self, rate_limiter):
        """Sends outside window don't count against limit."""
        account = {"id": "test-account", "limit_per_minute": 1}

        # Simulate a send from 2 minutes ago
        old_ts = int(time.time()) - 120
        rate_limiter._send_history["test-account"] = [old_ts]

        # Current send should be allowed
        deferred_until, _ = await rate_limiter.check_and_plan(account)
        assert deferred_until is None

    # =========================================================================
    # Per-hour limit
    # =========================================================================

    async def test_hour_limit_exceeded_defers(self, rate_limiter):
        """When hour limit exceeded, message is deferred."""
        account = {"id": "test-account", "limit_per_hour": 2}

        # Fill up limit
        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")
        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")

        # Third should be deferred
        deferred_until, should_reject = await rate_limiter.check_and_plan(account)
        assert deferred_until is not None
        assert should_reject is False

    async def test_hour_limit_deferral_is_to_next_hour(self, rate_limiter):
        """Hour limit deferral is until next hour boundary."""
        account = {"id": "test-account", "limit_per_hour": 1}

        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")

        now = int(time.time())
        deferred_until, _ = await rate_limiter.check_and_plan(account)

        expected = ((now // WINDOW_HOUR) + 1) * WINDOW_HOUR
        assert deferred_until == expected

    # =========================================================================
    # Per-day limit
    # =========================================================================

    async def test_day_limit_exceeded_defers(self, rate_limiter):
        """When day limit exceeded, message is deferred."""
        account = {"id": "test-account", "limit_per_day": 2}

        # Fill up limit
        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")
        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")

        # Third should be deferred
        deferred_until, should_reject = await rate_limiter.check_and_plan(account)
        assert deferred_until is not None

    # =========================================================================
    # In-flight tracking
    # =========================================================================

    async def test_in_flight_counts_against_limit(self, rate_limiter):
        """In-flight sends count against limit."""
        account = {"id": "test-account", "limit_per_minute": 2}

        # First send - allowed, increments in_flight
        result1 = await rate_limiter.check_and_plan(account)
        assert result1[0] is None
        assert rate_limiter._in_flight["test-account"] == 1

        # Second send - allowed, increments in_flight
        result2 = await rate_limiter.check_and_plan(account)
        assert result2[0] is None
        assert rate_limiter._in_flight["test-account"] == 2

        # Third send - deferred (2 in_flight + 0 sent = 2 >= 2)
        deferred_until, _ = await rate_limiter.check_and_plan(account)
        assert deferred_until is not None

    async def test_log_send_releases_in_flight(self, rate_limiter):
        """log_send releases in-flight slot and records send."""
        account = {"id": "test-account", "limit_per_minute": 10}

        await rate_limiter.check_and_plan(account)
        assert rate_limiter._in_flight["test-account"] == 1

        await rate_limiter.log_send("test-account")
        assert rate_limiter._in_flight["test-account"] == 0
        assert len(rate_limiter._send_history["test-account"]) == 1

    # =========================================================================
    # Cleanup and purge
    # =========================================================================

    async def test_cleanup_old_entries(self, rate_limiter):
        """Old entries outside day window are cleaned up."""
        from collections import deque

        account = {"id": "test-account", "limit_per_day": 100}

        # Add some old entries (older than 1 day) using deque (as used internally)
        old_ts = int(time.time()) - WINDOW_DAY - 100
        rate_limiter._send_history["test-account"] = deque([old_ts, old_ts + 1])

        # Check triggers cleanup
        await rate_limiter.check_and_plan(account)

        # Old entries should be removed
        assert len(rate_limiter._send_history["test-account"]) == 0

    async def test_purge_for_account(self, rate_limiter):
        """purge_for_account clears all data for account."""
        account = {"id": "test-account", "limit_per_minute": 10}

        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")

        count = await rate_limiter.purge_for_account("test-account")

        assert count == 1
        assert "test-account" not in rate_limiter._send_history
        assert "test-account" not in rate_limiter._in_flight

    async def test_purge_nonexistent_account(self, rate_limiter):
        """purge_for_account on nonexistent account returns 0."""
        count = await rate_limiter.purge_for_account("nonexistent")
        assert count == 0

    def test_clear(self, rate_limiter):
        """clear removes all data."""
        rate_limiter._send_history["acc1"] = [123]
        rate_limiter._in_flight["acc1"] = 1

        rate_limiter.clear()

        assert len(rate_limiter._send_history) == 0
        assert len(rate_limiter._in_flight) == 0

    # =========================================================================
    # Multiple accounts
    # =========================================================================

    async def test_limits_are_per_account(self, rate_limiter):
        """Each account has independent limits."""
        account1 = {"id": "account-1", "limit_per_minute": 1}
        account2 = {"id": "account-2", "limit_per_minute": 1}

        # Both should allow first send
        result1 = await rate_limiter.check_and_plan(account1)
        await rate_limiter.log_send("account-1")

        result2 = await rate_limiter.check_and_plan(account2)
        await rate_limiter.log_send("account-2")

        assert result1[0] is None
        assert result2[0] is None

        # Both should defer second send
        deferred1, _ = await rate_limiter.check_and_plan(account1)
        deferred2, _ = await rate_limiter.check_and_plan(account2)

        assert deferred1 is not None
        assert deferred2 is not None

    # =========================================================================
    # Edge cases
    # =========================================================================

    async def test_zero_limit_is_ignored(self, rate_limiter):
        """Zero limit is treated as no limit."""
        account = {"id": "test-account", "limit_per_minute": 0}
        deferred_until, _ = await rate_limiter.check_and_plan(account)
        assert deferred_until is None

    async def test_negative_limit_is_ignored(self, rate_limiter):
        """Negative limit is treated as no limit."""
        account = {"id": "test-account", "limit_per_minute": -5}
        deferred_until, _ = await rate_limiter.check_and_plan(account)
        assert deferred_until is None

    async def test_string_limit_is_converted(self, rate_limiter):
        """String limit is converted to int."""
        account = {"id": "test-account", "limit_per_minute": "2"}

        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")
        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")

        deferred_until, _ = await rate_limiter.check_and_plan(account)
        assert deferred_until is not None

    async def test_multiple_limits_checked_in_order(self, rate_limiter):
        """Limits are checked minute -> hour -> day."""
        account = {
            "id": "test-account",
            "limit_per_minute": 10,
            "limit_per_hour": 2,
            "limit_per_day": 100,
        }

        # Hour limit should trigger first (after 2 sends)
        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")
        await rate_limiter.check_and_plan(account)
        await rate_limiter.log_send("test-account")

        now = int(time.time())
        deferred_until, _ = await rate_limiter.check_and_plan(account)

        # Should defer to next hour, not next minute
        expected_hour = ((now // WINDOW_HOUR) + 1) * WINDOW_HOUR
        assert deferred_until == expected_hour
