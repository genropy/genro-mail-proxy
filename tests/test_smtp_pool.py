import pytest

from async_mail_service.smtp_pool import SMTPPool


class DummySMTP:
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
    created = []

    def factory(**kwargs):
        smtp = DummySMTP(**kwargs)
        created.append(smtp)
        return smtp

    monkeypatch.setattr("async_mail_service.smtp_pool.aiosmtplib.SMTP", factory)
    return created


@pytest.mark.asyncio
async def test_get_connection_reuses_active_instance(patch_aiosmtplib):
    pool = SMTPPool(ttl=30)
    smtp1 = await pool.get_connection("smtp.local", 25, "user", "pass", use_tls=False)
    smtp2 = await pool.get_connection("smtp.local", 25, "user", "pass", use_tls=False)

    assert smtp1 is smtp2
    assert smtp1.login_credentials == ("user", "pass")


@pytest.mark.asyncio
async def test_get_connection_discards_expired_instance(patch_aiosmtplib):
    pool = SMTPPool(ttl=-1)
    smtp1 = await pool.get_connection("smtp.local", 25, None, None, use_tls=False)
    smtp1.alive = True

    smtp2 = await pool.get_connection("smtp.local", 25, None, None, use_tls=False)
    assert smtp1.closed is True
    assert smtp2 is not smtp1


@pytest.mark.asyncio
async def test_cleanup_removes_dead_connections(monkeypatch, patch_aiosmtplib):
    pool = SMTPPool(ttl=1)
    smtp = await pool.get_connection("smtp.local", 25, None, None, use_tls=False)

    async def fake_is_alive(_smtp):
        return False

    monkeypatch.setattr(pool, "_is_alive", fake_is_alive)

    await pool.cleanup()
    assert smtp.closed is True
    assert pool.pool == {}


@pytest.mark.asyncio
async def test_get_connection_respects_use_tls(patch_aiosmtplib):
    pool = SMTPPool(ttl=30)
    smtp = await pool.get_connection("smtp.secure", 465, None, None, use_tls=True)
    assert smtp.use_tls is True
    assert smtp.start_tls is False
