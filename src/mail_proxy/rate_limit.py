# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Sliding-window rate limiter using persisted send logs.

This module implements per-account rate limiting with configurable limits
at minute, hour, and day granularity. The limiter uses SQLite-backed
persistence to track send history, enabling accurate rate limiting across
service restarts.

The sliding window approach ensures fair distribution of sends over time
rather than allowing burst behavior at window boundaries.

To handle parallel dispatch correctly, the limiter tracks "in-flight" sends
in memory. This ensures that concurrent sends are counted even before they
complete and are logged to the database.

Example:
    Using the rate limiter::

        rate_limiter = RateLimiter(persistence)
        deferred_until, should_reject = await rate_limiter.check_and_plan(account)
        if deferred_until:
            if should_reject:
                # Message should be rejected with rate limit error
                return {"error": "rate_limit_exceeded"}
            else:
                # Message should be deferred until this timestamp
                await persistence.set_deferred(msg_id, deferred_until)
        else:
            # Safe to send now
            try:
                await send_message(msg)
                await rate_limiter.log_send(account_id)
            except Exception:
                await rate_limiter.release_slot(account_id)
                raise
"""

import asyncio
import logging
import time
from typing import Any

from .mailproxy_db import MailProxyDb

logger = logging.getLogger(__name__)


class RateLimiter:
    """Per-account sliding-window rate limiter backed by SQLite persistence.

    Enforces configurable send rate limits at three granularities:
    - Per minute
    - Per hour
    - Per day

    When any limit is exceeded, the limiter calculates the earliest timestamp
    at which the message can be safely sent without violating the limit.

    Tracks in-flight sends in memory to handle parallel dispatch correctly.

    Attributes:
        db: The MailProxyDb instance used to query send history.
    """

    def __init__(self, db: MailProxyDb):
        """Initialize the rate limiter with a database backend.

        Args:
            db: A MailProxyDb instance providing access to the
                send log table for counting recent sends.
        """
        self.db = db
        self._in_flight: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def check_and_plan(self, account: dict[str, Any]) -> tuple[int | None, bool]:
        """Check rate limits and calculate deferral timestamp if exceeded.

        Evaluates the account's configured rate limits against recent send
        history plus in-flight sends. If any limit is exceeded, returns a
        tuple indicating the deferral time and whether to reject.

        If the check passes, reserves a slot by incrementing the in-flight
        counter. The caller MUST call either log_send() on success or
        release_slot() on failure to release the reservation.

        Limits are checked in order of granularity (minute, hour, day) and
        the first exceeded limit determines the deferral time.

        Args:
            account: Account configuration dictionary containing:
                - id: The account identifier (required).
                - limit_per_minute: Max sends per minute (optional).
                - limit_per_hour: Max sends per hour (optional).
                - limit_per_day: Max sends per day (optional).
                - limit_behavior: "defer" (default) or "reject".

        Returns:
            Tuple of (deferred_until, should_reject):
            - deferred_until: Unix timestamp until which message should be
              deferred, or None if sending is permitted immediately.
            - should_reject: True if limit_behavior is "reject" and limit
              was exceeded, meaning message should be rejected with error.
        """
        account_id = account["id"]
        now = int(time.time())
        behavior = account.get("limit_behavior", "defer")

        def lim(key: str) -> int | None:
            """Extract a positive integer limit or None."""
            v = account.get(key)
            if v is None:
                return None
            return int(v) if int(v) > 0 else None

        per_min = lim("limit_per_minute")
        per_hour = lim("limit_per_hour")
        per_day = lim("limit_per_day")

        # No limits configured - allow immediately
        if per_min is None and per_hour is None and per_day is None:
            return (None, False)

        async with self._lock:
            in_flight = self._in_flight.get(account_id, 0)
            logger.warning(
                "Rate check for %s: in_flight=%d, per_min=%s, per_hour=%s, per_day=%s",
                account_id, in_flight, per_min, per_hour, per_day
            )

            if per_min is not None:
                c = await self.db.count_sends_since(account_id, now - 60)
                logger.warning("Rate check %s: db_count=%d + in_flight=%d vs limit=%d", account_id, c, in_flight, per_min)
                if c + in_flight >= per_min:
                    logger.warning("Rate limit HIT for %s: %d+%d >= %d, behavior=%s", account_id, c, in_flight, per_min, behavior)
                    return ((now // 60 + 1) * 60, behavior == "reject")
            if per_hour is not None:
                c = await self.db.count_sends_since(account_id, now - 3600)
                if c + in_flight >= per_hour:
                    logger.info("Rate limit (hour) hit for %s: %d+%d >= %d", account_id, c, in_flight, per_hour)
                    return ((now // 3600 + 1) * 3600, behavior == "reject")
            if per_day is not None:
                c = await self.db.count_sends_since(account_id, now - 86400)
                if c + in_flight >= per_day:
                    logger.info("Rate limit (day) hit for %s: %d+%d >= %d", account_id, c, in_flight, per_day)
                    return ((now // 86400 + 1) * 86400, behavior == "reject")

            # Reserve a slot for this send
            self._in_flight[account_id] = in_flight + 1
            logger.warning("Rate check %s: ALLOWED, in_flight now %d", account_id, in_flight + 1)

        return (None, False)

    async def log_send(self, account_id: str) -> None:
        """Record a successful send for rate limiting purposes.

        Must be called after each successful message delivery to maintain
        accurate rate limit tracking. Releases the in-flight slot reserved
        by check_and_plan().

        Args:
            account_id: The SMTP account identifier that sent the message.
        """
        async with self._lock:
            if account_id in self._in_flight and self._in_flight[account_id] > 0:
                self._in_flight[account_id] -= 1
        await self.db.log_send(account_id, int(time.time()))

    async def release_slot(self, account_id: str) -> None:
        """Release an in-flight slot without logging a send.

        Call this when a send fails after check_and_plan() returned None.
        This ensures the in-flight counter stays accurate.

        Args:
            account_id: The SMTP account identifier.
        """
        async with self._lock:
            if account_id in self._in_flight and self._in_flight[account_id] > 0:
                self._in_flight[account_id] -= 1
