"""
End-to-end round-trip tests for the bidirectional PUSH communication flow.

These tests verify the complete message lifecycle:
1. Tenant submits messages via addMessages
2. Proxy dispatches via SMTP
3. Proxy sends delivery reports back to tenant endpoint
4. Messages marked as reported

This validates the architecture described in docs/multi_tenancy.rst
"""

import socket
import types
from typing import Any

import pytest
from aioresponses import aioresponses
from aiosmtpd.controller import Controller
from yarl import URL

from core.mail_proxy.core import MailProxy


def get_free_port() -> int:
    """Find a free port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


class CapturingHandler:
    """SMTP handler that captures received messages."""

    def __init__(self):
        self.messages: list[dict[str, Any]] = []

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        self.messages.append({
            "from": envelope.mail_from,
            "to": envelope.rcpt_tos,
            "data": envelope.content.decode("utf-8", errors="replace"),
        })
        return "250 Message accepted for delivery"


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

    def __init__(self):
        self.sent_count = 0
        self.error_count = 0
        self.deferred_count = 0

    def set_pending(self, value: int):
        pass

    def inc_sent(self, tenant_id=None, tenant_name=None, account_id=None, account_name=None):
        self.sent_count += 1

    def inc_error(self, tenant_id=None, tenant_name=None, account_id=None, account_name=None):
        self.error_count += 1

    def inc_deferred(self, tenant_id=None, tenant_name=None, account_id=None, account_name=None):
        self.deferred_count += 1

    def inc_rate_limited(self, tenant_id=None, tenant_name=None, account_id=None, account_name=None):
        pass


class DummyAttachments:
    """Dummy attachment manager for testing."""

    async def fetch(self, attachment):
        return (b"test content", attachment.get("filename", "file.txt"))

    def guess_mime(self, filename):
        return "application", "octet-stream"


def make_dummy_logger():
    """Create a dummy logger that swallows all messages."""
    return types.SimpleNamespace(
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
    )


async def make_roundtrip_core(tmp_path, smtp_port: int) -> MailProxy:
    """Create a test core instance configured for round-trip testing."""
    db_path = tmp_path / "roundtrip.db"
    core = MailProxy(
        db_path=str(db_path),
        start_active=True,
        test_mode=True,
    )
    await core.db.init_db()
    core.rate_limiter = DummyRateLimiter()
    core.metrics = DummyMetrics()
    core.attachments = DummyAttachments()
    core.logger = make_dummy_logger()
    return core


@pytest.fixture
def smtp_handler():
    """Create a fresh SMTP handler."""
    return CapturingHandler()


@pytest.fixture
def smtp_server(smtp_handler):
    """Start a fake SMTP server on a free port."""
    port = get_free_port()
    controller = Controller(smtp_handler, hostname="127.0.0.1", port=port)
    controller.start()
    yield controller, port
    controller.stop()


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_roundtrip_single_tenant(tmp_path, smtp_server, smtp_handler):
    """
    Test complete round-trip flow for a single tenant:
    1. Create tenant with sync URL
    2. Submit message via addMessages
    3. Process SMTP cycle (message sent)
    4. Process client cycle (delivery report sent to tenant)
    5. Verify message marked as reported
    """
    controller, smtp_port = smtp_server
    core = await make_roundtrip_core(tmp_path, smtp_port)

    # 1. Create tenant with sync endpoint
    await core.db.add_tenant({
        "id": "acme",
        "name": "ACME Corporation",
        "client_base_url": "https://api.acme.com",
        "client_sync_path": "/proxy_sync",
        "client_auth": {"method": "bearer", "token": "acme-secret-token"},
        "active": True,
    })

    # 2. Create SMTP account for tenant
    await core.handle_command("addAccount", {
        "id": "smtp-acme",
        "tenant_id": "acme",
        "host": "127.0.0.1",
        "port": smtp_port,
        "use_tls": False,
    })

    # 3. Submit message
    result = await core.handle_command("addMessages", {
        "messages": [{
            "id": "acme-msg-001",
            "tenant_id": "acme",
            "account_id": "smtp-acme",
            "from": "noreply@acme.com",
            "to": ["customer@example.com"],
            "subject": "Welcome to ACME",
            "body": "Thank you for choosing ACME!",
        }]
    })
    assert result["ok"] is True
    assert result["queued"] == 1

    # 4. Process SMTP cycle - message should be sent
    await core._process_smtp_cycle()

    # Verify: SMTP server received the message
    assert len(smtp_handler.messages) == 1
    msg = smtp_handler.messages[0]
    assert msg["from"] == "noreply@acme.com"
    assert "customer@example.com" in msg["to"]
    assert "Welcome to ACME" in msg["data"]

    # Verify: Message marked as processed (smtp_ts set), event not yet reported
    messages = await core.db.list_messages("acme")
    assert len(messages) == 1
    assert messages[0]["smtp_ts"] is not None
    pk = messages[0]["pk"]
    # Event should exist but not be reported yet
    events = await core.db.get_events_for_message(pk)
    assert len(events) == 1
    assert events[0]["reported_ts"] is None

    # 5. Process client cycle - delivery report should be sent to tenant
    with aioresponses() as m:
        m.post(
            "https://api.acme.com/proxy_sync",
            status=200,
            payload={"sent": 1, "error": 0, "deferred": 0}
        )

        await core._process_client_cycle()

        # Verify: Request was sent to tenant's endpoint
        assert ("POST", URL("https://api.acme.com/proxy_sync")) in m.requests
        request = m.requests[("POST", URL("https://api.acme.com/proxy_sync"))][0]

        # Verify: Bearer auth was used
        assert request.kwargs["headers"].get("Authorization") == "Bearer acme-secret-token"

        # Verify: Payload contains delivery report
        # Note: aioresponses captures the json parameter
        assert "json" in request.kwargs
        payload = request.kwargs["json"]
        assert "delivery_report" in payload
        assert len(payload["delivery_report"]) == 1
        report = payload["delivery_report"][0]
        assert report["id"] == "acme-msg-001"
        assert report["sent_ts"] is not None

    # 6. Verify: Event is now marked as reported
    events = await core.db.get_events_for_message(pk)
    assert len(events) == 1
    assert events[0]["reported_ts"] is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_full_roundtrip_multi_tenant(tmp_path, smtp_server, smtp_handler):
    """
    Test round-trip with multiple tenants - each receives their own reports.
    """
    controller, smtp_port = smtp_server
    core = await make_roundtrip_core(tmp_path, smtp_port)

    # Create two tenants
    await core.db.add_tenant({
        "id": "tenant-alpha",
        "client_base_url": "https://api.alpha.com",
        "client_sync_path": "/proxy_sync",
        "client_auth": {"method": "bearer", "token": "alpha-token"},
        "active": True,
    })
    await core.db.add_tenant({
        "id": "tenant-beta",
        "client_base_url": "https://api.beta.com",
        "client_sync_path": "/proxy_sync",
        "client_auth": {"method": "basic", "user": "beta", "password": "pass"},
        "active": True,
    })

    # Create SMTP accounts for each tenant
    await core.handle_command("addAccount", {
        "id": "smtp-alpha",
        "tenant_id": "tenant-alpha",
        "host": "127.0.0.1",
        "port": smtp_port,
        "use_tls": False,
    })
    await core.handle_command("addAccount", {
        "id": "smtp-beta",
        "tenant_id": "tenant-beta",
        "host": "127.0.0.1",
        "port": smtp_port,
        "use_tls": False,
    })

    # Submit messages for both tenants
    await core.handle_command("addMessages", {
        "messages": [
            {
                "id": "alpha-msg-001",
                "tenant_id": "tenant-alpha",
                "account_id": "smtp-alpha",
                "from": "noreply@alpha.com",
                "to": ["user1@example.com"],
                "subject": "Alpha message",
                "body": "From Alpha",
            },
            {
                "id": "beta-msg-001",
                "tenant_id": "tenant-beta",
                "account_id": "smtp-beta",
                "from": "noreply@beta.com",
                "to": ["user2@example.com"],
                "subject": "Beta message",
                "body": "From Beta",
            },
        ]
    })

    # Process SMTP cycle
    await core._process_smtp_cycle()

    # Both messages should be sent
    assert len(smtp_handler.messages) == 2
    assert core.metrics.sent_count == 2

    # Process client cycle with mocked tenant endpoints
    with aioresponses() as m:
        m.post("https://api.alpha.com/proxy_sync", status=200, payload={"sent": 1})
        m.post("https://api.beta.com/proxy_sync", status=200, payload={"sent": 1})

        await core._process_client_cycle()

        # Both tenant endpoints should be called
        assert ("POST", URL("https://api.alpha.com/proxy_sync")) in m.requests
        assert ("POST", URL("https://api.beta.com/proxy_sync")) in m.requests

        # Verify Alpha used bearer auth
        alpha_req = m.requests[("POST", URL("https://api.alpha.com/proxy_sync"))][0]
        assert alpha_req.kwargs["headers"].get("Authorization") == "Bearer alpha-token"

        # Verify Beta used basic auth
        beta_req = m.requests[("POST", URL("https://api.beta.com/proxy_sync"))][0]
        assert beta_req.kwargs["auth"] is not None
        assert beta_req.kwargs["auth"].login == "beta"

    # All events should be marked as reported
    unreported = await core.db.fetch_unreported_events(limit=10)
    assert len(unreported) == 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_roundtrip_with_smtp_error(tmp_path, smtp_server, smtp_handler):
    """
    Test round-trip when SMTP delivery fails.
    Error should be reported to tenant.
    """
    controller, smtp_port = smtp_server
    core = await make_roundtrip_core(tmp_path, smtp_port)

    # Configure handler to reject next message
    smtp_handler.reject_next = True
    smtp_handler.reject_code = 550
    smtp_handler.reject_message = "User not found"

    # Monkey-patch handler to support reject_next
    original_handle_data = smtp_handler.handle_DATA

    async def handle_data_with_reject(server, session, envelope):
        if smtp_handler.reject_next:
            smtp_handler.reject_next = False
            return f"{smtp_handler.reject_code} {smtp_handler.reject_message}"
        return await original_handle_data(server, session, envelope)

    smtp_handler.handle_DATA = handle_data_with_reject

    await core.db.add_tenant({
        "id": "error-tenant",
        "client_base_url": "https://api.error.com",
        "client_sync_path": "/proxy_sync",
        "client_auth": {"method": "none"},
        "active": True,
    })

    await core.handle_command("addAccount", {
        "id": "smtp-error",
        "tenant_id": "error-tenant",
        "host": "127.0.0.1",
        "port": smtp_port,
        "use_tls": False,
    })

    await core.handle_command("addMessages", {
        "messages": [{
            "id": "error-msg-001",
            "tenant_id": "error-tenant",
            "account_id": "smtp-error",
            "from": "sender@error.com",
            "to": ["invalid@example.com"],
            "subject": "This will fail",
            "body": "Should not be delivered",
        }]
    })

    # Process SMTP cycle - should fail
    smtp_handler.reject_next = True
    await core._process_smtp_cycle()

    # Message should be marked as processed (permanent error)
    messages = await core.db.list_messages("error-tenant")
    assert len(messages) == 1
    assert messages[0]["smtp_ts"] is not None

    # Error event should be recorded
    pk = messages[0]["pk"]
    events = await core.db.get_events_for_message(pk)
    error_events = [e for e in events if e["event_type"] == "error"]
    assert len(error_events) == 1
    assert "550" in error_events[0]["description"] or "User not found" in error_events[0]["description"]

    # Process client cycle - error report should be sent
    with aioresponses() as m:
        m.post("https://api.error.com/proxy_sync", status=200, payload={"error": 1})

        await core._process_client_cycle()

        assert ("POST", URL("https://api.error.com/proxy_sync")) in m.requests
        request = m.requests[("POST", URL("https://api.error.com/proxy_sync"))][0]

        # Verify error is in the report
        payload = request.kwargs["json"]
        report = payload["delivery_report"][0]
        assert report["error_ts"] is not None
        assert report["error"] is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_roundtrip_tenant_sync_failure_retry(tmp_path, smtp_server, smtp_handler):
    """
    Test that when tenant sync fails, message remains unreported for retry.
    """
    controller, smtp_port = smtp_server
    core = await make_roundtrip_core(tmp_path, smtp_port)

    await core.db.add_tenant({
        "id": "flaky-tenant",
        "client_base_url": "https://api.flaky.com",
        "client_sync_path": "/proxy_sync",
        "client_auth": {"method": "none"},
        "active": True,
    })

    await core.handle_command("addAccount", {
        "id": "smtp-flaky",
        "tenant_id": "flaky-tenant",
        "host": "127.0.0.1",
        "port": smtp_port,
        "use_tls": False,
    })

    await core.handle_command("addMessages", {
        "messages": [{
            "id": "flaky-msg-001",
            "tenant_id": "flaky-tenant",
            "account_id": "smtp-flaky",
            "from": "sender@flaky.com",
            "to": ["recipient@example.com"],
            "subject": "Flaky test",
            "body": "Testing retry",
        }]
    })

    # Process SMTP - success
    await core._process_smtp_cycle()
    assert len(smtp_handler.messages) == 1

    # First client cycle - tenant returns error
    with aioresponses() as m:
        m.post("https://api.flaky.com/proxy_sync", status=500)

        await core._process_client_cycle()

    # Event should NOT be marked as reported (will retry)
    messages = await core.db.list_messages("flaky-tenant")
    pk = messages[0]["pk"]
    events = await core.db.get_events_for_message(pk)
    assert len(events) == 1
    assert events[0]["reported_ts"] is None  # Not reported due to error

    # Second client cycle - tenant recovers
    with aioresponses() as m:
        m.post("https://api.flaky.com/proxy_sync", status=200, payload={"sent": 1})

        await core._process_client_cycle()

    # Now event should be marked as reported
    events = await core.db.get_events_for_message(pk)
    assert events[0]["reported_ts"] is not None


@pytest.mark.asyncio
@pytest.mark.integration
async def test_roundtrip_fallback_to_global_url(tmp_path, smtp_server, smtp_handler):
    """
    Test fallback to global sync URL when tenant has no sync URL configured.
    """
    controller, smtp_port = smtp_server
    core = await make_roundtrip_core(tmp_path, smtp_port)

    # Set global sync URL
    core._client_sync_url = "https://global.fallback.com/sync"

    # Create tenant without sync URL
    await core.db.add_tenant({
        "id": "no-url-tenant",
        "client_base_url": None,
        "active": True,
    })

    await core.handle_command("addAccount", {
        "id": "smtp-no-url",
        "tenant_id": "no-url-tenant",
        "host": "127.0.0.1",
        "port": smtp_port,
        "use_tls": False,
    })

    await core.handle_command("addMessages", {
        "messages": [{
            "id": "no-url-msg-001",
            "tenant_id": "no-url-tenant",
            "account_id": "smtp-no-url",
            "from": "sender@nourl.com",
            "to": ["recipient@example.com"],
            "subject": "Fallback test",
            "body": "Should use global URL",
        }]
    })

    await core._process_smtp_cycle()

    with aioresponses() as m:
        m.post("https://global.fallback.com/sync", status=200, payload={"sent": 1})

        await core._process_client_cycle()

        # Global URL should be used
        assert ("POST", URL("https://global.fallback.com/sync")) in m.requests


@pytest.mark.asyncio
@pytest.mark.integration
async def test_roundtrip_batch_messages(tmp_path, smtp_server, smtp_handler):
    """
    Test round-trip with multiple messages in a batch.
    """
    controller, smtp_port = smtp_server
    core = await make_roundtrip_core(tmp_path, smtp_port)

    await core.db.add_tenant({
        "id": "batch-tenant",
        "client_base_url": "https://api.batch.com",
        "client_sync_path": "/proxy_sync",
        "client_auth": {"method": "bearer", "token": "batch-token"},
        "active": True,
    })

    await core.handle_command("addAccount", {
        "id": "smtp-batch",
        "tenant_id": "batch-tenant",
        "host": "127.0.0.1",
        "port": smtp_port,
        "use_tls": False,
    })

    # Submit batch of messages
    messages_to_send = [
        {
            "id": f"batch-msg-{i:03d}",
            "tenant_id": "batch-tenant",
            "account_id": "smtp-batch",
            "from": "batch@example.com",
            "to": [f"user{i}@example.com"],
            "subject": f"Batch message {i}",
            "body": f"This is message number {i}",
        }
        for i in range(5)
    ]

    result = await core.handle_command("addMessages", {"messages": messages_to_send})
    assert result["queued"] == 5

    # Process all
    await core._process_smtp_cycle()
    assert len(smtp_handler.messages) == 5

    with aioresponses() as m:
        m.post("https://api.batch.com/proxy_sync", status=200, payload={"sent": 5})

        await core._process_client_cycle()

        request = m.requests[("POST", URL("https://api.batch.com/proxy_sync"))][0]
        payload = request.kwargs["json"]

        # All 5 messages should be in the delivery report
        assert len(payload["delivery_report"]) == 5

    # All events should be marked as reported
    unreported = await core.db.fetch_unreported_events(limit=10)
    assert len(unreported) == 0
