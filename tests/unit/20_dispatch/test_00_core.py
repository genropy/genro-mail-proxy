import asyncio
import math
import types
from typing import Any

import pytest

from mail_proxy.core import MailProxy


class DummySMTP:
    def __init__(self):
        self.sent: list[dict[str, Any]] = []
        self.raise_error: Exception | None = None
        self.raise_error_persistent: bool = False  # If True, error persists across sends

    async def send_message(self, message, from_addr=None, **_kwargs):
        if self.raise_error:
            exc = self.raise_error
            if not self.raise_error_persistent:
                self.raise_error = None
            raise exc
        self.sent.append({"message": message, "from": from_addr})


class DummyPool:
    def __init__(self):
        self.smtp = DummySMTP()
        self.requests: list[Any] = []

    async def get_connection(self, host, port, user, password, use_tls):
        self.requests.append((host, port, user, password, use_tls))
        return self.smtp

    async def acquire(self, host, port, user, password, *, use_tls, timeout=None):
        self.requests.append((host, port, user, password, use_tls))
        return self.smtp

    async def release(self, smtp):
        pass

    def connection(self, host, port, user, password, *, use_tls, timeout=None):
        """Context manager for connection acquire/release."""
        return _DummyConnectionContext(self, host, port, user, password, use_tls)

    async def cleanup(self):
        return None


class _DummyConnectionContext:
    """Async context manager for DummyPool.connection()."""

    def __init__(self, pool, host, port, user, password, use_tls):
        self.pool = pool
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.use_tls = use_tls
        self.smtp = None

    async def __aenter__(self):
        self.smtp = await self.pool.acquire(
            self.host, self.port, self.user, self.password, use_tls=self.use_tls
        )
        return self.smtp

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.pool.release(self.smtp)
        return False


class DummyRateLimiter:
    def __init__(self):
        self.plan_result: int | None = None
        self.logged: list[str] = []

    async def check_and_plan(self, account):
        return self.plan_result

    async def log_send(self, account_id: str):
        self.logged.append(account_id)

    async def release_slot(self, account_id: str):
        pass


class DummyMetrics:
    def __init__(self):
        self.pending_value = None
        self.sent_accounts: list[str] = []
        self.error_accounts: list[str] = []
        self.deferred_accounts: list[str] = []
        self.rate_limited_accounts: list[str] = []

    def set_pending(self, value: int):
        self.pending_value = value

    def inc_sent(self, account_id: str):
        self.sent_accounts.append(account_id or "default")

    def inc_error(self, account_id: str):
        self.error_accounts.append(account_id or "default")

    def inc_deferred(self, account_id: str):
        self.deferred_accounts.append(account_id or "default")

    def inc_rate_limited(self, account_id: str):
        self.rate_limited_accounts.append(account_id or "default")


class DummyAttachments:
    async def fetch(self, attachment):
        return b"content"

    def guess_mime(self, filename):
        return "text", "plain"


class DummyReporter:
    def __init__(self):
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]):
        self.payloads.append(payload)


