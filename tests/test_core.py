import types
from datetime import datetime, timezone
from email.message import EmailMessage
from unittest.mock import AsyncMock

import pytest

from async_mail_service import core as core_module
from async_mail_service.core import AsyncMailCore


class StubPersistence:
    def __init__(self):
        self.accounts = {}
        self.pending = {}
        self.deferred = {}
        self.send_log = []
        self.cleared = []
        self.rules = []
        self.rule_id = 0
        self.delivery_reports = {}
        self.delivery_seq = 0

    async def init_db(self):
        return None

    async def add_account(self, acc):
        self.accounts[acc["id"]] = acc.copy()

    async def list_accounts(self):
        return [acc.copy() for acc in self.accounts.values()]

    async def delete_account(self, account_id):
        self.accounts.pop(account_id, None)

    async def get_account(self, account_id):
        if account_id not in self.accounts:
            raise ValueError(f"Account '{account_id}' not found")
        return self.accounts[account_id].copy()

    async def list_pending(self):
        return list(self.pending.values())

    async def add_pending(self, msg_id, to_addr, subject):
        self.pending[msg_id] = {"id": msg_id, "to_addr": to_addr, "subject": subject}

    async def remove_pending(self, msg_id):
        self.pending.pop(msg_id, None)

    async def list_deferred(self):
        return [
            {"id": msg_id, "account_id": acc_id, "deferred_until": entry["deferred_until"]}
            for (msg_id, acc_id), entry in self.deferred.items()
        ]

    async def set_deferred(self, msg_id, account_id, deferred_until):
        self.deferred[(msg_id, account_id)] = {"deferred_until": deferred_until}

    async def get_deferred_until(self, msg_id, account_id):
        entry = self.deferred.get((msg_id, account_id))
        return entry["deferred_until"] if entry else None

    async def clear_deferred(self, msg_id):
        for key in list(self.deferred):
            if key[0] == msg_id:
                self.deferred.pop(key)
        self.cleared.append(msg_id)

    async def log_send(self, account_id, timestamp):
        self.send_log.append((account_id, timestamp))

    async def count_sends_since(self, account_id, since_ts):
        return sum(1 for acc_id, ts in self.send_log if acc_id == account_id and ts > since_ts)

    async def list_rules(self):
        return [rule.copy() for rule in self.rules]

    async def add_rule(self, rule):
        self.rule_id += 1
        stored = rule.copy()
        stored.setdefault("interval_minutes", 1)
        stored.setdefault("enabled", True)
        stored.setdefault("priority", len(self.rules))
        stored.setdefault("days", [])
        stored["id"] = self.rule_id
        self.rules.append(stored)
        return stored

    async def delete_rule(self, rule_id):
        self.rules = [r for r in self.rules if r["id"] != rule_id]

    async def set_rule_enabled(self, rule_id, enabled):
        for rule in self.rules:
            if rule["id"] == rule_id:
                rule["enabled"] = enabled

    async def clear_rules(self):
        self.rules = []

    async def save_delivery_report(self, event):
        self.delivery_seq += 1
        report_id = f"report-{self.delivery_seq}"
        self.delivery_reports[report_id] = {"payload": event.copy(), "retry_count": 0}
        return report_id

    async def list_delivery_reports(self):
        return [
            {"id": rid, "payload": data["payload"].copy(), "retry_count": data["retry_count"]}
            for rid, data in self.delivery_reports.items()
        ]

    async def delete_delivery_report(self, report_id):
        self.delivery_reports.pop(report_id, None)

    async def increment_report_retry(self, report_id):
        if report_id in self.delivery_reports:
            self.delivery_reports[report_id]["retry_count"] += 1


class StubRateLimiter:
    def __init__(self):
        self.plan_map = {}
        self.logged = []

    async def check_and_plan(self, account):
        return self.plan_map.get(account["id"])

    async def log_send(self, account_id):
        self.logged.append(account_id)


class StubFetcher:
    def __init__(self, messages=None):
        self.messages = list(messages or [])
        self.calls = 0
        self.reports = []

    async def fetch_messages(self):
        self.calls += 1
        current = list(self.messages)
        self.messages.clear()
        return current

    async def report_delivery(self, payload):
        self.reports.append(payload)


class StubSMTP:
    def __init__(self):
        self.sent = []
        self.should_fail = False
        self.closed = False
        self.last_from_addr = None

    async def connect(self):
        return None

    async def login(self, *_args, **_kwargs):
        return None

    async def send_message(self, message, from_addr=None, to_addrs=None, mail_options=None, rcpt_options=None):
        if self.should_fail:
            raise RuntimeError("send failure")
        self.last_from_addr = from_addr
        self.sent.append(message)

    async def noop(self):
        return 250, b"OK"

    async def quit(self):
        self.closed = True


