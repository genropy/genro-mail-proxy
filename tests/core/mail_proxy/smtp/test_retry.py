# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for RetryStrategy."""

import asyncio

import aiosmtplib
import pytest

from core.mail_proxy.smtp.retry import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_RETRY_DELAYS,
    RetryStrategy,
)


class TestRetryStrategy:
    """Tests for RetryStrategy configuration and logic."""

    def test_default_values(self):
        """Strategy uses default values when not specified."""
        strategy = RetryStrategy()
        assert strategy.max_retries == DEFAULT_MAX_RETRIES
        assert strategy.delays == DEFAULT_RETRY_DELAYS

    def test_custom_values(self):
        """Strategy accepts custom configuration."""
        strategy = RetryStrategy(max_retries=3, delays=(10, 20, 30))
        assert strategy.max_retries == 3
        assert strategy.delays == (10, 20, 30)

    # =========================================================================
    # calculate_delay tests
    # =========================================================================

    def test_calculate_delay_first_retry(self):
        """First retry uses first delay value."""
        strategy = RetryStrategy(delays=(60, 300, 900))
        assert strategy.calculate_delay(0) == 60

    def test_calculate_delay_second_retry(self):
        """Second retry uses second delay value."""
        strategy = RetryStrategy(delays=(60, 300, 900))
        assert strategy.calculate_delay(1) == 300

    def test_calculate_delay_exceeds_length(self):
        """When retry count exceeds delays, uses last value."""
        strategy = RetryStrategy(delays=(60, 300, 900))
        assert strategy.calculate_delay(10) == 900

    def test_calculate_delay_exact_boundary(self):
        """At exact boundary, uses last value."""
        strategy = RetryStrategy(delays=(60, 300, 900))
        assert strategy.calculate_delay(3) == 900

    # =========================================================================
    # should_retry tests
    # =========================================================================

    def test_should_retry_first_attempt_temporary_error(self):
        """First attempt with temporary error should retry."""
        strategy = RetryStrategy(max_retries=3)
        error = asyncio.TimeoutError()
        assert strategy.should_retry(0, error) is True

    def test_should_retry_max_retries_reached(self):
        """Should not retry when max retries reached."""
        strategy = RetryStrategy(max_retries=3)
        error = asyncio.TimeoutError()
        assert strategy.should_retry(3, error) is False

    def test_should_retry_permanent_error(self):
        """Should not retry permanent errors even on first attempt."""
        strategy = RetryStrategy(max_retries=5)
        error = Exception("authentication failed")
        assert strategy.should_retry(0, error) is False

    # =========================================================================
    # classify_error tests - Network errors
    # =========================================================================

    def test_classify_timeout_error(self):
        """asyncio.TimeoutError is temporary."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(asyncio.TimeoutError())
        assert is_temp is True
        assert code is None

    def test_classify_builtin_timeout_error(self):
        """Built-in TimeoutError is temporary."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(TimeoutError())
        assert is_temp is True
        assert code is None

    def test_classify_connection_error(self):
        """ConnectionError is temporary."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(ConnectionError())
        assert is_temp is True
        assert code is None

    def test_classify_os_error(self):
        """OSError is temporary."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(OSError())
        assert is_temp is True
        assert code is None

    # =========================================================================
    # classify_error tests - SMTP codes
    # =========================================================================

    def test_classify_smtp_4xx_temporary(self):
        """SMTP 4xx errors are temporary."""
        strategy = RetryStrategy()
        error = aiosmtplib.SMTPResponseException(450, "Mailbox unavailable")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True
        assert code == 450

    def test_classify_smtp_421_service_unavailable(self):
        """SMTP 421 Service not available is temporary."""
        strategy = RetryStrategy()
        error = aiosmtplib.SMTPResponseException(421, "Service not available")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True
        assert code == 421

    def test_classify_smtp_451_local_error(self):
        """SMTP 451 Local error is temporary."""
        strategy = RetryStrategy()
        error = aiosmtplib.SMTPResponseException(451, "Local error in processing")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True
        assert code == 451

    def test_classify_smtp_452_insufficient_storage(self):
        """SMTP 452 Insufficient storage is temporary."""
        strategy = RetryStrategy()
        error = aiosmtplib.SMTPResponseException(452, "Insufficient system storage")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True
        assert code == 452

    def test_classify_smtp_5xx_permanent(self):
        """SMTP 5xx errors are permanent."""
        strategy = RetryStrategy()
        error = aiosmtplib.SMTPResponseException(550, "Mailbox not found")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is False
        assert code == 550

    def test_classify_smtp_535_auth_failed(self):
        """SMTP 535 Authentication failed is permanent."""
        strategy = RetryStrategy()
        error = aiosmtplib.SMTPResponseException(535, "Authentication credentials invalid")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is False
        assert code == 535

    def test_classify_smtp_530_auth_required(self):
        """SMTP 530 Authentication required is permanent."""
        strategy = RetryStrategy()
        error = aiosmtplib.SMTPResponseException(530, "Authentication required")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is False
        assert code == 530

    # =========================================================================
    # classify_error tests - Error message patterns
    # =========================================================================

    def test_classify_timeout_message(self):
        """Error message containing 'timeout' is temporary."""
        strategy = RetryStrategy()
        error = Exception("Connection timeout after 30s")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True

    def test_classify_connection_refused_message(self):
        """Error message containing 'connection refused' is temporary."""
        strategy = RetryStrategy()
        error = Exception("Connection refused by server")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True

    def test_classify_connection_reset_message(self):
        """Error message containing 'connection reset' is temporary."""
        strategy = RetryStrategy()
        error = Exception("Connection reset by peer")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True

    def test_classify_temporarily_unavailable_message(self):
        """Error message containing 'temporarily unavailable' is temporary."""
        strategy = RetryStrategy()
        error = Exception("Server temporarily unavailable")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True

    def test_classify_try_again_message(self):
        """Error message containing 'try again' is temporary."""
        strategy = RetryStrategy()
        error = Exception("Please try again later")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True

    def test_classify_throttling_message(self):
        """Error message containing 'throttl' is temporary."""
        strategy = RetryStrategy()
        error = Exception("Request throttled")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True

    def test_classify_authentication_failed_message(self):
        """Error message containing 'authentication failed' is permanent."""
        strategy = RetryStrategy()
        error = Exception("SMTP authentication failed")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is False

    def test_classify_wrong_version_number_message(self):
        """Error message containing 'wrong_version_number' is permanent."""
        strategy = RetryStrategy()
        error = Exception("SSL: wrong_version_number")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is False

    def test_classify_certificate_verify_failed_message(self):
        """Error message containing 'certificate verify failed' is permanent."""
        strategy = RetryStrategy()
        error = Exception("SSL certificate verify failed")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is False

    def test_classify_ssl_handshake_message(self):
        """Error message containing 'ssl handshake' is permanent."""
        strategy = RetryStrategy()
        error = Exception("SSL handshake error")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is False

    def test_classify_self_signed_certificate_message(self):
        """Error message containing 'self signed certificate' is permanent."""
        strategy = RetryStrategy()
        error = Exception("self signed certificate in chain")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is False

    def test_classify_unknown_error_defaults_to_temporary(self):
        """Unknown errors default to temporary (safer for retry)."""
        strategy = RetryStrategy()
        error = Exception("Some unknown error")
        is_temp, code = strategy.classify_error(error)
        assert is_temp is True
        assert code is None
