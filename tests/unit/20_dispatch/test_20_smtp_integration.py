"""Tests with real SMTP server using aiosmtpd."""

import asyncio
import socket
import types
from typing import Any

import pytest
from aiosmtpd.controller import Controller

from mail_proxy.core import MailProxy


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
        self.reject_next = False
        self.reject_code = 550
        self.reject_message = "Mailbox not found"
        self.delay_seconds = 0

    async def handle_RCPT(self, server, session, envelope, address, rcpt_options):
        """Handle RCPT TO command."""
        envelope.rcpt_tos.append(address)
        return "250 OK"

    async def handle_DATA(self, server, session, envelope):
        """Handle DATA command - capture the message."""
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

        if self.reject_next:
            self.reject_next = False
            return f"{self.reject_code} {self.reject_message}"

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

    def inc_sent(self, account_id: str):
        self.sent_count += 1

    def inc_error(self, account_id: str):
        self.error_count += 1

    def inc_deferred(self, account_id: str):
        self.deferred_count += 1

    def inc_rate_limited(self, account_id: str):
        pass


class DummyAttachments:
    """Dummy attachment manager for testing."""

    async def fetch(self, attachment):
        return (b"test content", attachment.get("filename", "file.txt"))

    def guess_mime(self, filename):
        return "application", "octet-stream"


class DummyReporter:
    """Dummy delivery reporter for testing."""

    def __init__(self):
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]):
        self.payloads.append(payload)


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


async def make_core_with_smtp(tmp_path, smtp_port) -> MailProxy:
    """Create a test core instance connected to the fake SMTP server."""
    db_path = tmp_path / "smtp_test.db"
    reporter = DummyReporter()
    core = MailProxy(
        db_path=str(db_path),
        start_active=True,
        report_delivery_callable=reporter,
        test_mode=True,
    )
    await core.db.init_db()
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

    # Create tenant first - accounts require tenant_id
    await core.handle_command("addTenant", {"id": "test-tenant", "name": "Test"})

    # Add account pointing to fake SMTP server
    await core.handle_command("addAccount", {
        "id": "test-smtp",
        "tenant_id": "test-tenant",
        "host": "127.0.0.1",
        "port": smtp_port,
        "use_tls": False,
    })

    return core


@pytest.mark.asyncio
async def test_send_email_via_real_smtp(tmp_path, smtp_server, smtp_handler):
    """Test sending an email through a real SMTP server."""
    controller, port = smtp_server
    core = await make_core_with_smtp(tmp_path, port)

    await core.handle_command("addMessages", {
        "messages": [{
            "id": "msg1",
            "tenant_id": "test-tenant",
            "account_id": "test-smtp",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Test Subject",
            "body": "Hello, this is a test email.",
        }]
    })

    await core._process_smtp_cycle()

    # Verify the message was received by the SMTP server
    assert len(smtp_handler.messages) == 1
    msg = smtp_handler.messages[0]
    assert msg["from"] == "sender@test.com"
    assert "recipient@test.com" in msg["to"]
    assert "Test Subject" in msg["data"]
    assert "Hello, this is a test email." in msg["data"]

    # Verify message is marked as processed (smtp_ts set)
    messages = await core.db.list_messages()
    assert messages[0]["smtp_ts"] is not None
    assert core.metrics.sent_count == 1


@pytest.mark.asyncio
async def test_send_email_with_multiple_recipients(tmp_path, smtp_server, smtp_handler):
    """Test sending to multiple recipients."""
    controller, port = smtp_server
    core = await make_core_with_smtp(tmp_path, port)

    await core.handle_command("addMessages", {
        "messages": [{
            "id": "msg-multi",
            "tenant_id": "test-tenant",
            "account_id": "test-smtp",
            "from": "sender@test.com",
            "to": ["alice@test.com", "bob@test.com"],
            "cc": ["charlie@test.com"],
            "subject": "Multi-recipient test",
            "body": "Hello everyone!",
        }]
    })

    await core._process_smtp_cycle()

    assert len(smtp_handler.messages) == 1
    msg = smtp_handler.messages[0]
    assert "alice@test.com" in msg["to"]
    assert "bob@test.com" in msg["to"]
    assert "charlie@test.com" in msg["to"]


