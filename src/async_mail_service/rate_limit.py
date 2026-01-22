# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Sliding-window rate limiter using persisted send logs.

This module implements per-account rate limiting with configurable limits
at minute, hour, and day granularity. The limiter uses SQLite-backed
persistence to track send history, enabling accurate rate limiting across
service restarts.

The sliding window approach ensures fair distribution of sends over time
rather than allowing burst behavior at window boundaries.

Example:
    Using the rate limiter::

        rate_limiter = RateLimiter(persistence)
        deferred_until = await rate_limiter.check_and_plan(account)
        if deferred_until:
            # Message should be deferred until this timestamp
            await persistence.set_deferred(msg_id, deferred_until)
        else:
            # Safe to send now
            await send_message(msg)
            await rate_limiter.log_send(account_id)
"""

import time
from typing import Any

from .persistence import Persistence


class RateLimiter:
    """Per-account sliding-window rate limiter backed by SQLite persistence.

    Enforces configurable send rate limits at three granularities:
    - Per minute
    - Per hour
    - Per day

    When any limit is exceeded, the limiter calculates the earliest timestamp
    at which the message can be safely sent without violating the limit.

    Attributes:
        persistence: The Persistence instance used to query send history.
    """

    def __init__(self, persistence: Persistence):
        """Initialize the rate limiter with a persistence backend.

        Args:
            persistence: A Persistence instance providing access to the
                send log table for counting recent sends.
        """
        self.persistence = persistence

    async def check_and_plan(self, account: dict[str, Any]) -> int | None:
        """Check rate limits and calculate deferral timestamp if exceeded.

        Evaluates the account's configured rate limits against recent send
        history. If any limit is exceeded, returns a Unix timestamp indicating
        when the message should be retried.

        Limits are checked in order of granularity (minute, hour, day) and
        the first exceeded limit determines the deferral time.

        Args:
            account: Account configuration dictionary containing:
                - id: The account identifier (required).
                - limit_per_minute: Max sends per minute (optional).
                - limit_per_hour: Max sends per hour (optional).
                - limit_per_day: Max sends per day (optional).

        Returns:
            Unix timestamp (seconds since epoch) until which the message
            should be deferred, or None if sending is permitted immediately.
        """
        account_id = account["id"]
        now = int(time.time())

        def lim(key: str) -> int | None:
            """Extract a positive integer limit or None."""
            v = account.get(key)
            if v is None:
                return None
            return int(v) if int(v) > 0 else None

        per_min = lim("limit_per_minute")
        per_hour = lim("limit_per_hour")
        per_day = lim("limit_per_day")

        if per_min is not None:
            c = await self.persistence.count_sends_since(account_id, now - 60)
            if c >= per_min:
                return (now // 60 + 1) * 60
        if per_hour is not None:
            c = await self.persistence.count_sends_since(account_id, now - 3600)
            if c >= per_hour:
                return (now // 3600 + 1) * 3600
        if per_day is not None:
            c = await self.persistence.count_sends_since(account_id, now - 86400)
            if c >= per_day:
                return (now // 86400 + 1) * 86400
        return None

    async def log_send(self, account_id: str) -> None:
        """Record a successful send for rate limiting purposes.

        Must be called after each successful message delivery to maintain
        accurate rate limit tracking.

        Args:
            account_id: The SMTP account identifier that sent the message.
        """
        await self.persistence.log_send(account_id, int(time.time()))
