# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Process-level API guards, the three behaviours no e2e test can reach.

Every other assertion of the former tests/unit/10_api/test_00_api.py lives in
tests/fullstack, exercised over HTTP against a running proxy. These three
cannot: a live server always has a service, and the stack's proxy always
carries an API token.

- ``service is None`` -> 500 "Service not initialized" on every route. The
  app that server.py builds always receives a service, so no HTTP request
  reaches that branch.
- ``api_token=None`` -> every request allowed. The compose stack's proxy is
  always started with a token.

This is the one FastAPI-coupled test site left in the suite. The 0.7.7
transport swap edits it knowingly, the way it edits tests/fullstack/api_routes.py.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from mail_proxy import api
from mail_proxy.api import API_TOKEN_HEADER_NAME, create_app

API_TOKEN = "secret-token"


class DummyService:
    """The smallest service create_app accepts."""

    def __init__(self):
        self._active = True

    async def handle_command(self, cmd, payload):
        return {"ok": True, "cmd": cmd, "payload": payload}


@pytest.fixture(autouse=True)
def reset_service():
    """Restore the module-level service and token the guards manipulate."""
    original = api.service
    original_token = getattr(api.app.state, "api_token", None)
    api.service = None
    api.app.state.api_token = None
    try:
        yield
    finally:
        api.service = original
        api.app.state.api_token = original_token


class TestServiceNotInitialized:
    """Routes answer 500 while the module-level service is unset."""

    def test_returns_500_when_service_missing(self):
        create_app(DummyService(), api_token=API_TOKEN)
        api.service = None
        client = TestClient(api.app)

        response = client.post(
            "/commands/run-now", headers={API_TOKEN_HEADER_NAME: API_TOKEN}
        )
        assert response.status_code == 500
        assert response.json()["detail"] == "Service not initialized"

    def test_every_command_route_returns_500(self):
        create_app(DummyService(), api_token=API_TOKEN)
        api.service = None
        client = TestClient(api.app)
        headers = {API_TOKEN_HEADER_NAME: API_TOKEN}

        routes = [
            ("POST", "/commands/run-now", None),
            ("POST", "/commands/suspend?tenant_id=test-tenant", None),
            ("POST", "/commands/activate?tenant_id=test-tenant", None),
            ("POST", "/commands/add-messages", {"messages": []}),
            ("POST", "/commands/delete-messages?tenant_id=test-tenant", {"ids": []}),
            ("POST", "/commands/cleanup-messages?tenant_id=test-tenant", {}),
            ("POST", "/account", {"id": "a", "host": "h", "port": 25}),
            ("GET", "/accounts?tenant_id=test-tenant", None),
            ("DELETE", "/account/test?tenant_id=test-tenant", None),
            ("GET", "/messages?tenant_id=test-tenant", None),
            ("GET", "/metrics", None),
        ]

        for method, path, body in routes:
            if method == "GET":
                response = client.get(path, headers=headers)
            elif method == "POST":
                response = client.post(path, json=body, headers=headers)
            else:
                response = client.delete(path, headers=headers)

            assert response.status_code == 500, f"{method} {path}"
            assert response.json()["detail"] == "Service not initialized"


class TestNoTokenConfigured:
    """An app built without a token lets every request through."""

    def test_no_token_configured_allows_access(self):
        client = TestClient(create_app(DummyService(), api_token=None))

        response = client.get("/status")
        assert response.status_code == 200
        assert response.json()["ok"] is True
