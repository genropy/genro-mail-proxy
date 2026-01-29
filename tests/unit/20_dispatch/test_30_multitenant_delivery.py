"""Tests for multi-tenant delivery report routing with HTTP mocking."""

import types
from typing import Any

import pytest
from aioresponses import aioresponses
from yarl import URL

from core.mail_proxy.core import MailProxy


class DummyPool:
    """Dummy SMTP pool for testing."""

    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def get_connection(self, host, port, user, password, use_tls):
        return self

    async def send_message(self, message, from_addr=None, **_kwargs):
        self.sent.append({"message": message, "from": from_addr})

    async def cleanup(self):
        pass


class DummyRateLimiter:
    """Dummy rate limiter for testing."""

    async def check_and_plan(self, account):
        return (None, False)

    async def log_send(self, account_id: str):
        pass

    async def release_slot(self, account_id: str):
        pass


class DummyMetrics:
    """Dummy metrics for testing."""

    def set_pending(self, value: int):
        pass

    def inc_sent(self, tenant_id=None, tenant_name=None, account_id=None, account_name=None):
        pass

    def inc_error(self, tenant_id=None, tenant_name=None, account_id=None, account_name=None):
        pass

    def inc_deferred(self, tenant_id=None, tenant_name=None, account_id=None, account_name=None):
        pass

    def inc_rate_limited(self, tenant_id=None, tenant_name=None, account_id=None, account_name=None):
        pass


class DummyAttachments:
    """Dummy attachment manager for testing."""

    async def fetch(self, attachment):
        return (b"content", "file.txt")

    def guess_mime(self, filename):
        return "text", "plain"


async def make_core(tmp_path) -> MailProxy:
    """Create a test core instance with mocked dependencies."""
    db_path = tmp_path / "test.db"
    core = MailProxy(
        db_path=str(db_path),
        start_active=True,
        test_mode=True,
    )
    await core.db.init_db()
    core.pool = DummyPool()
    core.rate_limiter = DummyRateLimiter()
    core.metrics = DummyMetrics()
    core.attachments = DummyAttachments()
    core.logger = types.SimpleNamespace(
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
    )
    return core


@pytest.mark.asyncio
async def test_send_reports_to_tenant_bearer_auth(tmp_path):
    """Test sending reports to tenant with bearer token authentication."""
    core = await make_core(tmp_path)

    tenant = {
        "id": "acme",
        "client_base_url": "https://api.acme.com",
        "client_sync_path": "/sync",
        "client_auth": {"method": "bearer", "token": "secret123"},
    }
    payloads = [{"id": "msg1", "sent_ts": 123456, "account_id": "acc1"}]

    with aioresponses() as m:
        m.post("https://api.acme.com/sync", status=200, payload={"ok": True})

        await core._send_reports_to_tenant(tenant, payloads)

        # Verify the request was made
        assert len(m.requests) == 1
        key = ("POST", URL("https://api.acme.com/sync"))
        assert key in m.requests
        request = m.requests[key][0]
        assert request.kwargs["headers"].get("Authorization") == "Bearer secret123"


@pytest.mark.asyncio
async def test_send_reports_to_tenant_basic_auth(tmp_path):
    """Test sending reports to tenant with basic authentication."""
    core = await make_core(tmp_path)

    tenant = {
        "id": "beta",
        "client_base_url": "https://api.beta.com",
        "client_sync_path": "/sync",
        "client_auth": {"method": "basic", "user": "admin", "password": "pass123"},
    }
    payloads = [{"id": "msg2", "sent_ts": 123456}]

    with aioresponses() as m:
        m.post("https://api.beta.com/sync", status=200, payload={"ok": True})

        await core._send_reports_to_tenant(tenant, payloads)

        assert len(m.requests) == 1
        key = ("POST", URL("https://api.beta.com/sync"))
        assert key in m.requests
        # Verify basic auth was used
        request = m.requests[key][0]
        assert request.kwargs["auth"] is not None
        assert request.kwargs["auth"].login == "admin"


@pytest.mark.asyncio
async def test_send_reports_to_tenant_no_auth(tmp_path):
    """Test sending reports to tenant without authentication."""
    core = await make_core(tmp_path)

    tenant = {
        "id": "gamma",
        "client_base_url": "https://api.gamma.com",
        "client_sync_path": "/sync",
        "client_auth": {"method": "none"},
    }
    payloads = [{"id": "msg3", "sent_ts": 123456}]

    with aioresponses() as m:
        m.post("https://api.gamma.com/sync", status=200, payload={"ok": True})

        await core._send_reports_to_tenant(tenant, payloads)

        assert len(m.requests) == 1
        key = ("POST", URL("https://api.gamma.com/sync"))
        assert key in m.requests
        request = m.requests[key][0]
        # No Authorization header (headers is None or empty)
        headers = request.kwargs.get("headers")
        assert headers is None or "Authorization" not in headers


