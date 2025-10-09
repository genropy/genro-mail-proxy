import types

import pytest
from fastapi.testclient import TestClient

from async_mail_service import api
from async_mail_service.api import create_app, API_TOKEN_HEADER_NAME


API_TOKEN = "secret-token"


class DummyService:
    def __init__(self):
        self.calls = []
        self.metrics = types.SimpleNamespace(generate_latest=lambda: b"metrics-data")
        self.rules = []
        self.rule_id = 1

    async def handle_command(self, cmd, payload):
        self.calls.append((cmd, payload))
        if cmd == "sendMessage":
            return {
                "ok": True,
                "result": {
                    "id": payload.get("id"),
                    "status": "sent",
                    "timestamp": "2024-01-01T00:00:00Z",
                    "account": payload.get("account_id"),
                },
            }
        if cmd == "addMessages":
            return {"ok": True, "queued": len(payload.get("messages", []))}
        if cmd == "addRule":
            rule = payload.copy()
            rule.setdefault("interval_minutes", 1)
            rule.setdefault("enabled", True)
            rule.setdefault("priority", len(self.rules))
            rule.setdefault("days", [])
            rule["id"] = self.rule_id
            self.rule_id += 1
            self.rules.append(rule)
            return {"ok": True, "rules": list(self.rules)}
        if cmd == "listRules":
            return {"ok": True, "rules": list(self.rules)}
        if cmd == "deleteRule":
            self.rules = [r for r in self.rules if r["id"] != payload.get("id")]
            return {"ok": True, "rules": list(self.rules)}
        if cmd == "setRuleEnabled":
            for rule in self.rules:
                if rule["id"] == payload.get("id"):
                    rule["enabled"] = payload.get("enabled", True)
            return {"ok": True, "rules": list(self.rules)}
        if cmd in {"pendingMessages"}:
            return {"ok": True, "pending": []}
        if cmd in {"listDeferred"}:
            return {"ok": True, "deferred": []}
        if cmd == "listAccounts":
            return {"ok": True, "accounts": []}
        return {"ok": True, "cmd": cmd, "payload": payload}


@pytest.fixture(autouse=True)
def reset_service():
    original = api.service
    original_token = getattr(api.app.state, "api_token", None)
    api.service = None
    api.app.state.api_token = None
    try:
        yield
    finally:
        api.service = original
        api.app.state.api_token = original_token


@pytest.fixture
def client_and_service():
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})
    return client, svc


def test_returns_500_when_service_missing():
    create_app(DummyService(), api_token=API_TOKEN)
    api.service = None
    client = TestClient(api.app)
    response = client.post("/commands/run-now", headers={API_TOKEN_HEADER_NAME: API_TOKEN})
    assert response.status_code == 500
    assert response.json()["detail"] == "Service not initialized"


def test_rejects_missing_token():
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    response = client.get("/status")
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid or missing API token"


def test_basic_endpoints_dispatch_to_service(client_and_service):
    client, svc = client_and_service

    assert client.get("/status").json() == {"ok": True}

    assert client.post("/commands/run-now").json()["ok"] is True
    assert client.post("/commands/suspend").json()["ok"] is True
    assert client.post("/commands/activate").json()["ok"] is True

    rule_payload = {
        "name": "peak",
        "days": [1, 2],
        "start_hour": 9,
        "end_hour": 10,
        "interval_minutes": 5,
    }
    rule_resp = client.post("/commands/rules", json=rule_payload)
    assert rule_resp.json()["ok"] is True
    rule_id = rule_resp.json()["rules"][0]["id"]
    assert client.get("/commands/rules").json()["ok"] is True
    assert client.patch(f"/commands/rules/{rule_id}", json={"enabled": False}).json()["ok"] is True
    assert client.delete(f"/commands/rules/{rule_id}").json()["ok"] is True

    send_payload = {
        "id": "msg1",
        "account_id": "acc",
        "from": "sender@example.com",
        "to": ["dest@example.com"],
        "subject": "Hello",
        "body": "Hi",
        "attachments": [
            {"filename": "doc.txt", "content": "ZGF0YQ=="},
            {"filename": "remote.bin", "url": "https://files"},
        ],
    }
    resp = client.post("/commands/send-message", json=send_payload)
    assert resp.status_code == 200
    assert resp.json()["result"]["status"] == "sent"

    bulk_payload = {
        "messages": [
            {
                "id": "msg-bulk",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Bulk",
                "body": "Bulk body",
            }
        ]
    }
    bulk_resp = client.post("/commands/add-messages", json=bulk_payload)
    assert bulk_resp.status_code == 200
    assert bulk_resp.json()["queued"] == 1

    account = {"id": "acc", "host": "smtp.local", "port": 25}
    assert client.post("/account", json=account).json()["ok"] is True
    assert client.get("/accounts").json()["ok"] is True
    assert client.delete("/account/acc").json()["ok"] is True
    assert client.get("/pending").json()["ok"] is True
    assert client.get("/deferred").json()["ok"] is True

    expected_calls = [
        ("run now", {}),
        ("suspend", {}),
        ("activate", {}),
        ("addRule", {"name": "peak", "enabled": True, "days": [1, 2], "start_hour": 9, "end_hour": 10, "cross_midnight": False, "interval_minutes": 5}),
        ("listRules", {}),
        ("setRuleEnabled", {"id": rule_id, "enabled": False}),
        ("deleteRule", {"id": rule_id}),
        (
            "sendMessage",
            {
                "id": "msg1",
                "account_id": "acc",
                "from": "sender@example.com",
                "to": ["dest@example.com"],
                "subject": "Hello",
                "body": "Hi",
                "content_type": "plain",
                "attachments": [
                    {"filename": "doc.txt", "content": "ZGF0YQ=="},
                    {"filename": "remote.bin", "url": "https://files"},
                ],
            },
        ),
        (
            "addMessages",
            {
                "messages": [
                    {
                        "id": "msg-bulk",
                        "from": "sender@example.com",
                        "to": ["dest@example.com"],
                        "subject": "Bulk",
                        "body": "Bulk body",
                        "content_type": "plain",
                    }
                ]
            },
        ),
        ("addAccount", {"id": "acc", "host": "smtp.local", "port": 25, "user": None, "password": None, "ttl": 300, "limit_per_minute": None, "limit_per_hour": None, "limit_per_day": None, "limit_behavior": "defer", "use_tls": None}),
        ("listAccounts", {}),
        ("deleteAccount", {"id": "acc"}),
        ("pendingMessages", {}),
        ("listDeferred", {}),
    ]
    assert svc.calls == expected_calls


def test_metrics_endpoint_uses_service_metrics():
    svc = DummyService()
    client = TestClient(create_app(svc, api_token=API_TOKEN))
    client.headers.update({API_TOKEN_HEADER_NAME: API_TOKEN})

    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.text == "metrics-data"
