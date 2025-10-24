import asyncio
import math
import types
from typing import Any, Dict, List

import pytest

from async_mail_service.core import AsyncMailCore


class DummySMTP:
    def __init__(self):
        self.sent: List[Dict[str, Any]] = []
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
        self.requests: List[Any] = []

    async def get_connection(self, host, port, user, password, use_tls):
        self.requests.append((host, port, user, password, use_tls))
        return self.smtp

    async def cleanup(self):
        return None


class DummyRateLimiter:
    def __init__(self):
        self.plan_result: int | None = None
        self.logged: List[str] = []

    async def check_and_plan(self, account):
        return self.plan_result

    async def log_send(self, account_id: str):
        self.logged.append(account_id)


class DummyMetrics:
    def __init__(self):
        self.pending_value = None
        self.sent_accounts: List[str] = []
        self.error_accounts: List[str] = []
        self.deferred_accounts: List[str] = []
        self.rate_limited_accounts: List[str] = []

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
        self.payloads: List[Dict[str, Any]] = []

    async def __call__(self, payload: Dict[str, Any]):
        self.payloads.append(payload)


async def make_core(tmp_path, max_retries=5) -> AsyncMailCore:
    db_path = tmp_path / "core.db"
    reporter = DummyReporter()
    core = AsyncMailCore(
        db_path=str(db_path),
        start_active=True,
        report_delivery_callable=reporter,
        report_retention_seconds=2,
        test_mode=True,
        max_retries=max_retries,
    )
    await core.persistence.init_db()
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
    core = AsyncMailCore(db_path=str(db_path), start_active=True)
    await core.persistence.init_db()
    result = await core.handle_command("run now", {})
    assert result["ok"] is True
    # "run now" wakes up only the client loop (for immediate report delivery)
    # SMTP loop runs every 0.5s by default, which is fast enough
    assert not core._wake_event.is_set()
    assert core._wake_client_event.is_set()
    core._wake_client_event.clear()


@pytest.mark.asyncio
async def test_test_mode_start_waits_for_run_now(tmp_path):
    db_path = tmp_path / "core-test.db"
    core = AsyncMailCore(db_path=str(db_path), start_active=True, test_mode=True)
    await core.start()
    try:
        assert math.isinf(core._send_loop_interval)
        assert core._task_smtp is not None
        assert core._task_client is not None
        assert not core._task_smtp.done()
        assert not core._task_client.done()
        result = await core.handle_command("run now", {})
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
    messages = await core.persistence.list_messages()
    assert messages[0]["sent_ts"] is not None

    # Delivery report cycle marks message as reported
    await core._process_client_cycle()
    assert core.rate_limiter.logged == ["acc"]
    reported = await core.persistence.list_messages()
    assert reported[0]["reported_ts"] is not None

    # Retention removes the message after threshold
    past_ts = core._utc_now_epoch() - (core._report_retention_seconds + 10)
    await core.persistence.mark_reported(["msg1"], past_ts)
    await core._apply_retention()
    assert await core.persistence.list_messages() == []


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
    ready = await core.persistence.fetch_ready_messages(limit=5, now_ts=core._utc_now_epoch())
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
    messages = await core.persistence.list_messages()

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
            await core.persistence.clear_deferred("msg-retry-exhausted")

        # Process the SMTP cycle
        processed = await core._process_smtp_cycle()
        messages = await core.persistence.list_messages()

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
    messages = await core.persistence.list_messages()

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
    await core.persistence.mark_reported(["msg-cleanup"], old_ts)
    
    # Verify message exists
    messages = await core.persistence.list_messages()
    assert len(messages) == 1
    
    # Cleanup with custom threshold (older than 5000 seconds)
    result = await core.handle_command("cleanupMessages", {"older_than_seconds": 5000})
    assert result["ok"] is True
    assert result["removed"] == 1
    
    # Verify message was removed
    messages = await core.persistence.list_messages()
    assert len(messages) == 0