class StubPool:
    def __init__(self):
        self.smtp = StubSMTP()
        self.requests = []
        self.cleaned = False

    async def get_connection(self, host, port, user, password, use_tls):
        self.requests.append((host, port, user, password, use_tls))
        return self.smtp

    async def cleanup(self):
        self.cleaned = True


class StubAttachments:
    def __init__(self, data_map=None):
        self.data_map = data_map or {}
        self.guessed = []

    async def fetch(self, attachment):
        filename = attachment.get("filename")
        return self.data_map.get(filename)

    def guess_mime(self, filename):
        self.guessed.append(filename)
        if filename.endswith(".txt"):
            return "text", "plain"
        return "application", "octet-stream"


class StubMetrics:
    def __init__(self):
        self.pending_value = None
        self.sent_accounts = []
        self.error_accounts = []
        self.deferred_accounts = []
        self.rate_limited_accounts = []

    def set_pending(self, value):
        self.pending_value = value

    def inc_sent(self, account_id):
        self.sent_accounts.append(account_id or "default")

    def inc_error(self, account_id):
        self.error_accounts.append(account_id or "default")

    def inc_deferred(self, account_id):
        self.deferred_accounts.append(account_id or "default")

    def inc_rate_limited(self, account_id):
        self.rate_limited_accounts.append(account_id or "default")


def make_core(messages=None):
    core = AsyncMailCore(start_active=True)
    core.logger = types.SimpleNamespace(
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )
    core.persistence = StubPersistence()
    core.rate_limiter = StubRateLimiter()
    core.fetcher = StubFetcher(messages)
    core.pool = StubPool()
    core.attachments = StubAttachments()
    core.metrics = StubMetrics()
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
    core.persistence.rules = core._rules.copy()
    core.persistence.rule_id = len(core.persistence.rules)
    return core


@pytest.mark.asyncio
async def test_handle_command_dispatch(monkeypatch):
    core = make_core()

    fetch_mock = AsyncMock()
    monkeypatch.setattr(core, "_fetch_and_send_once", fetch_mock)

    await core.persistence.add_account({"id": "acc", "host": "smtp", "port": 25})
    await core.persistence.add_pending("id1", "to@example.com", "Subject")
    core.persistence.deferred[("id2", "acc")] = {"deferred_until": 1700}

    assert await core.handle_command("run now") == {"ok": True}
    fetch_mock.assert_awaited()

    assert await core.handle_command("suspend") == {"ok": True, "active": False}
    assert core._active is False

    assert await core.handle_command("activate") == {"ok": True, "active": True}
    assert core._active is True

    rule_payload = {"name": "peak", "days": [1], "start_hour": 9, "end_hour": 12, "interval_minutes": 5}
    response = await core.handle_command("addRule", rule_payload)
    assert response["ok"] is True
    assert len(response["rules"]) == 2
    listed = await core.handle_command("listRules")
    assert listed["ok"] is True
    assert any(rule.get("name") == "peak" for rule in listed["rules"])

    await core.handle_command("addAccount", {"id": "new", "host": "smtp", "port": 2525})
    accounts = await core.handle_command("listAccounts")
    assert accounts["ok"] is True
    assert any(acc["id"] == "new" for acc in accounts["accounts"])

    await core.handle_command("deleteAccount", {"id": "new"})
    assert all(acc["id"] != "new" for acc in (await core.handle_command("listAccounts"))["accounts"])

    pending = await core.handle_command("pendingMessages")
    assert pending["ok"] is True
    assert core.metrics.pending_value == len(pending["pending"])

    deferred = await core.handle_command("listDeferred")
    assert deferred["ok"] is True
    assert deferred["deferred"][0]["id"] == "id2"

    assert await core.handle_command("unknown") == {"ok": False, "error": "unknown command"}


@pytest.mark.asyncio
async def test_handle_command_send_message_success():
    core = make_core()
    await core.persistence.add_account({"id": "acc", "host": "smtp", "port": 25})

    payload = {
        "id": "msg-send",
        "account_id": "acc",
        "from": "sender@example.com",
        "to": ["dest@example.com"],
        "subject": "Test",
        "body": "Hello",
    }

    result = await core.handle_command("sendMessage", payload)
    assert result["ok"] is True
    assert result["result"]["status"] == "sent"

    queued = await core._result_queue.get()
    assert queued["status"] == "sent"


