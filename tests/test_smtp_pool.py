"""Tests for the SMTP connection pool with acquire/release pattern."""

import asyncio

import pytest

from async_mail_service.smtp_pool import PooledConnection, SMTPPool


class DummySMTP:
    """Mock SMTP client for testing."""

    def __init__(self, hostname, port, start_tls=True, use_tls=False, timeout=None):
        self.hostname = hostname
        self.port = port
        self.start_tls = start_tls
        self.use_tls = use_tls
        self.timeout = timeout
        self.login_credentials = None
        self.connected = False
        self.closed = False
        self.alive = True

    async def connect(self):
        self.connected = True

    async def login(self, user, password):
        self.login_credentials = (user, password)

    async def noop(self):
        if not self.alive:
            raise RuntimeError("Connection dead")
        return 250, b"OK"

    async def quit(self):
        self.closed = True


@pytest.fixture(autouse=True)
def patch_aiosmtplib(monkeypatch):
    """Patch aiosmtplib.SMTP with DummySMTP."""
    created = []

    def factory(**kwargs):
        smtp = DummySMTP(**kwargs)
        created.append(smtp)
        return smtp

    monkeypatch.setattr("async_mail_service.smtp_pool.aiosmtplib.SMTP", factory)
    return created


@pytest.mark.asyncio
async def test_acquire_creates_new_connection(patch_aiosmtplib):
    """Test that acquire creates a new connection when pool is empty."""
    pool = SMTPPool(ttl=30, max_per_account=5)
    smtp = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)

    assert smtp is not None
    assert smtp.connected is True
    assert smtp.login_credentials == ("user", "pass")
    assert len(patch_aiosmtplib) == 1


@pytest.mark.asyncio
async def test_release_returns_connection_to_pool(patch_aiosmtplib):
    """Test that release returns connection to idle pool."""
    pool = SMTPPool(ttl=30, max_per_account=5)
    smtp = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)

    await pool.release(smtp)

    stats = pool.stats()
    assert stats["idle"]["smtp.local:25:user"] == 1
    assert stats["active"]["smtp.local:25:user"] == 0


@pytest.mark.asyncio
async def test_acquire_reuses_released_connection(patch_aiosmtplib):
    """Test that acquire reuses a released connection."""
    pool = SMTPPool(ttl=30, max_per_account=5)

    smtp1 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)
    await pool.release(smtp1)

    smtp2 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)

    # Should reuse the same connection
    assert smtp1 is smtp2
    assert len(patch_aiosmtplib) == 1


@pytest.mark.asyncio
async def test_concurrent_acquire_creates_multiple_connections(patch_aiosmtplib):
    """Test that concurrent acquires create multiple connections."""
    pool = SMTPPool(ttl=30, max_per_account=5)

    # Acquire two connections without releasing
    smtp1 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)
    smtp2 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)

    assert smtp1 is not smtp2
    assert len(patch_aiosmtplib) == 2

    stats = pool.stats()
    assert stats["active"]["smtp.local:25:user"] == 2


@pytest.mark.asyncio
async def test_max_connections_per_account(patch_aiosmtplib):
    """Test that pool respects max_per_account limit."""
    pool = SMTPPool(ttl=30, max_per_account=2)

    # Acquire max connections
    smtp1 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)
    smtp2 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)

    # Third acquire should timeout
    with pytest.raises(asyncio.TimeoutError):
        await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False, timeout=0.1)


@pytest.mark.asyncio
async def test_release_unblocks_waiting_acquire(patch_aiosmtplib):
    """Test that release unblocks a waiting acquire."""
    pool = SMTPPool(ttl=30, max_per_account=1)

    smtp1 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)

    async def delayed_release():
        await asyncio.sleep(0.1)
        await pool.release(smtp1)

    # Start release in background
    asyncio.create_task(delayed_release())

    # This should wait and then succeed
    smtp2 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False, timeout=1.0)
    assert smtp2 is smtp1


@pytest.mark.asyncio
async def test_connection_context_manager(patch_aiosmtplib):
    """Test the connection context manager."""
    pool = SMTPPool(ttl=30, max_per_account=5)

    async with pool.connection("smtp.local", 25, "user", "pass", use_tls=False) as smtp:
        assert smtp.connected is True

    # Connection should be released back to pool
    stats = pool.stats()
    assert stats["idle"]["smtp.local:25:user"] == 1
    assert stats["active"]["smtp.local:25:user"] == 0


@pytest.mark.asyncio
async def test_expired_connection_not_reused(patch_aiosmtplib):
    """Test that expired connections are discarded."""
    pool = SMTPPool(ttl=-1, max_per_account=5)  # TTL=-1 means always expired

    smtp1 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)
    await pool.release(smtp1)

    smtp2 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)

    # Should create new connection since first was expired
    assert smtp2 is not smtp1
    assert smtp1.closed is True
    assert len(patch_aiosmtplib) == 2


@pytest.mark.asyncio
async def test_dead_connection_not_reused(patch_aiosmtplib):
    """Test that dead connections are discarded."""
    pool = SMTPPool(ttl=30, max_per_account=5)

    smtp1 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)
    await pool.release(smtp1)

    # Mark connection as dead
    smtp1.alive = False

    smtp2 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)

    # Should create new connection
    assert smtp2 is not smtp1
    assert len(patch_aiosmtplib) == 2


