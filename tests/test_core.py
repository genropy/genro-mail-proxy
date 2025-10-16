import types
from typing import Any, Dict, List

import pytest

from async_mail_service.core import AsyncMailCore


class DummySMTP:
    def __init__(self):
        self.sent: List[Dict[str, Any]] = []
        self.raise_error: Exception | None = None

    async def send_message(self, message, from_addr=None, **_kwargs):
        if self.raise_error:
            exc = self.raise_error
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


async def make_core(tmp_path) -> AsyncMailCore:
    db_path = tmp_path / "core.db"
    reporter = DummyReporter()
    core = AsyncMailCore(
        db_path=str(db_path),
        start_active=True,
        report_delivery_callable=reporter,
        report_retention_seconds=2,
        test_mode=True,
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
    core._rules = [
        {
            "id": 1,
            "name": "default",
            "enabled": True,
            "priority": 0,
            "days": [],
            "start_hour": None,
            "end_hour": None,
            "cross_midnight": False,
            "interval_minutes": 1,
        }
    ]
    await core.handle_command("addAccount", {"id": "acc", "host": "smtp.local", "port": 25})
    return core


@pytest.mark.asyncio
async def test_run_now_disallowed_outside_test_mode(tmp_path):
    db_path = tmp_path / "core-prod.db"
    core = AsyncMailCore(db_path=str(db_path), start_active=True)
    await core.persistence.init_db()
    result = await core.handle_command("run now", {})
    assert result["ok"] is False
    assert "test_mode" in result["error"]


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

    await core.handle_command("run now", {})

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
    core = await make_core(tmp_path)
    base_msg = {
        "id": "dup",
        "account_id": "acc",
        "from": "sender@example.com",
        "to": ["dest@example.com"],
        "subject": "Hello",
        "body": "Body",
    }
    first = await core.handle_command("addMessages", {"messages": [base_msg]})
    assert first["ok"] is True
    second = await core.handle_command("addMessages", {"messages": [base_msg]})
    assert second["ok"] is True
    assert second["rejected"][0]["reason"] == "duplicate id"


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
    await core.handle_command("run now", {})
    assert core.metrics.deferred_accounts == ["acc"]
    ready = await core.persistence.fetch_ready_messages(limit=5, now_ts=core._utc_now_epoch())
    assert ready == []


@pytest.mark.asyncio
async def test_send_failure_sets_error(tmp_path):
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
    await core.handle_command("run now", {})
    messages = await core.persistence.list_messages()
    assert messages[0]["error_ts"] is not None
    assert core.metrics.error_accounts == ["acc"]