@pytest.mark.asyncio
async def test_handle_command_send_message_with_optional_headers(monkeypatch):
    core = make_core()
    await core.persistence.add_account({"id": "acc", "host": "smtp", "port": 25})

    captured = {}

    async def fake_send(msg, envelope_from, msg_id, account_id):
        captured["msg"] = msg
        captured["envelope_from"] = envelope_from
        captured["msg_id"] = msg_id
        captured["account_id"] = account_id
        return {"status": "sent", "timestamp": "now", "account": account_id}

    monkeypatch.setattr(core, "_send_with_limits", fake_send)

    payload = {
        "id": "msg-headers",
        "account_id": "acc",
        "from": "sender@example.com",
        "to": ["dest@example.com"],
        "cc": ["cc1@example.com", "cc2@example.com"],
        "bcc": "hidden@example.com",
        "reply_to": "reply@example.com",
        "return_path": "bounce@example.com",
        "message_id": "<custom-id@example.com>",
        "headers": {"X-Test": "value"},
        "subject": "Test headers",
        "body": "Body",
    }

    result = await core.handle_command("sendMessage", payload)
    assert result["ok"] is True
    assert result["result"]["status"] == "sent"

    msg = captured["msg"]
    assert msg["Cc"] == "cc1@example.com, cc2@example.com"
    assert msg["Bcc"] == "hidden@example.com"
    assert msg["Reply-To"] == "reply@example.com"
    assert msg["Message-ID"] == "<custom-id@example.com>"
    assert "Return-Path" not in msg
    assert msg["X-Test"] == "value"
    assert captured["envelope_from"] == "bounce@example.com"


@pytest.mark.asyncio
async def test_handle_command_send_message_missing_fields():
    core = make_core()

    result = await core.handle_command("sendMessage", {"to": ["dest@example.com"]})
    assert result["ok"] is False
    assert "missing" in result["error"]


@pytest.mark.asyncio
async def test_add_messages_queue_processed(monkeypatch):
    core = make_core()

    send_mock = AsyncMock()
    monkeypatch.setattr(core, "_send_with_limits", send_mock)

    payload = {
        "messages": [
            {
                "id": "queued-1",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Queued",
                "body": "Hello",
            }
        ]
    }

    result = await core.handle_command("addMessages", payload)
    assert result["ok"] is True
    assert result["queued"] == 1

    send_mock.assert_awaited()
    args, _ = send_mock.await_args
    _, _, msg_id, _ = args
    assert msg_id == "queued-1"


def test_current_interval_from_schedule(monkeypatch):
    core = make_core()

    assert core._current_interval_from_schedule() == 60

    class FakeDateTime(datetime):
        fixed_hour = 10
        fixed_day = 1

        @classmethod
        def now(cls, tz=None):
            return datetime(2024, 1, cls.fixed_day, cls.fixed_hour, 0, tzinfo=tz)

    monkeypatch.setattr(core_module, "datetime", FakeDateTime)

    # Rule active during 9-12 with interval 5 minutes
    core._rules.append(
        {
            "id": 2,
            "name": "morning",
            "enabled": True,
            "priority": 1,
            "days": [0],
            "start_hour": 9,
            "end_hour": 12,
            "cross_midnight": False,
            "interval_minutes": 5,
        }
    )
    assert core._current_interval_from_schedule() == 300

    # Cross-midnight rule overrides when matching
    core._rules.append(
        {
            "id": 3,
            "name": "night",
            "enabled": True,
            "priority": 2,
            "days": [0],
            "start_hour": 22,
            "end_hour": 2,
            "cross_midnight": True,
            "interval_minutes": 8,
        }
    )
    FakeDateTime.fixed_hour = 23
    assert core._current_interval_from_schedule() == 480

    FakeDateTime.fixed_hour = 15
    assert core._current_interval_from_schedule() == 60


@pytest.mark.asyncio
async def test_build_email_adds_only_available_attachments():
    core = make_core()
    core.attachments = StubAttachments({"a.txt": b"data"})

    data = {
        "from": "sender@example.com",
        "to": ["dest@example.com"],
        "subject": "Greetings",
        "body": "<p>Hello</p>",
        "content_type": "html",
        "attachments": [
            {"filename": "a.txt"},
            {"filename": "missing.bin"},
        ],
    }

    msg, envelope_from = await core._build_email(data)
    assert msg["From"] == "sender@example.com"
    assert msg.get_content_type() == "multipart/mixed"
    body = msg.get_body()
    assert body.get_content_subtype() == "html"
    attachments = list(msg.iter_attachments())
    assert len(attachments) == 1
    assert attachments[0].get_filename() == "a.txt"
    assert envelope_from == "sender@example.com"


@pytest.mark.asyncio
async def test_fetch_and_send_once_inactive():
    core = make_core([{"id": "1"}])
    core._active = False

    await core._fetch_and_send_once()
    assert core.fetcher.calls == 0