@pytest.mark.asyncio
async def test_cleanup_removes_expired_connections(patch_aiosmtplib):
    """Test that cleanup removes expired connections."""
    pool = SMTPPool(ttl=-1, max_per_account=5)

    smtp = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)
    await pool.release(smtp)

    await pool.cleanup()

    stats = pool.stats()
    assert "smtp.local:25:user" not in stats["idle"]
    assert smtp.closed is True


@pytest.mark.asyncio
async def test_cleanup_removes_dead_connections(patch_aiosmtplib, monkeypatch):
    """Test that cleanup removes dead connections."""
    pool = SMTPPool(ttl=300, max_per_account=5)

    smtp = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)
    await pool.release(smtp)

    # Mark as dead
    smtp.alive = False

    await pool.cleanup()

    stats = pool.stats()
    assert "smtp.local:25:user" not in stats["idle"]


@pytest.mark.asyncio
async def test_close_all(patch_aiosmtplib):
    """Test that close_all closes all connections."""
    pool = SMTPPool(ttl=30, max_per_account=5)

    smtp1 = await pool.acquire("smtp.local", 25, "user1", "pass", use_tls=False)
    smtp2 = await pool.acquire("smtp.local", 25, "user2", "pass", use_tls=False)
    await pool.release(smtp1)
    await pool.release(smtp2)

    await pool.close_all()

    stats = pool.stats()
    assert stats["idle"] == {}
    assert stats["active"] == {}
    assert smtp1.closed is True
    assert smtp2.closed is True


@pytest.mark.asyncio
async def test_different_accounts_separate_pools(patch_aiosmtplib):
    """Test that different accounts have separate connection pools."""
    pool = SMTPPool(ttl=30, max_per_account=1)

    # Should be able to get connections for different accounts
    smtp1 = await pool.acquire("smtp.local", 25, "user1", "pass", use_tls=False)
    smtp2 = await pool.acquire("smtp.local", 25, "user2", "pass", use_tls=False)

    assert smtp1 is not smtp2
    stats = pool.stats()
    assert stats["active"]["smtp.local:25:user1"] == 1
    assert stats["active"]["smtp.local:25:user2"] == 1


@pytest.mark.asyncio
async def test_tls_settings(patch_aiosmtplib):
    """Test TLS configuration for different ports."""
    pool = SMTPPool(ttl=30, max_per_account=5)

    # Port 465 with TLS = direct TLS
    smtp465 = await pool.acquire("smtp.local", 465, None, None, use_tls=True)
    assert smtp465.use_tls is True
    assert smtp465.start_tls is False

    # Port 587 with TLS = STARTTLS
    smtp587 = await pool.acquire("smtp.local", 587, None, None, use_tls=True)
    assert smtp587.use_tls is False
    assert smtp587.start_tls is True

    # No TLS
    smtp_plain = await pool.acquire("smtp.local", 25, None, None, use_tls=False)
    assert smtp_plain.use_tls is False
    assert smtp_plain.start_tls is False


@pytest.mark.asyncio
async def test_legacy_get_connection(patch_aiosmtplib):
    """Test backward-compatible get_connection method."""
    pool = SMTPPool(ttl=30, max_per_account=5)

    smtp = await pool.get_connection("smtp.local", 25, "user", "pass", use_tls=False)

    assert smtp is not None
    assert smtp.connected is True

    # Should be tracked as active
    stats = pool.stats()
    assert stats["active"]["smtp.local:25:user"] == 1


@pytest.mark.asyncio
async def test_stats(patch_aiosmtplib):
    """Test pool statistics."""
    pool = SMTPPool(ttl=30, max_per_account=5)

    smtp1 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)
    smtp2 = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)
    await pool.release(smtp1)

    stats = pool.stats()
    assert stats["idle"]["smtp.local:25:user"] == 1
    assert stats["active"]["smtp.local:25:user"] == 1
    assert stats["max_per_account"] == 5
    assert stats["ttl"] == 30


@pytest.mark.asyncio
async def test_release_unhealthy_connection_closes_it(patch_aiosmtplib):
    """Test that releasing an unhealthy connection closes it."""
    pool = SMTPPool(ttl=30, max_per_account=5)

    smtp = await pool.acquire("smtp.local", 25, "user", "pass", use_tls=False)

    # Mark as dead before release
    smtp.alive = False

    await pool.release(smtp)

    # Should not be in idle pool
    stats = pool.stats()
    assert stats["idle"].get("smtp.local:25:user", 0) == 0
    assert smtp.closed is True


@pytest.mark.asyncio
async def test_pooled_connection_dataclass():
    """Test PooledConnection helper methods."""
    import time

    smtp = DummySMTP(hostname="test", port=25)
    conn = PooledConnection(smtp=smtp, account_key="test:25:")

    # Age should be very small
    assert conn.age() < 1.0

    # Touch updates last_used
    time.sleep(0.01)
    old_last_used = conn.last_used
    conn.touch()
    assert conn.last_used > old_last_used

    # Idle time should be small after touch
    assert conn.idle_time() < 1.0