@pytest.mark.asyncio
async def test_send_html_email(tmp_path, smtp_server, smtp_handler):
    """Test sending HTML email."""
    controller, port = smtp_server
    core = await make_core_with_smtp(tmp_path, port)

    await core.handle_command("addMessages", {
        "messages": [{
            "id": "msg-html",
            "tenant_id": "test-tenant",
            "account_id": "test-smtp",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "HTML Test",
            "body": "<html><body><h1>Hello</h1></body></html>",
            "content_type": "html",
        }]
    })

    await core._process_smtp_cycle()

    assert len(smtp_handler.messages) == 1
    msg = smtp_handler.messages[0]
    assert "<h1>Hello</h1>" in msg["data"]


@pytest.mark.asyncio
async def test_smtp_rejection_marks_error(tmp_path, smtp_server, smtp_handler):
    """Test that SMTP rejection marks message as error."""
    controller, port = smtp_server
    core = await make_core_with_smtp(tmp_path, port)

    # Configure handler to reject next message
    smtp_handler.reject_next = True
    smtp_handler.reject_code = 550
    smtp_handler.reject_message = "User unknown"

    await core.handle_command("addMessages", {
        "messages": [{
            "id": "msg-reject",
            "tenant_id": "test-tenant",
            "account_id": "test-smtp",
            "from": "sender@test.com",
            "to": ["nonexistent@test.com"],
            "subject": "Will be rejected",
            "body": "This should fail",
        }]
    })

    await core._process_smtp_cycle()

    # Message should be marked as processed (permanent error)
    messages = await core.db.list_messages()
    assert messages[0]["smtp_ts"] is not None  # Processed
    assert messages[0]["deferred_ts"] is None  # Not retrying

    # Verify error event was recorded
    events = await core.db.get_events_for_message("msg-reject")
    error_events = [e for e in events if e["event_type"] == "error"]
    assert len(error_events) == 1
    assert "550" in error_events[0]["description"] or "User unknown" in error_events[0]["description"]
    assert core.metrics.error_count == 1


@pytest.mark.asyncio
async def test_smtp_temporary_error_defers(tmp_path, smtp_server, smtp_handler):
    """Test that temporary SMTP errors defer the message."""
    controller, port = smtp_server
    core = await make_core_with_smtp(tmp_path, port)

    # Configure handler to return temporary error
    smtp_handler.reject_next = True
    smtp_handler.reject_code = 451
    smtp_handler.reject_message = "Try again later"

    await core.handle_command("addMessages", {
        "messages": [{
            "id": "msg-temp-err",
            "tenant_id": "test-tenant",
            "account_id": "test-smtp",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Temporary failure",
            "body": "Should be retried",
        }]
    })

    await core._process_smtp_cycle()

    # Message should be deferred, not permanently failed
    messages = await core.db.list_messages()
    assert messages[0]["smtp_ts"] is None  # Not processed (back in pending state)
    assert messages[0]["deferred_ts"] is not None  # Deferred for retry


@pytest.mark.asyncio
async def test_send_multiple_messages_batch(tmp_path, smtp_server, smtp_handler):
    """Test sending multiple messages in a batch."""
    controller, port = smtp_server
    core = await make_core_with_smtp(tmp_path, port)

    messages = []
    for i in range(5):
        messages.append({
            "id": f"batch-msg-{i}",
            "tenant_id": "test-tenant",
            "account_id": "test-smtp",
            "from": "sender@test.com",
            "to": [f"recipient{i}@test.com"],
            "subject": f"Batch message {i}",
            "body": f"Content {i}",
        })

    await core.handle_command("addMessages", {"messages": messages})
    await core._process_smtp_cycle()

    # All messages should be sent
    assert len(smtp_handler.messages) == 5
    assert core.metrics.sent_count == 5

    # All messages should be marked as processed (sent)
    db_messages = await core.db.list_messages()
    assert all(m["smtp_ts"] is not None for m in db_messages)