@pytest.mark.asyncio
async def test_send_reports_to_tenant_http_error(tmp_path):
    """Test handling HTTP errors when sending to tenant."""
    import aiohttp

    core = await make_core(tmp_path)

    tenant = {
        "id": "error-tenant",
        "client_base_url": "https://api.error.com",
        "client_sync_path": "/sync",
        "client_auth": {"method": "none"},
    }
    payloads = [{"id": "msg-err", "sent_ts": 123456}]

    with aioresponses() as m:
        m.post("https://api.error.com/sync", status=500)

        with pytest.raises(aiohttp.ClientResponseError):
            await core._send_reports_to_tenant(tenant, payloads)


@pytest.mark.asyncio
async def test_send_reports_to_tenant_missing_url(tmp_path):
    """Test that missing client_base_url raises RuntimeError."""
    core = await make_core(tmp_path)

    tenant = {
        "id": "no-url-tenant",
        "client_base_url": None,
    }
    payloads = [{"id": "msg", "sent_ts": 123456}]

    with pytest.raises(RuntimeError, match="has no sync URL configured"):
        await core._send_reports_to_tenant(tenant, payloads)


@pytest.mark.asyncio
async def test_process_client_cycle_routes_to_tenants(tmp_path):
    """Test that _process_client_cycle routes reports to correct tenant endpoints."""
    core = await make_core(tmp_path)

    # Create two tenants
    await core.db.table('tenants').add({
        "id": "tenant1",
        "client_base_url": "https://api.tenant1.com",
        "client_sync_path": "/sync",
        "client_auth": {"method": "bearer", "token": "token1"},
        "active": True,
    })
    await core.db.table('tenants').add({
        "id": "tenant2",
        "client_base_url": "https://api.tenant2.com",
        "client_sync_path": "/sync",
        "client_auth": {"method": "bearer", "token": "token2"},
        "active": True,
    })

    # Create accounts for each tenant
    await core.db.add_account({
        "id": "acc1",
        "tenant_id": "tenant1",
        "host": "smtp1.com",
        "port": 587,
    })
    await core.db.add_account({
        "id": "acc2",
        "tenant_id": "tenant2",
        "host": "smtp2.com",
        "port": 587,
    })

    # Insert messages for each tenant
    inserted = await core.db.insert_messages([
        {
            "id": "msg1",
            "tenant_id": "tenant1",
            "account_id": "acc1",
            "priority": 2,
            "payload": {"from": "a@1.com", "to": ["b@1.com"], "subject": "T1"},
        },
        {
            "id": "msg2",
            "tenant_id": "tenant2",
            "account_id": "acc2",
            "priority": 2,
            "payload": {"from": "a@2.com", "to": ["b@2.com"], "subject": "T2"},
        },
    ])
    pk_map = {m["id"]: m["pk"] for m in inserted}

    # Mark messages as sent
    sent_ts = core._utc_now_epoch()
    await core.db.mark_sent(pk_map["msg1"], sent_ts)
    await core.db.mark_sent(pk_map["msg2"], sent_ts)

    with aioresponses() as m:
        # New protocol: response with sent/error/not_found lists
        m.post("https://api.tenant1.com/sync", status=200, payload={"sent": ["msg1"], "error": [], "not_found": []})
        m.post("https://api.tenant2.com/sync", status=200, payload={"sent": ["msg2"], "error": [], "not_found": []})

        await core._process_client_cycle()

        # Verify both tenant endpoints were called
        assert ("POST", URL("https://api.tenant1.com/sync")) in m.requests
        assert ("POST", URL("https://api.tenant2.com/sync")) in m.requests

        # Verify events are marked as reported
        unreported = await core.db.fetch_unreported_events(limit=10)
        assert len(unreported) == 0  # All events should be reported


@pytest.mark.asyncio
async def test_process_client_cycle_fallback_global(tmp_path):
    """Test fallback to global URL when tenant has no sync URL."""
    core = await make_core(tmp_path)
    core._client_sync_url = "https://global.fallback.com/sync"

    # Create tenant without sync URL
    await core.db.table('tenants').add({
        "id": "no-url-tenant",
        "client_base_url": None,
        "active": True,
    })
    await core.db.add_account({
        "id": "acc-no-url",
        "tenant_id": "no-url-tenant",
        "host": "smtp.com",
        "port": 587,
    })

    inserted = await core.db.insert_messages([{
        "id": "msg-fallback",
        "tenant_id": "no-url-tenant",
        "account_id": "acc-no-url",
        "priority": 2,
        "payload": {"from": "a@x.com", "to": ["b@x.com"], "subject": "Fallback"},
    }])

    sent_ts = core._utc_now_epoch()
    await core.db.mark_sent(inserted[0]["pk"], sent_ts)

    with aioresponses() as m:
        m.post("https://global.fallback.com/sync", status=200, payload={"ok": True})

        await core._process_client_cycle()

        # Verify global fallback was used
        assert ("POST", URL("https://global.fallback.com/sync")) in m.requests