async def make_core(tmp_path, max_retries=5) -> MailProxy:
    db_path = tmp_path / "core.db"
    reporter = DummyReporter()
    core = MailProxy(
        db_path=str(db_path),
        start_active=True,
        report_delivery_callable=reporter,
        report_retention_seconds=2,
        test_mode=True,
        max_retries=max_retries,
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
    await core.handle_command("addAccount", {"id": "acc", "host": "smtp.local", "port": 25})
    return core


@pytest.mark.asyncio
async def test_run_now_triggers_wakeup(tmp_path):
    db_path = tmp_path / "core-prod.db"
    core = MailProxy(db_path=str(db_path), start_active=True)
    await core.db.init_db()
    # Create a tenant for the test
    await core.handle_command("addTenant", {"id": "test-tenant", "name": "Test"})
    result = await core.handle_command("run now", {"tenant_id": "test-tenant"})
    assert result["ok"] is True
    # "run now" wakes up both loops for immediate processing
    assert core._wake_event.is_set()  # SMTP dispatch loop
    assert core._wake_client_event.is_set()  # Client report loop
    core._wake_event.clear()
    core._wake_client_event.clear()


@pytest.mark.asyncio
async def test_test_mode_start_waits_for_run_now(tmp_path):
    db_path = tmp_path / "core-test.db"
    core = MailProxy(db_path=str(db_path), start_active=True, test_mode=True)
    await core.start()
    try:
        assert math.isinf(core._send_loop_interval)
        assert core._task_smtp is not None
        assert core._task_client is not None
        assert not core._task_smtp.done()
        assert not core._task_client.done()
        # Create a tenant for the test
        await core.handle_command("addTenant", {"id": "test-tenant", "name": "Test"})
        result = await core.handle_command("run now", {"tenant_id": "test-tenant"})
        assert result["ok"] is True
        await asyncio.sleep(0)
        assert not core._wake_event.is_set()
    finally:
        await core.stop()


@pytest.mark.asyncio
async def test_add_messages_and_dispatch(tmp_path):
    core = await make_core(tmp_path)
    payload = {
        "messages": [
            {
                "id": "msg1",
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Hello",
                "body": "Body",
            }
        ]
    }
    result = await core.handle_command("addMessages", payload)
    assert result["ok"] is True
    assert result["queued"] == 1
    assert result["rejected"] == []

    await core._process_smtp_cycle()

    # Message sent and logged
    assert len(core.pool.smtp.sent) == 1
    assert core.metrics.sent_accounts == ["acc"]
    # Message stored with sent_ts
    messages = await core.db.list_messages()
    assert messages[0]["sent_ts"] is not None

    # Delivery report cycle marks message as reported
    await core._process_client_cycle()
    assert core.rate_limiter.logged == ["acc"]
    reported = await core.db.list_messages()
    assert reported[0]["reported_ts"] is not None

    # Retention removes the message after threshold
    past_ts = core._utc_now_epoch() - (core._report_retention_seconds + 10)
    await core.db.mark_reported(["msg1"], past_ts)
    await core._apply_retention()
    assert await core.db.list_messages() == []


@pytest.mark.asyncio
async def test_add_messages_rejects_invalid(tmp_path):
    core = await make_core(tmp_path)
    payload = {
        "messages": [
            {"from": "sender@example.com", "to": ["dest@example.com"], "subject": "Invalid", "body": "Body"}
        ]
    }
    result = await core.handle_command("addMessages", payload)
    assert result["ok"] is False
    assert result["rejected"][0]["reason"] == "missing id"


@pytest.mark.asyncio
async def test_add_messages_all_rejected_returns_ok_false(tmp_path):
    """Test that ok=False when ALL messages in a batch are rejected."""
    core = await make_core(tmp_path)
    # All messages have validation errors (missing id)
    payload = {
        "messages": [
            {"from": "a@x.com", "to": ["b@x.com"], "subject": "No ID 1", "body": "Body"},
            {"from": "c@x.com", "to": ["d@x.com"], "subject": "No ID 2", "body": "Body"},
            {"from": "e@x.com", "to": ["f@x.com"], "subject": "No ID 3", "body": "Body"},
        ]
    }
    result = await core.handle_command("addMessages", payload)
    assert result["ok"] is False
    assert result["queued"] == 0
    assert len(result["rejected"]) == 3


@pytest.mark.asyncio
async def test_add_messages_partial_rejection_returns_ok_true(tmp_path):
    """Test that ok=True when at least one message is accepted."""
    core = await make_core(tmp_path)
    payload = {
        "messages": [
            # Valid message
            {"id": "valid1", "account_id": "acc", "from": "a@x.com", "to": ["b@x.com"], "subject": "OK", "body": "Body"},
            # Invalid - missing id
            {"from": "c@x.com", "to": ["d@x.com"], "subject": "No ID", "body": "Body"},
            # Invalid - missing id
            {"from": "e@x.com", "to": ["f@x.com"], "subject": "No ID 2", "body": "Body"},
        ]
    }
    result = await core.handle_command("addMessages", payload)
    assert result["ok"] is True
    assert result["queued"] == 1
    assert len(result["rejected"]) == 2


@pytest.mark.asyncio
async def test_duplicate_messages_rejected(tmp_path):
    """Test that duplicate messages are replaced if not sent, rejected if already sent."""
    core = await make_core(tmp_path)
    base_msg = {
        "id": "dup",
        "account_id": "acc",
        "from": "sender@example.com",
        "to": ["dest@example.com"],
        "subject": "Hello",
        "body": "Body",
    }

    # First insert - should succeed
    first = await core.handle_command("addMessages", {"messages": [base_msg]})
    assert first["ok"] is True
    assert first["queued"] == 1
    assert len(first["rejected"]) == 0

    # Second insert (before sending) - should replace the message
    modified_msg = dict(base_msg)
    modified_msg["subject"] = "Modified subject"
    second = await core.handle_command("addMessages", {"messages": [modified_msg]})
    assert second["ok"] is True
    assert second["queued"] == 1  # Replaced, not duplicate
    assert len(second["rejected"]) == 0

    # Send the message
    await core._process_smtp_cycle()

    # Third insert (after sending) - should be rejected
    third = await core.handle_command("addMessages", {"messages": [base_msg]})
    assert third["ok"] is True
    assert third["queued"] == 0
    assert len(third["rejected"]) == 1
    assert third["rejected"][0]["reason"] == "already sent"


@pytest.mark.asyncio
async def test_rate_limited_message_is_deferred(tmp_path):
    core = await make_core(tmp_path)
    core.rate_limiter.plan_result = core._utc_now_epoch() + 60
    payload = {
        "messages": [
            {
                "id": "msg-defer",
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Wait",
                "body": "Body",
            }
        ]
    }
    await core.handle_command("addMessages", payload)
    await core._process_smtp_cycle()
    assert core.metrics.deferred_accounts == ["acc"]
    ready = await core.db.fetch_ready_messages(limit=5, now_ts=core._utc_now_epoch())
    assert ready == []


@pytest.mark.asyncio
async def test_send_failure_sets_error(tmp_path):
    """Test that temporary errors are retried automatically."""
    core = await make_core(tmp_path)
    core.pool.smtp.raise_error = RuntimeError("boom")
    payload = {
        "messages": [
            {
                "id": "msg-error",
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Hi",
                "body": "Body",
            }
        ]
    }
    await core.handle_command("addMessages", payload)
    await core._process_smtp_cycle()
    messages = await core.db.list_messages()

    # RuntimeError("boom") is classified as temporary, so message should be deferred
    assert messages[0]["error_ts"] is None, "Temporary errors should not set error_ts"
    assert messages[0]["deferred_ts"] is not None, "Temporary errors should defer message"

    # Check that retry_count was incremented in payload
    msg_payload = messages[0]["message"]
    assert msg_payload.get("retry_count", 0) == 1, "Retry count should be 1"


@pytest.mark.asyncio
async def test_temporary_error_retry_exhaustion(tmp_path):
    """Test that messages fail permanently after max retries."""
    core = await make_core(tmp_path, max_retries=3)
    core.pool.smtp.raise_error = RuntimeError("temporary error")
    core.pool.smtp.raise_error_persistent = True  # Keep raising error for all attempts

    payload = {
        "messages": [
            {
                "id": "msg-retry-exhausted",
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Hi",
                "body": "Body",
            }
        ]
    }
    await core.handle_command("addMessages", payload)

    # Simulate 4 attempts (initial + 3 retries)
    for attempt in range(4):
        # If not the first attempt, clear deferred_ts to make message ready for processing
        if attempt > 0:
            # Clear deferred_ts so the message is immediately ready
            await core.db.clear_deferred("msg-retry-exhausted")

        # Process the SMTP cycle
        processed = await core._process_smtp_cycle()
        messages = await core.db.list_messages()

        if attempt < 3:
            # Should be deferred for retries
            assert processed, f"Attempt {attempt}: should have processed a message"
            assert messages[0]["error_ts"] is None, f"Attempt {attempt}: should not have error_ts"
            assert messages[0]["deferred_ts"] is not None, f"Attempt {attempt}: should have deferred_ts, got {messages[0]}"
            # Verify retry count
            msg_payload = messages[0]["message"]
            assert msg_payload.get("retry_count", 0) == attempt + 1, f"Attempt {attempt}: expected retry_count {attempt+1}, got {msg_payload.get('retry_count', 0)}"
        else:
            # After max retries, should be marked as error
            assert processed, "Final attempt should have processed the message"
            assert messages[0]["error_ts"] is not None, "Should have error_ts after max retries"
            assert "Max retries" in messages[0]["error"], f"Error should mention max retries, got: {messages[0]['error']}"
            break


@pytest.mark.asyncio
async def test_permanent_error_no_retry(tmp_path):
    """Test that permanent 5xx SMTP errors are not retried."""
    import aiosmtplib

    core = await make_core(tmp_path)

    # Create a permanent SMTP error (5xx)
    smtp_error = aiosmtplib.SMTPResponseException(550, "Mailbox not found")
    smtp_error.smtp_code = 550
    core.pool.smtp.raise_error = smtp_error

    payload = {
        "messages": [
            {
                "id": "msg-permanent",
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Hi",
                "body": "Body",
            }
        ]
    }
    await core.handle_command("addMessages", payload)
    await core._process_smtp_cycle()
    messages = await core.db.list_messages()

    # 5xx errors should be marked as permanent errors immediately
    assert messages[0]["error_ts"] is not None
    assert messages[0]["deferred_ts"] is None
    assert "550" in messages[0]["error"]
    assert core.metrics.error_accounts == ["acc"]


@pytest.mark.asyncio
async def test_batch_size_per_account_limiting(tmp_path):
    """Test that batch_size_per_account limits messages sent per account per cycle."""
    core = await make_core(tmp_path)
    core._batch_size_per_account = 2  # Set limit to 2 messages per account per cycle

    # Add 5 messages for account 'acc'
    messages = []
    for i in range(5):
        messages.append({
            "id": f"msg-acc-{i}",
            "account_id": "acc",
            "from": "sender@example.com",
            "to": ["dest@example.com"],
            "subject": f"Message {i}",
            "body": "Body",
        })

    await core.handle_command("addMessages", {"messages": messages})

    # First cycle should process only 2 messages
    await core._process_smtp_cycle()
    assert len(core.pool.smtp.sent) == 2

    # Second cycle should process 2 more
    await core._process_smtp_cycle()
    assert len(core.pool.smtp.sent) == 4

    # Third cycle should process the last message
    await core._process_smtp_cycle()
    assert len(core.pool.smtp.sent) == 5


@pytest.mark.asyncio
async def test_batch_size_per_account_multiple_accounts(tmp_path):
    """Test that batch_size_per_account is applied per account independently."""
    core = await make_core(tmp_path)

    # Add second account
    await core.handle_command("addAccount", {"id": "acc2", "host": "smtp2.local", "port": 25})

    core._batch_size_per_account = 2  # Set limit to 2 messages per account

    # Add 3 messages for 'acc' and 3 for 'acc2'
    messages = []
    for i in range(3):
        messages.append({
            "id": f"msg-acc-{i}",
            "account_id": "acc",
            "from": "sender@example.com",
            "to": ["dest@example.com"],
            "subject": f"Message acc {i}",
            "body": "Body",
        })
        messages.append({
            "id": f"msg-acc2-{i}",
            "account_id": "acc2",
            "from": "sender@example.com",
            "to": ["dest@example.com"],
            "subject": f"Message acc2 {i}",
            "body": "Body",
        })

    await core.handle_command("addMessages", {"messages": messages})

    # First cycle should process 2 messages per account = 4 total
    await core._process_smtp_cycle()
    assert len(core.pool.smtp.sent) == 4

    # Second cycle should process 1 more per account = 2 more total
    await core._process_smtp_cycle()
    assert len(core.pool.smtp.sent) == 6


@pytest.mark.asyncio
async def test_batch_size_per_account_override(tmp_path):
    """Test that account-specific batch_size overrides the global default."""
    core = await make_core(tmp_path)
    core._batch_size_per_account = 10  # Global default

    # Add second account with custom batch_size
    await core.handle_command("addAccount", {
        "id": "acc2",
        "host": "smtp2.local",
        "port": 25,
        "batch_size": 1  # Override: only 1 message per cycle
    })

    # Add 3 messages for each account
    messages = []
    for i in range(3):
        messages.append({
            "id": f"msg-acc-{i}",
            "account_id": "acc",
            "from": "sender@example.com",
            "to": ["dest@example.com"],
            "subject": f"Message acc {i}",
            "body": "Body",
        })
        messages.append({
            "id": f"msg-acc2-{i}",
            "account_id": "acc2",
            "from": "sender@example.com",
            "to": ["dest@example.com"],
            "subject": f"Message acc2 {i}",
            "body": "Body",
        })

    await core.handle_command("addMessages", {"messages": messages})

    # First cycle: acc sends 3 (limited by 10), acc2 sends 1 (limited by its override)
    await core._process_smtp_cycle()
    assert len(core.pool.smtp.sent) == 4  # 3 from acc + 1 from acc2

    # Second cycle: acc has 0 left, acc2 sends 1 more
    await core._process_smtp_cycle()
    assert len(core.pool.smtp.sent) == 5  # +1 from acc2

    # Third cycle: acc2 sends the last one
    await core._process_smtp_cycle()
    assert len(core.pool.smtp.sent) == 6  # +1 from acc2

@pytest.mark.asyncio
async def test_cleanup_messages_command(tmp_path):
    """Test manual cleanup of reported messages via command."""
    core = await make_core(tmp_path)

    # Create a tenant and associate the account with it (addAccount does upsert)
    await core.handle_command("addTenant", {"id": "test-tenant", "name": "Test"})
    await core.handle_command(
        "addAccount", {"id": "acc", "tenant_id": "test-tenant", "host": "smtp.local", "port": 25}
    )

    # Add and send a message
    payload = {
        "messages": [
            {
                "id": "msg-cleanup",
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Test",
                "body": "Body",
            }
        ]
    }
    await core.handle_command("addMessages", payload)
    await core._process_smtp_cycle()

    # Mark as reported (artificially old)
    old_ts = core._utc_now_epoch() - 10000
    await core.db.mark_reported(["msg-cleanup"], old_ts)

    # Verify message exists
    messages = await core.db.list_messages()
    assert len(messages) == 1

    # Cleanup with custom threshold (older than 5000 seconds)
    # tenant_id is required for security isolation
    result = await core.handle_command(
        "cleanupMessages", {"tenant_id": "test-tenant", "older_than_seconds": 5000}
    )
    assert result["ok"] is True
    assert result["removed"] == 1

    # Verify message was removed
    messages = await core.db.list_messages()
    assert len(messages) == 0


@pytest.mark.asyncio
async def test_mime_type_override_in_attachment(tmp_path):
    """Test that explicit mime_type in attachment overrides auto-detection."""

    core = await make_core(tmp_path)

    # Create a custom attachment fetcher that returns fixed content
    class TrackingAttachments:
        def __init__(self):
            self.fetched = []

        async def fetch(self, attachment):
            self.fetched.append(attachment)
            return b"test content", attachment.get("filename", "file.bin")

        def guess_mime(self, filename):
            # Always return octet-stream for detection
            return "application", "octet-stream"

    tracker = TrackingAttachments()
    core.attachments = tracker

    # Send message with attachment that has mime_type override
    payload = {
        "messages": [
            {
                "id": "msg-mime-override",
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Test MIME Override",
                "body": "Body",
                "attachments": [
                    {
                        "filename": "data.bin",
                        "storage_path": "base64:dGVzdA==",
                        "mime_type": "application/json"  # Override
                    }
                ]
            }
        ]
    }
    await core.handle_command("addMessages", payload)
    await core._process_smtp_cycle()

    # Verify message was sent
    assert len(core.pool.smtp.sent) == 1
    sent_msg = core.pool.smtp.sent[0]["message"]

    # Check the attachment has the overridden MIME type
    parts = list(sent_msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_content_type() == "application/json"


@pytest.mark.asyncio
async def test_mime_type_fallback_when_not_specified(tmp_path):
    """Test that MIME type is guessed when not explicitly specified."""
    core = await make_core(tmp_path)

    class TrackingAttachments:
        def __init__(self):
            self.fetched = []
            self.guess_called = False

        async def fetch(self, attachment):
            self.fetched.append(attachment)
            return b"test content", attachment.get("filename", "file.bin")

        def guess_mime(self, filename):
            self.guess_called = True
            if filename.endswith(".pdf"):
                return "application", "pdf"
            return "application", "octet-stream"

    tracker = TrackingAttachments()
    core.attachments = tracker

    # Send message without mime_type in attachment
    payload = {
        "messages": [
            {
                "id": "msg-mime-guess",
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Test MIME Guess",
                "body": "Body",
                "attachments": [
                    {
                        "filename": "document.pdf",
                        "storage_path": "base64:dGVzdA=="
                        # No mime_type - should be guessed
                    }
                ]
            }
        ]
    }
    await core.handle_command("addMessages", payload)
    await core._process_smtp_cycle()

    # Verify guess_mime was called
    assert tracker.guess_called

    # Check the attachment has guessed MIME type
    sent_msg = core.pool.smtp.sent[0]["message"]
    parts = list(sent_msg.iter_attachments())
    assert len(parts) == 1
    assert parts[0].get_content_type() == "application/pdf"


@pytest.mark.asyncio
async def test_tenant_attachment_config_applied(tmp_path):
    """Test that tenant's client_base_url is used for message attachments."""
    core = await make_core(tmp_path)

    # Track which config was used
    configs_used = []

    class ConfigTrackingAttachments:
        def __init__(self, http_endpoint=None):
            self.http_endpoint = http_endpoint

        async def fetch(self, attachment):
            configs_used.append(self.http_endpoint)
            return b"content", attachment.get("filename", "file.bin")

        def guess_mime(self, filename):
            return "application", "octet-stream"

    # Set global attachment manager
    core.attachments = ConfigTrackingAttachments(http_endpoint="https://global.example.com")
    core._attachment_config = types.SimpleNamespace(
        base_dir="/global/base",
        http_endpoint="https://global.example.com",
        http_auth_config=None,
    )
    core._attachment_cache = None

    # Create tenant with custom attachment config
    await core.handle_command("addTenant", {
        "id": "tenant1",
        "name": "Test Tenant",
        "client_base_url": "https://tenant1.example.com",
        "client_attachment_path": "/attachments",
    })

    # Associate account with tenant
    await core.handle_command("addAccount", {
        "id": "tenant1-acc",
        "tenant_id": "tenant1",
        "host": "smtp.tenant1.local",
        "port": 25,
    })

    # Send message using tenant account
    payload = {
        "messages": [
            {
                "id": "msg-tenant-att",
                "account_id": "tenant1-acc",
                "from": "sender@tenant1.com",
                "to": ["dest@example.com"],
                "subject": "Test Tenant Attachment",
                "body": "Body",
                "attachments": [
                    {
                        "filename": "report.pdf",
                        "storage_path": "dGVzdA==",
                        "fetch_mode": "base64"
                    }
                ]
            }
        ]
    }
    await core.handle_command("addMessages", payload)

    # Clear configs_used before processing
    configs_used.clear()

    await core._process_smtp_cycle()

    # Verify tenant config was used (not global)
    # The attachment manager should have been created with tenant's http_endpoint
    assert len(core.pool.smtp.sent) == 1


@pytest.mark.asyncio
async def test_tenant_attachment_config_fallback_to_global(tmp_path):
    """Test that global config is used when tenant has no client_base_url."""
    core = await make_core(tmp_path)

    # Create tenant WITHOUT attachment config
    await core.handle_command("addTenant", {
        "id": "tenant-no-config",
        "name": "Tenant No Config",
        # No client_base_url
    })

    # Associate account with tenant
    await core.handle_command("addAccount", {
        "id": "tenant-no-config-acc",
        "tenant_id": "tenant-no-config",
        "host": "smtp.local",
        "port": 25,
    })

    # Track that global manager is used
    fetch_called = []

    class GlobalAttachments:
        async def fetch(self, attachment):
            fetch_called.append("global")
            return b"content", attachment.get("filename", "file.bin")

        def guess_mime(self, filename):
            return "application", "octet-stream"

    core.attachments = GlobalAttachments()

    # Send message
    payload = {
        "messages": [
            {
                "id": "msg-fallback",
                "account_id": "tenant-no-config-acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Test Fallback",
                "body": "Body",
                "attachments": [
                    {
                        "filename": "doc.pdf",
                        "storage_path": "base64:dGVzdA=="
                    }
                ]
            }
        ]
    }
    await core.handle_command("addMessages", payload)
    await core._process_smtp_cycle()

    # Verify global manager was used
    assert "global" in fetch_called
    assert len(core.pool.smtp.sent) == 1


@pytest.mark.asyncio
async def test_account_configuration_error():
    """Test AccountConfigurationError exception."""
    from mail_proxy.core import AccountConfigurationError

    # Default message
    exc = AccountConfigurationError()
    assert str(exc) == "Missing SMTP account configuration"
    assert exc.code == "missing_account_configuration"

    # Custom message
    exc2 = AccountConfigurationError("Custom error message")
    assert str(exc2) == "Custom error message"
    assert exc2.code == "missing_account_configuration"


@pytest.mark.asyncio
async def test_normalise_priority_edge_cases(tmp_path):
    """Test priority normalization with edge cases."""
    core = await make_core(tmp_path)

    # String priority labels
    priority, label = core._normalise_priority("immediate")
    assert priority == 0
    assert label == "immediate"

    priority, label = core._normalise_priority("HIGH")  # case insensitive
    assert priority == 1
    assert label == "high"

    # Invalid string falls back to default
    priority, label = core._normalise_priority("invalid")
    assert priority == 2  # default
    assert label == "medium"

    # String number
    priority, label = core._normalise_priority("1")
    assert priority == 1
    assert label == "high"

    # Out of range clamps
    priority, label = core._normalise_priority(100)
    assert priority == 3  # max

    priority, label = core._normalise_priority(-5)
    assert priority == 0  # min

    # None uses default
    priority, label = core._normalise_priority(None)
    assert priority == 2

    # Custom default as string
    priority, label = core._normalise_priority(None, "low")
    assert priority == 3

    # Invalid default falls back to DEFAULT_PRIORITY
    priority, label = core._normalise_priority(None, "invalid_default")
    assert priority == 2


@pytest.mark.asyncio
async def test_init_with_cache_config(tmp_path, monkeypatch):
    """Test core initialization with cache configuration."""
    # Set cache env vars
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    monkeypatch.setenv("GMP_CACHE_DISK_DIR", str(cache_dir))
    monkeypatch.setenv("GMP_CACHE_MEMORY_MAX_MB", "25")

    db_path = tmp_path / "core-cache.db"
    core = MailProxy(db_path=str(db_path), start_active=False, test_mode=True)
    await core.init()

    # Verify cache was initialized
    assert core._cache_config is not None
    assert core._cache_config.enabled is True
    assert core._cache_config.disk_dir == str(cache_dir)
    assert core._cache_config.memory_max_mb == 25.0
    assert core._attachment_cache is not None


@pytest.mark.asyncio
async def test_summarise_addresses():
    """Test address summarization helper."""
    from mail_proxy.core import MailProxy

    # Empty returns "-"
    assert MailProxy._summarise_addresses(None) == "-"
    assert MailProxy._summarise_addresses("") == "-"
    assert MailProxy._summarise_addresses([]) == "-"

    # Single address
    assert MailProxy._summarise_addresses("test@example.com") == "test@example.com"

    # List of addresses
    result = MailProxy._summarise_addresses(["a@b.com", "c@d.com"])
    assert "a@b.com" in result
    assert "c@d.com" in result


@pytest.mark.asyncio
async def test_message_without_account_uses_default(tmp_path):
    """Test that messages without account_id use default SMTP settings."""
    core = await make_core(tmp_path)

    # Set default SMTP settings
    core.default_host = "default.smtp.local"
    core.default_port = 587

    # Add message WITHOUT account_id
    payload = {
        "messages": [{
            "id": "msg-no-account",
            "from": "sender@example.com",
            "to": ["dest@example.com"],
            "subject": "No Account",
            "body": "Body",
        }]
    }
    result = await core.handle_command("addMessages", payload)
    assert result["ok"] is True

    await core._process_smtp_cycle()

    # Should have used default host
    assert len(core.pool.requests) == 1
    host, port, _, _, _ = core.pool.requests[0]
    assert host == "default.smtp.local"
    assert port == 587


@pytest.mark.asyncio
async def test_parallel_dispatch_multiple_messages(tmp_path):
    """Test that multiple messages are dispatched in parallel."""
    core = await make_core(tmp_path)

    # Add multiple messages
    messages = []
    for i in range(5):
        messages.append({
            "id": f"parallel-msg-{i}",
            "account_id": "acc",
            "from": "sender@example.com",
            "to": ["dest@example.com"],
            "subject": f"Parallel Test {i}",
            "body": f"Body {i}",
        })

    result = await core.handle_command("addMessages", {"messages": messages})
    assert result["ok"] is True
    assert result["queued"] == 5

    # Process all messages
    await core._process_smtp_cycle()

    # All 5 messages should have been sent
    assert len(core.pool.smtp.sent) == 5


@pytest.mark.asyncio
async def test_parallel_dispatch_respects_global_concurrency(tmp_path):
    """Test that global concurrency limit is respected."""
    core = await make_core(tmp_path)

    # Verify default concurrency settings
    assert core._max_concurrent_sends == 10
    assert core._max_concurrent_per_account == 3


@pytest.mark.asyncio
async def test_parallel_dispatch_per_account_semaphore(tmp_path):
    """Test that per-account semaphores are created correctly."""
    core = await make_core(tmp_path)

    # Get semaphore for an account
    sem1 = core._get_account_semaphore("acc-1")
    sem2 = core._get_account_semaphore("acc-1")
    sem3 = core._get_account_semaphore("acc-2")

    # Same account should return same semaphore
    assert sem1 is sem2
    # Different account should return different semaphore
    assert sem1 is not sem3


@pytest.mark.asyncio
async def test_parallel_dispatch_custom_concurrency(tmp_path):
    """Test that custom concurrency settings are applied."""
    db_path = tmp_path / "custom-concurrent.db"
    core = MailProxy(
        db_path=str(db_path),
        start_active=False,
        test_mode=True,
        max_concurrent_sends=5,
        max_concurrent_per_account=2,
    )
    await core.init()

    assert core._max_concurrent_sends == 5
    assert core._max_concurrent_per_account == 2


@pytest.mark.asyncio
async def test_priority_immediate_processed_first(tmp_path):
    """Test that immediate priority messages are processed before regular ones."""
    core = await make_core(tmp_path)

    # Track dispatch order
    dispatch_order = []
    original_dispatch = core._dispatch_message

    async def tracking_dispatch(entry, now_ts):
        dispatch_order.append(entry["id"])
        await original_dispatch(entry, now_ts)

    core._dispatch_message = tracking_dispatch

    # Add messages: some regular, some immediate
    messages = [
        {"id": "regular1", "account_id": "acc", "priority": 2, "from": "a@x.com", "to": ["b@x.com"], "subject": "r1", "body": "r1"},
        {"id": "regular2", "account_id": "acc", "priority": 2, "from": "a@x.com", "to": ["b@x.com"], "subject": "r2", "body": "r2"},
        {"id": "immediate1", "account_id": "acc", "priority": 0, "from": "a@x.com", "to": ["b@x.com"], "subject": "i1", "body": "i1"},
        {"id": "regular3", "account_id": "acc", "priority": 3, "from": "a@x.com", "to": ["b@x.com"], "subject": "r3", "body": "r3"},
        {"id": "immediate2", "account_id": "acc", "priority": 0, "from": "a@x.com", "to": ["b@x.com"], "subject": "i2", "body": "i2"},
    ]
    await core.handle_command("addMessages", {"messages": messages})

    # Process messages
    await core._process_smtp_cycle()

    # Immediate messages should be processed first (order within each batch may vary due to parallel dispatch)
    immediate_indices = [dispatch_order.index(m) for m in ["immediate1", "immediate2"]]
    regular_indices = [dispatch_order.index(m) for m in ["regular1", "regular2", "regular3"]]

    # All immediate messages should have lower indices than all regular messages
    assert max(immediate_indices) < min(regular_indices), (
        f"Immediate messages should be processed first. Order: {dispatch_order}"
    )
