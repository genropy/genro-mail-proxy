# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""In-memory sliding-window rate limiter.

This module implements per-account rate limiting with configurable limits
at minute, hour, and day granularity. The limiter uses in-memory storage
to track send history, enabling fast rate limiting decisions.

The sliding window approach ensures fair distribution of sends over time
rather than allowing burst behavior at window boundaries.

To handle parallel dispatch correctly, the limiter tracks "in-flight" sends
in memory. This ensures that concurrent sends are counted even before they
complete and are logged.

Note: Send history is lost on service restart. This is acceptable for rate
limiting purposes as it only allows a brief window of potentially higher
sending rates after restart.

Example:
    Using the rate limiter::

        rate_limiter = RateLimiter()
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

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .sender import SmtpSender

logger = logging.getLogger(__name__)

# Time windows in seconds
WINDOW_MINUTE = 60
WINDOW_HOUR = 3600
WINDOW_DAY = 86400


class RateLimiter:
    """Per-account sliding-window rate limiter with in-memory storage.

    Enforces configurable send rate limits at three granularities:
    - Per minute
    - Per hour
    - Per day

    When any limit is exceeded, the limiter calculates the earliest timestamp
    at which the message can be safely sent without violating the limit.

    Tracks in-flight sends in memory to handle parallel dispatch correctly.
    """

    def __init__(self, smtp_sender: SmtpSender | None = None) -> None:
        """Initialize the rate limiter.

        Args:
            smtp_sender: Parent SmtpSender instance for accessing proxy resources.
        """
        self.smtp_sender = smtp_sender
        # Per-account send history: account_id -> deque of timestamps
        self._send_history: dict[str, deque[int]] = {}
        # In-flight sends: account_id -> count
        self._in_flight: dict[str, int] = {}
        self._lock = asyncio.Lock()

    def _count_since(self, account_id: str, since_ts: int) -> int:
        """Count sends since the given timestamp for an account.

        Args:
            account_id: The account identifier.
            since_ts: Unix timestamp to count from.

        Returns:
            Number of sends after since_ts.
        """
        history = self._send_history.get(account_id)
        if not history:
            return 0
        # Count entries > since_ts (deque is sorted oldest-first)
        return sum(1 for ts in history if ts > since_ts)

    def _cleanup_old_entries(self, account_id: str, now: int) -> None:
        """Remove entries older than the largest window (1 day).

        Args:
            account_id: The account identifier.
            now: Current unix timestamp.
        """
        history = self._send_history.get(account_id)
        if not history:
            return
        cutoff = now - WINDOW_DAY
        # Remove old entries from left side (oldest first)
        while history and history[0] <= cutoff:
            history.popleft()

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
            # Cleanup old entries first
            self._cleanup_old_entries(account_id, now)

            in_flight = self._in_flight.get(account_id, 0)
            logger.debug(
                "Rate check for %s: in_flight=%d, per_min=%s, per_hour=%s, per_day=%s",
                account_id,
                in_flight,
                per_min,
                per_hour,
                per_day,
            )

            if per_min is not None:
                c = self._count_since(account_id, now - WINDOW_MINUTE)
                logger.debug(
                    "Rate check %s: count=%d + in_flight=%d vs limit=%d",
                    account_id,
                    c,
                    in_flight,
                    per_min,
                )
                if c + in_flight >= per_min:
                    logger.info(
                        "Rate limit (minute) hit for %s: %d+%d >= %d, behavior=%s",
                        account_id,
                        c,
                        in_flight,
                        per_min,
                        behavior,
                    )
                    return ((now // WINDOW_MINUTE + 1) * WINDOW_MINUTE, behavior == "reject")

            if per_hour is not None:
                c = self._count_since(account_id, now - WINDOW_HOUR)
                if c + in_flight >= per_hour:
                    logger.info(
                        "Rate limit (hour) hit for %s: %d+%d >= %d",
                        account_id,
                        c,
                        in_flight,
                        per_hour,
                    )
                    return ((now // WINDOW_HOUR + 1) * WINDOW_HOUR, behavior == "reject")

            if per_day is not None:
                c = self._count_since(account_id, now - WINDOW_DAY)
                if c + in_flight >= per_day:
                    logger.info(
                        "Rate limit (day) hit for %s: %d+%d >= %d",
                        account_id,
                        c,
                        in_flight,
                        per_day,
                    )
                    return ((now // WINDOW_DAY + 1) * WINDOW_DAY, behavior == "reject")

            # Reserve a slot for this send
            self._in_flight[account_id] = in_flight + 1
            logger.debug("Rate check %s: ALLOWED, in_flight now %d", account_id, in_flight + 1)

        return (None, False)

    async def log_send(self, account_id: str) -> None:
        """Record a successful send for rate limiting purposes.

        Must be called after each successful message delivery to maintain
        accurate rate limit tracking. Releases the in-flight slot reserved
        by check_and_plan().

        Args:
            account_id: The SMTP account identifier that sent the message.
        """
        now = int(time.time())
        async with self._lock:
            # Release in-flight slot
            if account_id in self._in_flight and self._in_flight[account_id] > 0:
                self._in_flight[account_id] -= 1

            # Add to send history
            if account_id not in self._send_history:
                self._send_history[account_id] = deque()
            self._send_history[account_id].append(now)

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

    async def purge_for_account(self, account_id: str) -> int:
        """Clear all rate limit data for an account.

        Call this when an account is deleted.

        Args:
            account_id: The SMTP account identifier.

        Returns:
            Number of entries cleared.
        """
        async with self._lock:
            count = 0
            if account_id in self._send_history:
                count = len(self._send_history[account_id])
                del self._send_history[account_id]
            if account_id in self._in_flight:
                del self._in_flight[account_id]
            return count

    def clear(self) -> None:
        """Clear all rate limit data. Used for testing."""
        self._send_history.clear()
        self._in_flight.clear()


__all__ = ["RateLimiter"]
