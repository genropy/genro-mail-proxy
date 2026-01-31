# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for SMTPPool with mocked SMTP connections."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.mail_proxy.smtp.pool import SMTPPool, PooledConnection


class TestPooledConnection:
    """Tests for PooledConnection dataclass."""

    def test_touch_updates_last_used(self):
        """touch() updates last_used timestamp."""
        smtp_mock = MagicMock()
        conn = PooledConnection(smtp=smtp_mock, account_key="test:465:user")
        original_last_used = conn.last_used

        import time
        time.sleep(0.01)
        conn.touch()

        assert conn.last_used > original_last_used

    def test_age_returns_seconds_since_creation(self):
        """age() returns seconds since connection was created."""
        smtp_mock = MagicMock()
        conn = PooledConnection(smtp=smtp_mock, account_key="test:465:user")

        import time
        time.sleep(0.01)

        assert conn.age() >= 0.01

    def test_idle_time_returns_seconds_since_last_use(self):
        """idle_time() returns seconds since last use."""
        smtp_mock = MagicMock()
        conn = PooledConnection(smtp=smtp_mock, account_key="test:465:user")

        import time
        time.sleep(0.01)

        assert conn.idle_time() >= 0.01


class TestSMTPPool:
    """Tests for SMTP connection pool."""

    @pytest.fixture
    def pool(self):
        """Create a fresh pool for each test."""
        return SMTPPool(ttl=300, max_per_account=5)

    # =========================================================================
    # Initialization
    # =========================================================================

    def test_init_defaults(self):
        """Pool initializes with default values."""
        pool = SMTPPool()
        assert pool.ttl == 300
        assert pool.max_per_account == 5

    def test_init_custom_values(self):
        """Pool accepts custom configuration."""
        pool = SMTPPool(ttl=600, max_per_account=10)
        assert pool.ttl == 600
        assert pool.max_per_account == 10

    # =========================================================================
    # Key generation
    # =========================================================================

    def test_make_key_with_user(self, pool):
        """Key includes user when provided."""
        key = pool._make_key("smtp.example.com", 465, "user@example.com")
        assert key == "smtp.example.com:465:user@example.com"

    def test_make_key_without_user(self, pool):
        """Key handles None user."""
        key = pool._make_key("smtp.example.com", 587, None)
        assert key == "smtp.example.com:587:"

    # =========================================================================
    # acquire/release with mocked SMTP
    # =========================================================================

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_acquire_creates_new_connection(self, mock_smtp_class, pool):
        """acquire() creates new connection when pool is empty."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.login = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        smtp = await pool.acquire(
            "smtp.example.com", 465, "user", "pass", use_tls=True
        )

        assert smtp == mock_smtp
        mock_smtp.connect.assert_called_once()
        mock_smtp.login.assert_called_once_with("user", "pass")

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_acquire_without_auth(self, mock_smtp_class, pool):
        """acquire() works without authentication."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        smtp = await pool.acquire(
            "smtp.example.com", 587, None, None, use_tls=True
        )

        assert smtp == mock_smtp
        mock_smtp.connect.assert_called_once()
        mock_smtp.login.assert_not_called()

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_release_returns_to_pool(self, mock_smtp_class, pool):
        """release() returns healthy connection to idle pool."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp_class.return_value = mock_smtp

        smtp = await pool.acquire(
            "smtp.example.com", 465, "user", "pass", use_tls=True
        )
        await pool.release(smtp)

        key = "smtp.example.com:465:user"
        assert len(pool._idle.get(key, [])) == 1

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_release_closes_unhealthy_connection(self, mock_smtp_class, pool):
        """release() closes connection if health check fails."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(500, "Error"))
        mock_smtp.quit = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        smtp = await pool.acquire(
            "smtp.example.com", 465, "user", "pass", use_tls=True
        )
        await pool.release(smtp)

        key = "smtp.example.com:465:user"
        assert len(pool._idle.get(key, [])) == 0
        mock_smtp.quit.assert_called_once()

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_acquire_reuses_idle_connection(self, mock_smtp_class, pool):
        """acquire() reuses healthy connection from idle pool."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp_class.return_value = mock_smtp

        # First acquire and release
        smtp1 = await pool.acquire(
            "smtp.example.com", 465, "user", "pass", use_tls=True
        )
        await pool.release(smtp1)

        # Second acquire should reuse
        smtp2 = await pool.acquire(
            "smtp.example.com", 465, "user", "pass", use_tls=True
        )

        assert smtp2 == smtp1
        # connect should only be called once (for initial creation)
        assert mock_smtp.connect.call_count == 1

    # =========================================================================
    # Connection context manager
    # =========================================================================

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_connection_context_manager(self, mock_smtp_class, pool):
        """connection() context manager acquires and releases."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp_class.return_value = mock_smtp

        async with pool.connection(
            "smtp.example.com", 465, "user", "pass", use_tls=True
        ) as smtp:
            assert smtp == mock_smtp

        # Connection should be back in pool
        key = "smtp.example.com:465:user"
        assert len(pool._idle.get(key, [])) == 1

    # =========================================================================
    # TLS modes
    # =========================================================================

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_port_465_uses_implicit_tls(self, mock_smtp_class, pool):
        """Port 465 with use_tls=True uses implicit TLS."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        await pool.acquire("smtp.example.com", 465, None, None, use_tls=True)

        mock_smtp_class.assert_called_once_with(
            hostname="smtp.example.com",
            port=465,
            start_tls=False,
            use_tls=True,
            timeout=10.0,
        )

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_port_587_uses_starttls(self, mock_smtp_class, pool):
        """Port 587 with use_tls=True uses STARTTLS."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        await pool.acquire("smtp.example.com", 587, None, None, use_tls=True)

        mock_smtp_class.assert_called_once_with(
            hostname="smtp.example.com",
            port=587,
            start_tls=True,
            use_tls=False,
            timeout=10.0,
        )

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_no_tls_mode(self, mock_smtp_class, pool):
        """use_tls=False disables all TLS."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        await pool.acquire("smtp.example.com", 25, None, None, use_tls=False)

        mock_smtp_class.assert_called_once_with(
            hostname="smtp.example.com",
            port=25,
            start_tls=False,
            use_tls=False,
            timeout=10.0,
        )

    # =========================================================================
    # Pool limits
    # =========================================================================

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_max_connections_per_account(self, mock_smtp_class):
        """Pool enforces max connections per account."""
        pool = SMTPPool(ttl=300, max_per_account=2)

        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        # Acquire 2 connections (max)
        smtp1 = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)
        smtp2 = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)

        # Third acquire should timeout
        with pytest.raises(asyncio.TimeoutError):
            await pool.acquire(
                "smtp.example.com", 465, "user", "pass",
                use_tls=True, timeout=0.1
            )

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_release_notifies_waiters(self, mock_smtp_class):
        """release() notifies waiters when connection becomes available."""
        pool = SMTPPool(ttl=300, max_per_account=1)

        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp_class.return_value = mock_smtp

        # Acquire the only slot
        smtp1 = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)

        # Start a waiter
        async def waiter():
            return await pool.acquire(
                "smtp.example.com", 465, "user", "pass",
                use_tls=True, timeout=5.0
            )

        waiter_task = asyncio.create_task(waiter())

        # Give waiter time to start waiting
        await asyncio.sleep(0.01)

        # Release should unblock waiter
        await pool.release(smtp1)
        smtp2 = await waiter_task

        assert smtp2 == smtp1

    # =========================================================================
    # Cleanup
    # =========================================================================

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_cleanup_removes_expired_connections(self, mock_smtp_class):
        """cleanup() removes connections older than TTL."""
        pool = SMTPPool(ttl=0, max_per_account=5)  # 0 TTL = expire immediately

        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp.quit = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        # Acquire and release
        smtp = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)
        await pool.release(smtp)

        key = "smtp.example.com:465:user"
        assert len(pool._idle.get(key, [])) == 1

        # Cleanup should remove expired
        await pool.cleanup()
        assert len(pool._idle.get(key, [])) == 0

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_close_all_clears_pool(self, mock_smtp_class, pool):
        """close_all() closes all idle connections."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp.quit = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        # Acquire and release some connections
        smtp = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)
        await pool.release(smtp)

        await pool.close_all()

        assert len(pool._idle) == 0
        assert len(pool._active_count) == 0
        mock_smtp.quit.assert_called()

    # =========================================================================
    # Stats
    # =========================================================================

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_stats_returns_pool_state(self, mock_smtp_class, pool):
        """stats() returns current pool state."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp_class.return_value = mock_smtp

        # Acquire one, release one
        smtp = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)
        await pool.release(smtp)

        # Acquire another (still active)
        await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)

        stats = pool.stats()

        assert stats["max_per_account"] == 5
        assert stats["ttl"] == 300
        # One in idle, one active
        key = "smtp.example.com:465:user"
        assert stats["idle"].get(key, 0) == 0  # Reused the idle one
        assert stats["active"].get(key, 0) == 1

    # =========================================================================
    # Error handling
    # =========================================================================

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_acquire_connection_error_releases_slot(self, mock_smtp_class, pool):
        """Connection error during acquire releases the slot."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock(side_effect=ConnectionError("Failed"))
        mock_smtp_class.return_value = mock_smtp

        with pytest.raises(ConnectionError):
            await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)

        # Slot should be released
        key = "smtp.example.com:465:user"
        assert pool._active_count.get(key, 0) == 0

    async def test_release_untracked_connection(self, pool):
        """release() handles untracked connections gracefully."""
        mock_smtp = AsyncMock()
        mock_smtp.quit = AsyncMock()

        # Releasing untracked connection should just close it
        await pool.release(mock_smtp)
        mock_smtp.quit.assert_called_once()

    async def test_release_untracked_connection_quit_exception(self, pool):
        """release() handles exception during quit of untracked connection."""
        mock_smtp = AsyncMock()
        mock_smtp.quit = AsyncMock(side_effect=Exception("quit failed"))

        # Should not raise
        await pool.release(mock_smtp)
        mock_smtp.quit.assert_called_once()

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_release_unhealthy_connection_closes_it(self, mock_smtp_class, pool):
        """release() closes unhealthy connection instead of returning to pool."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.login = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp.quit = AsyncMock(side_effect=Exception("quit failed"))
        mock_smtp_class.return_value = mock_smtp

        # Acquire connection
        smtp = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)

        # Make it unhealthy before release
        mock_smtp.noop = AsyncMock(side_effect=Exception("connection lost"))

        # Release should close it (quit exception is caught)
        await pool.release(smtp)

        # Connection should NOT be in idle pool
        key = "smtp.example.com:465:user"
        assert key not in pool._idle or len(pool._idle[key]) == 0

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_acquire_expired_ttl_connection_closes_it(self, mock_smtp_class, pool):
        """acquire() closes expired TTL connection and continues."""
        # Create pool with very short TTL
        pool = SMTPPool(ttl=0.001, max_per_account=5)

        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.login = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp.quit = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        # Acquire and release to put in idle pool
        smtp1 = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)
        # Make it healthy for release
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        await pool.release(smtp1)

        # Wait for TTL to expire
        import time
        time.sleep(0.01)

        # Acquire again - should get new connection because old one expired
        smtp2 = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)
        # quit should have been called on the expired connection
        assert mock_smtp.quit.called

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_acquire_unhealthy_idle_connection_closes_it(self, mock_smtp_class, pool):
        """acquire() closes unhealthy idle connection and continues."""
        mock_smtp1 = AsyncMock()
        mock_smtp1.connect = AsyncMock()
        mock_smtp1.login = AsyncMock()
        mock_smtp1.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp1.quit = AsyncMock()

        mock_smtp2 = AsyncMock()
        mock_smtp2.connect = AsyncMock()
        mock_smtp2.login = AsyncMock()
        mock_smtp2.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp2.quit = AsyncMock()

        # First call returns first mock, second returns second
        mock_smtp_class.side_effect = [mock_smtp1, mock_smtp2]

        # Acquire and release to put in idle pool
        smtp1 = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)
        await pool.release(smtp1)

        # Make first connection unhealthy
        mock_smtp1.noop = AsyncMock(side_effect=Exception("connection lost"))

        # Acquire again - should close unhealthy and get new connection
        smtp2 = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)

        # First connection should have been closed
        mock_smtp1.quit.assert_called()
        # New connection should be second mock
        assert smtp2 is mock_smtp2

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_is_alive_exception_returns_false(self, mock_smtp_class, pool):
        """_is_alive returns False on exception."""
        mock_smtp = AsyncMock()
        mock_smtp.noop = AsyncMock(side_effect=Exception("network error"))

        result = await pool._is_alive(mock_smtp)

        assert result is False

    async def test_close_connection_exception_ignored(self, pool):
        """_close_connection ignores exceptions during quit."""
        mock_smtp = AsyncMock()
        mock_smtp.quit = AsyncMock(side_effect=Exception("already closed"))

        conn = PooledConnection(smtp=mock_smtp, account_key="test")

        # Should not raise
        await pool._close_connection(conn)
        mock_smtp.quit.assert_called_once()

    async def test_get_connection_legacy_api(self, pool):
        """get_connection (deprecated) delegates to acquire."""
        pool.acquire = AsyncMock(return_value="mock_smtp")

        result = await pool.get_connection(
            "smtp.example.com", 465, "user", "pass", use_tls=True
        )

        assert result == "mock_smtp"
        pool.acquire.assert_called_once_with(
            "smtp.example.com", 465, "user", "pass", use_tls=True
        )

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_cleanup_removes_expired_and_cleans_empty_keys(self, mock_smtp_class, pool):
        """cleanup() removes expired connections and cleans up empty keys."""
        pool = SMTPPool(ttl=0.001, max_per_account=5)

        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.login = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp.quit = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        # Acquire and release
        smtp = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)
        await pool.release(smtp)

        # Wait for TTL
        import time
        time.sleep(0.01)

        key = "smtp.example.com:465:user"
        assert key in pool._idle

        # Cleanup should remove expired
        await pool.cleanup()

        # Key should be removed since empty
        assert key not in pool._idle

    @patch("core.mail_proxy.smtp.pool.aiosmtplib.SMTP")
    async def test_cleanup_keeps_valid_connections(self, mock_smtp_class, pool):
        """cleanup() keeps valid connections in idle pools."""
        mock_smtp = AsyncMock()
        mock_smtp.connect = AsyncMock()
        mock_smtp.login = AsyncMock()
        mock_smtp.noop = AsyncMock(return_value=(250, "OK"))
        mock_smtp.quit = AsyncMock()
        mock_smtp_class.return_value = mock_smtp

        # Acquire and release
        smtp = await pool.acquire("smtp.example.com", 465, "user", "pass", use_tls=True)
        await pool.release(smtp)

        key = "smtp.example.com:465:user"
        assert key in pool._idle
        assert len(pool._idle[key]) == 1

        # Cleanup should keep valid connection
        await pool.cleanup()

        assert key in pool._idle
        assert len(pool._idle[key]) == 1