@pytest.mark.asyncio
async def test_process_client_cycle_mixed_tenants(tmp_path):
    """Test handling mix of tenant messages and messages without account."""
    core = await make_core(tmp_path)
    core._client_sync_url = "https://global.com/sync"

    # Tenant with sync URL
    await core.db.table('tenants').add({
        "id": "with-url",
        "client_base_url": "https://api.with-url.com",
        "client_sync_path": "/sync",
        "active": True,
    })
    await core.db.add_account({
        "id": "acc-with",
        "tenant_id": "with-url",
        "host": "smtp.com",
        "port": 587,
    })

    # Create a tenant without sync URL for global fallback test
    await core.db.table('tenants').add({
        "id": "no-url-tenant",
        "client_base_url": None,
        "active": True,
    })
    await core.db.add_account({
        "id": "acc-no-url",
        "tenant_id": "no-url-tenant",
        "host": "smtp.com",
        "port": 587,
    })

    inserted = await core.db.insert_messages([
        {
            "id": "msg-with-tenant",
            "tenant_id": "with-url",
            "account_id": "acc-with",
            "priority": 2,
            "payload": {"from": "a@1.com", "to": ["b@1.com"], "subject": "With"},
        },
        {
            # Message with tenant that has no sync URL uses global fallback
            "id": "msg-no-url",
            "tenant_id": "no-url-tenant",
            "account_id": "acc-no-url",
            "priority": 2,
            "payload": {"from": "a@2.com", "to": ["b@2.com"], "subject": "Without"},
        },
    ])
    pk_map = {m["id"]: m["pk"] for m in inserted}

    sent_ts = core._utc_now_epoch()
    await core.db.mark_sent(pk_map["msg-with-tenant"], sent_ts)
    await core.db.mark_sent(pk_map["msg-no-url"], sent_ts)

    with aioresponses() as m:
        m.post("https://api.with-url.com/sync", status=200, payload={"ok": True})
        m.post("https://global.com/sync", status=200, payload={"ok": True})

        await core._process_client_cycle()

        # Tenant-specific URL called for tenant message
        assert ("POST", URL("https://api.with-url.com/sync")) in m.requests
        # Global URL called for message without account
        assert ("POST", URL("https://global.com/sync")) in m.requests


@pytest.mark.asyncio
async def test_process_client_cycle_partial_failure(tmp_path):
    """Test that partial HTTP failures don't affect other tenants."""
    core = await make_core(tmp_path)

    # Two tenants
    await core.db.table('tenants').add({
        "id": "success-tenant",
        "client_base_url": "https://api.success.com",
        "client_sync_path": "/sync",
        "active": True,
    })
    await core.db.table('tenants').add({
        "id": "fail-tenant",
        "client_base_url": "https://api.fail.com",
        "client_sync_path": "/sync",
        "active": True,
    })
    await core.db.add_account({
        "id": "acc-success",
        "tenant_id": "success-tenant",
        "host": "smtp.com",
        "port": 587,
    })
    await core.db.add_account({
        "id": "acc-fail",
        "tenant_id": "fail-tenant",
        "host": "smtp.com",
        "port": 587,
    })

    inserted = await core.db.insert_messages([
        {
            "id": "msg-success",
            "tenant_id": "success-tenant",
            "account_id": "acc-success",
            "priority": 2,
            "payload": {"from": "a@s.com", "to": ["b@s.com"], "subject": "S"},
        },
        {
            "id": "msg-fail",
            "tenant_id": "fail-tenant",
            "account_id": "acc-fail",
            "priority": 2,
            "payload": {"from": "a@f.com", "to": ["b@f.com"], "subject": "F"},
        },
    ])
    pk_map = {m["id"]: m["pk"] for m in inserted}

    sent_ts = core._utc_now_epoch()
    await core.db.mark_sent(pk_map["msg-success"], sent_ts)
    await core.db.mark_sent(pk_map["msg-fail"], sent_ts)

    with aioresponses() as m:
        m.post("https://api.success.com/sync", status=200, payload={"ok": True})
        m.post("https://api.fail.com/sync", status=500)  # This one fails

        await core._process_client_cycle()

        # Success tenant event should be marked as reported
        success_events = await core.db.get_events_for_message(pk_map["msg-success"])
        assert len(success_events) == 1
        assert success_events[0]["reported_ts"] is not None

        # Failed tenant event should NOT be marked (will retry next cycle)
        fail_events = await core.db.get_events_for_message(pk_map["msg-fail"])
        assert len(fail_events) == 1
        assert fail_events[0]["reported_ts"] is None