@pytest.mark.asyncio
async def test_multitenant_smtp_dispatch(tmp_path, smtp_handler):
    """Test sending emails for multiple tenants through different accounts."""
    # Create two SMTP servers with pre-assigned ports
    handler1 = CapturingHandler()
    handler2 = CapturingHandler()
    port1 = get_free_port()
    port2 = get_free_port()
    controller1 = Controller(handler1, hostname="127.0.0.1", port=port1)
    controller2 = Controller(handler2, hostname="127.0.0.1", port=port2)
    controller1.start()
    controller2.start()

    try:

        db_path = tmp_path / "multitenant_smtp.db"
        reporter = DummyReporter()
        core = MailProxy(
            db_path=str(db_path),
            start_active=True,
            report_delivery_callable=reporter,
            test_mode=True,
        )
        await core.db.init_db()
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

        # Create two tenants with different SMTP accounts
        await core.db.add_tenant({
            "id": "tenant1",
            "client_base_url": "https://tenant1.com",
            "client_sync_path": "/sync",
            "active": True,
        })
        await core.db.add_tenant({
            "id": "tenant2",
            "client_base_url": "https://tenant2.com",
            "client_sync_path": "/sync",
            "active": True,
        })

        await core.handle_command("addAccount", {
            "id": "tenant1-smtp",
            "tenant_id": "tenant1",
            "host": "127.0.0.1",
            "port": port1,
            "use_tls": False,
        })
        await core.handle_command("addAccount", {
            "id": "tenant2-smtp",
            "tenant_id": "tenant2",
            "host": "127.0.0.1",
            "port": port2,
            "use_tls": False,
        })

        # Send messages for each tenant
        await core.handle_command("addMessages", {
            "messages": [
                {
                    "id": "tenant1-msg",
                    "tenant_id": "tenant1",
                    "account_id": "tenant1-smtp",
                    "from": "sender@tenant1.com",
                    "to": ["user@example.com"],
                    "subject": "From Tenant 1",
                    "body": "Hello from tenant 1",
                },
                {
                    "id": "tenant2-msg",
                    "tenant_id": "tenant2",
                    "account_id": "tenant2-smtp",
                    "from": "sender@tenant2.com",
                    "to": ["user@example.com"],
                    "subject": "From Tenant 2",
                    "body": "Hello from tenant 2",
                },
            ]
        })

        await core._process_smtp_cycle()

        # Verify tenant isolation - each SMTP server receives only its tenant's mail
        assert len(handler1.messages) == 1
        assert handler1.messages[0]["from"] == "sender@tenant1.com"
        assert "From Tenant 1" in handler1.messages[0]["data"]

        assert len(handler2.messages) == 1
        assert handler2.messages[0]["from"] == "sender@tenant2.com"
        assert "From Tenant 2" in handler2.messages[0]["data"]

    finally:
        controller1.stop()
        controller2.stop()


@pytest.mark.asyncio
async def test_smtp_connection_reuse(tmp_path, smtp_server, smtp_handler):
    """Test that SMTP connections are reused across multiple sends."""
    controller, port = smtp_server
    core = await make_core_with_smtp(tmp_path, port)

    # Send multiple messages
    for i in range(3):
        await core.handle_command("addMessages", {
            "messages": [{
                "id": f"reuse-msg-{i}",
                "tenant_id": "test-tenant",
                "account_id": "test-smtp",
                "from": "sender@test.com",
                "to": ["recipient@test.com"],
                "subject": f"Message {i}",
                "body": f"Body {i}",
            }]
        })
        await core._process_smtp_cycle()

    # All messages should be sent
    assert len(smtp_handler.messages) == 3

    # Connection pool should have reused connections
    # (We can't easily verify this without more introspection, but the test
    # ensures the pool works correctly for multiple sends)


@pytest.mark.asyncio
async def test_smtp_with_custom_headers(tmp_path, smtp_server, smtp_handler):
    """Test sending email with custom headers."""
    controller, port = smtp_server
    core = await make_core_with_smtp(tmp_path, port)

    await core.handle_command("addMessages", {
        "messages": [{
            "id": "msg-headers",
            "tenant_id": "test-tenant",
            "account_id": "test-smtp",
            "from": "sender@test.com",
            "to": ["recipient@test.com"],
            "subject": "Custom Headers Test",
            "body": "Test body",
            "reply_to": "reply@test.com",
            "headers": {
                "X-Custom-Header": "custom-value",
                "X-Priority": "1",
            },
        }]
    })

    await core._process_smtp_cycle()

    assert len(smtp_handler.messages) == 1
    data = smtp_handler.messages[0]["data"]
    assert "Reply-To: reply@test.com" in data
    assert "X-Custom-Header: custom-value" in data
    assert "X-Priority: 1" in data
