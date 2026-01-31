# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Retry strategy for SMTP message delivery.

This module provides a configurable retry strategy that determines when
and how to retry failed message deliveries.

Example:
    Using default retry strategy::

        strategy = RetryStrategy()

        # Check if error is temporary
        is_temp, smtp_code = strategy.classify_error(exception)

        # Check if should retry
        if strategy.should_retry(retry_count, exception):
            delay = strategy.calculate_delay(retry_count)
            # schedule retry after delay seconds
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import aiosmtplib

DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_DELAYS = (60, 300, 900, 3600, 7200)  # 1min, 5min, 15min, 1h, 2h


@dataclass
class RetryStrategy:
    """Configuration and logic for message retry behavior.

    Attributes:
        max_retries: Maximum number of retry attempts before permanent failure.
        delays: Tuple of delay intervals in seconds for each retry attempt.
            If retry_count exceeds the length, the last delay is used.
    """

    max_retries: int = DEFAULT_MAX_RETRIES
    delays: tuple[int, ...] = DEFAULT_RETRY_DELAYS

    def calculate_delay(self, retry_count: int) -> int:
        """Calculate delay in seconds before next retry attempt.

        Args:
            retry_count: Number of previous retry attempts (0-indexed).

        Returns:
            Delay in seconds before next retry.
        """
        if retry_count >= len(self.delays):
            return self.delays[-1]
        return self.delays[retry_count]

    def should_retry(self, retry_count: int, error: Exception) -> bool:
        """Determine if message should be retried.

        Args:
            retry_count: Current retry count (0-indexed).
            error: The exception that caused the failure.

        Returns:
            True if the message should be retried.
        """
        if retry_count >= self.max_retries:
            return False
        is_temporary, _ = self.classify_error(error)
        return is_temporary

    def classify_error(self, exc: Exception) -> tuple[bool, int | None]:
        """Classify an SMTP error as temporary or permanent.

        Args:
            exc: The exception to classify.

        Returns:
            Tuple of (is_temporary, smtp_code).
            - is_temporary: True if the error should trigger a retry.
            - smtp_code: The SMTP error code if available, None otherwise.
        """
        smtp_code = self._extract_smtp_code(exc)

        # Network/timeout errors are temporary
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
            return True, smtp_code

        # SMTP-specific codes
        if smtp_code:
            if 400 <= smtp_code < 500:
                return True, smtp_code  # 4xx = temporary
            if 500 <= smtp_code < 600:
                return False, smtp_code  # 5xx = permanent

        # Check error message patterns
        error_msg = str(exc).lower()

        if self._matches_temporary_pattern(error_msg):
            return True, smtp_code

        if self._matches_permanent_pattern(error_msg):
            return False, smtp_code

        # Default: treat unknown errors as temporary (safer for retry)
        return True, smtp_code

    def _extract_smtp_code(self, exc: Exception) -> int | None:
        """Extract SMTP code from exception if available."""
        if isinstance(exc, aiosmtplib.SMTPException):
            return getattr(exc, "smtp_code", None) or getattr(exc, "code", None)
        return None

    def _matches_temporary_pattern(self, error_msg: str) -> bool:
        """Check if error message matches temporary error patterns."""
        temporary_patterns = (
            "421",  # Service not available
            "450",  # Mailbox unavailable
            "451",  # Local error in processing
            "452",  # Insufficient system storage
            "timeout",
            "connection refused",
            "connection reset",
            "temporarily unavailable",
            "try again",
            "throttl",  # throttled/throttling
        )
        return any(pattern in error_msg for pattern in temporary_patterns)

    def _matches_permanent_pattern(self, error_msg: str) -> bool:
        """Check if error message matches permanent error patterns."""
        permanent_patterns = (
            "wrong_version_number",  # TLS/STARTTLS mismatch
            "certificate verify failed",
            "ssl handshake",
            "certificate_unknown",
            "unknown_ca",
            "certificate has expired",
            "self signed certificate",
            "authentication failed",
            "auth",  # Authentication errors
            "535",  # Authentication credentials invalid
            "534",  # Authentication mechanism too weak
            "530",  # Authentication required
        )
        return any(pattern in error_msg for pattern in permanent_patterns)


__all__ = ["RetryStrategy", "DEFAULT_MAX_RETRIES", "DEFAULT_RETRY_DELAYS"]