@pytest.mark.asyncio
async def test_fetch_and_send_once_handles_missing_fields():
    core = make_core([{"id": "msg1", "account_id": "acc"}])
    core.persistence.accounts["acc"] = {"id": "acc", "host": "smtp", "port": 25}

    await core._fetch_and_send_once()
    result = await core._result_queue.get()
    assert result["status"] == "error"
    assert "missing" in result["error"]
    await core._flush_delivery_reports()
    assert core.fetcher.reports[-1]["status"] == "error"


@pytest.mark.asyncio
async def test_fetch_and_send_once_skips_deferred(monkeypatch):
    future_ts = int(datetime.now(timezone.utc).timestamp()) + 100
    core = make_core(
        [
            {
                "id": "msg1",
                "account_id": "acc",
                "from": "a@example.com",
                "to": ["b@example.com"],
                "subject": "Subj",
            }
        ]
    )
    core.persistence.accounts["acc"] = {"id": "acc", "host": "smtp", "port": 25}
    core.persistence.deferred[("msg1", "acc")] = {"deferred_until": future_ts}

    send_mock = AsyncMock()
    monkeypatch.setattr(core, "_send_with_limits", send_mock)

    await core._fetch_and_send_once()
    result = await core._result_queue.get()
    assert result["status"] == "deferred"
    send_mock.assert_not_awaited()
    await core._flush_delivery_reports()
    assert core.fetcher.reports[-1]["status"] == "deferred"


@pytest.mark.asyncio
async def test_fetch_and_send_once_clears_expired_deferred(monkeypatch):
    core = make_core(
        [
            {
                "id": "msg2",
                "account_id": "acc",
                "from": "a@example.com",
                "to": ["b@example.com"],
                "subject": "Subj",
            }
        ]
    )
    core.persistence.accounts["acc"] = {"id": "acc", "host": "smtp", "port": 25}
    core.persistence.deferred[("msg2", "acc")] = {"deferred_until": 0}

    send_mock = AsyncMock()
    monkeypatch.setattr(core, "_send_with_limits", send_mock)

    await core._fetch_and_send_once()
    assert "msg2" in core.persistence.cleared
    send_mock.assert_awaited()


@pytest.mark.asyncio
async def test_send_with_limits_defers_when_rate_limited():
    core = make_core()
    await core.persistence.add_account({"id": "acc", "host": "smtp", "port": 25})
    core.rate_limiter.plan_map["acc"] = 9999

    msg = EmailMessage()
    msg["To"] = "dest@example.com"
    msg["Subject"] = "Hello"

    await core._send_with_limits(msg, None, "msg1", "acc")
    event = await core._result_queue.get()
    assert event["status"] == "deferred"
    assert core.metrics.deferred_accounts == ["acc"]
    assert core.metrics.rate_limited_accounts == ["acc"]
    assert ("msg1", "acc") in core.persistence.deferred
    await core._flush_delivery_reports()
    assert core.fetcher.reports[-1]["status"] == "deferred"


@pytest.mark.asyncio
async def test_send_with_limits_success_flow(monkeypatch):
    core = make_core()
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "friend@example.com"
    msg["Subject"] = "Hello"

    await core._send_with_limits(msg, None, "msg2", None)
    event = await core._result_queue.get()
    assert event["status"] == "sent"
    assert core.metrics.sent_accounts == ["default"]
    assert "msg2" not in core.persistence.pending
    assert core.rate_limiter.logged == ["default"]
    assert core.pool.smtp.last_from_addr == "sender@example.com"
    await core._flush_delivery_reports()
    assert core.fetcher.reports[-1]["status"] == "sent"


@pytest.mark.asyncio
async def test_send_with_limits_uses_custom_return_path():
    core = make_core()
    msg = EmailMessage()
    msg["From"] = "sender@example.com"
    msg["To"] = "friend@example.com"
    msg["Subject"] = "Hello"
    msg["Return-Path"] = "bounce@example.com"

    await core._send_with_limits(msg, "bounce@example.com", "msg-custom", None)
    await core._result_queue.get()
    assert core.pool.smtp.last_from_addr == "bounce@example.com"


@pytest.mark.asyncio
async def test_send_with_limits_handles_errors():
    core = make_core()
    core.pool.smtp.should_fail = True
    msg = EmailMessage()
    msg["To"] = "friend@example.com"
    msg["Subject"] = "Hello"

    await core._send_with_limits(msg, None, "msg3", None)
    event = await core._result_queue.get()
    assert event["status"] == "error"
    assert "msg3" not in core.persistence.pending
    assert core.metrics.error_accounts == ["default"]
    await core._flush_delivery_reports()
    assert core.fetcher.reports[-1]["status"] == "error"
