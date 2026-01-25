# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for RetryStrategy class."""

import asyncio

import pytest

from mail_proxy.retry import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAYS, RetryStrategy


class TestRetryStrategyDefaults:
    """Tests for default configuration."""

    def test_default_max_retries(self):
        """Test default max_retries value."""
        strategy = RetryStrategy()
        assert strategy.max_retries == DEFAULT_MAX_RETRIES

    def test_default_delays(self):
        """Test default delays tuple."""
        strategy = RetryStrategy()
        assert strategy.delays == DEFAULT_RETRY_DELAYS


class TestCalculateDelay:
    """Tests for calculate_delay method."""

    def test_first_retry_uses_first_delay(self):
        """Test first retry uses delays[0]."""
        strategy = RetryStrategy(delays=(60, 300, 900))
        assert strategy.calculate_delay(0) == 60

    def test_second_retry_uses_second_delay(self):
        """Test second retry uses delays[1]."""
        strategy = RetryStrategy(delays=(60, 300, 900))
        assert strategy.calculate_delay(1) == 300

    def test_beyond_list_uses_last_delay(self):
        """Test retries beyond list length use last delay."""
        strategy = RetryStrategy(delays=(60, 300, 900))
        assert strategy.calculate_delay(5) == 900
        assert strategy.calculate_delay(100) == 900

    def test_custom_delays(self):
        """Test with custom delay values."""
        strategy = RetryStrategy(delays=(10, 20, 30))
        assert strategy.calculate_delay(0) == 10
        assert strategy.calculate_delay(2) == 30


class TestShouldRetry:
    """Tests for should_retry method."""

    def test_retry_allowed_for_temporary_error(self):
        """Test retry is allowed for temporary errors."""
        strategy = RetryStrategy(max_retries=3)
        exc = asyncio.TimeoutError()
        assert strategy.should_retry(0, exc) is True
        assert strategy.should_retry(2, exc) is True

    def test_no_retry_after_max_retries(self):
        """Test no retry after max retries reached."""
        strategy = RetryStrategy(max_retries=3)
        exc = asyncio.TimeoutError()
        assert strategy.should_retry(3, exc) is False
        assert strategy.should_retry(10, exc) is False

    def test_no_retry_for_permanent_error(self):
        """Test no retry for permanent errors."""
        strategy = RetryStrategy(max_retries=5)
        exc = Exception("authentication failed")
        assert strategy.should_retry(0, exc) is False


class TestClassifyError:
    """Tests for classify_error method."""

    def test_timeout_is_temporary(self):
        """Test timeout errors are classified as temporary."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(asyncio.TimeoutError())
        assert is_temp is True
        assert code is None

    def test_timeout_error_is_temporary(self):
        """Test TimeoutError is temporary."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(TimeoutError("connection timed out"))
        assert is_temp is True

    def test_connection_error_is_temporary(self):
        """Test ConnectionError is temporary."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(ConnectionError("refused"))
        assert is_temp is True

    def test_ssl_error_is_permanent(self):
        """Test SSL errors are classified as permanent."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(Exception("wrong_version_number"))
        assert is_temp is False

    def test_auth_error_is_permanent(self):
        """Test authentication errors are permanent."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(Exception("authentication failed"))
        assert is_temp is False

    def test_smtp_535_is_permanent(self):
        """Test SMTP 535 code is permanent."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(Exception("535 Authentication failed"))
        assert is_temp is False

    def test_temporary_pattern_421(self):
        """Test 421 pattern is temporary."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(Exception("421 Service not available"))
        assert is_temp is True

    def test_temporary_pattern_throttle(self):
        """Test throttle pattern is temporary."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(Exception("Request was throttled"))
        assert is_temp is True

    def test_unknown_error_defaults_to_temporary(self):
        """Test unknown errors default to temporary (safer for retry)."""
        strategy = RetryStrategy()
        is_temp, code = strategy.classify_error(Exception("Some random error"))
        assert is_temp is True
