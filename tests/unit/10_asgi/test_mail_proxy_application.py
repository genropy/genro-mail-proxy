# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Phase-1 proof of the genro-asgi transport: MailProxyApplication over HTTP.

Implementation tests (they photograph the new transport layer, not the v1
contract — that one is asserted by ``tests/local_e2e``). A probe subclass adds
gated routes so the auth matrix, the request injection and the 422 mapping are
asserted against a real server on a free port, with real tokens in a real
SQLite tenants table.
"""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio
import uvicorn
from genro_asgi import AsgiServer
from genro_routes import route

from mail_proxy.core import MailProxy
from mail_proxy.mail_proxy_application import ADMIN_TAG, TENANT_TAG, MailProxyApplication

httpx = pytest.importorskip("httpx")

ADMIN_TOKEN = "asgi-admin-token"
TENANT_ID = "asgi-probe-tenant"


class ProbeApplication(MailProxyApplication):
    """MailProxyApplication plus throwaway routes that expose the seams."""

    @route(auth_rule=ADMIN_TAG)
    async def probe_admin(self) -> dict[str, str]:
        return {"gate": "admin"}

    @route(auth_rule=TENANT_TAG)
    async def probe_tenant(self, request) -> dict[str, str]:
        return {"gate": "tenant", "method": request.method}

    @route(auth_rule=TENANT_TAG)
    async def probe_typed(self, count: int) -> dict[str, int]:
        return {"count": count}


@pytest.fixture(scope="module")
def asgi_server(tmp_path_factory):
    """The probe app served by AsgiServer/uvicorn on a free port."""
    db_path = tmp_path_factory.mktemp("asgi") / "mail_proxy.db"
    core = MailProxy(db_path=str(db_path), start_active=True, test_mode=True)
    app = ProbeApplication(core=core, api_token=ADMIN_TOKEN)
    server = AsgiServer(applications=[app], middleware={"auth": False})

    config = uvicorn.Config(server, host="127.0.0.1", port=0, log_level="warning")
    uv_server = uvicorn.Server(config)
    thread = threading.Thread(target=uv_server.run, name="asgi-test-uvicorn", daemon=True)
    thread.start()

    deadline = time.monotonic() + 30
    while not uv_server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("asgi test server did not start within 30s")
        time.sleep(0.05)
    port = uv_server.servers[0].sockets[0].getsockname()[1]

    yield SimpleNamespace(base_url=f"http://127.0.0.1:{port}", core=core)

    uv_server.should_exit = True
    thread.join(timeout=30)


@pytest_asyncio.fixture
async def client(asgi_server):
    """Bare httpx client; each test sets its own token header."""
    async with httpx.AsyncClient(base_url=asgi_server.base_url, timeout=10.0) as cli:
        yield cli


@pytest_asyncio.fixture
async def tenant_token(asgi_server):
    """A real tenant with a real api key in the SQLite tenants table."""
    db = asgi_server.core.db
    existing = await db.get_tenant(TENANT_ID)
    if not existing:
        await db.add_tenant({"id": TENANT_ID, "name": "Probe Tenant"})
    raw_key = await db.tenants.create_api_key(TENANT_ID)
    assert raw_key
    return raw_key


def _auth(token: str) -> dict[str, str]:
    return {"X-API-Token": token}


class TestHealth:
    @pytest.mark.asyncio
    async def test_health_answers_the_v1_body_without_auth(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestAuthMatrix:
    @pytest.mark.asyncio
    async def test_gated_route_without_token_is_401(self, client):
        resp = await client.get("/probe_admin")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_admin_token_passes_the_admin_gate(self, client):
        resp = await client.get("/probe_admin", headers=_auth(ADMIN_TOKEN))
        assert resp.status_code == 200
        assert resp.json() == {"gate": "admin"}

    @pytest.mark.asyncio
    async def test_tenant_token_on_the_admin_gate_is_403(self, client, tenant_token):
        resp = await client.get("/probe_admin", headers=_auth(tenant_token))
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_tenant_token_passes_the_tenant_gate(self, client, tenant_token):
        resp = await client.get("/probe_tenant", headers=_auth(tenant_token))
        assert resp.status_code == 200
        assert resp.json()["gate"] == "tenant"

    @pytest.mark.asyncio
    async def test_admin_token_passes_the_tenant_gate_too(self, client):
        resp = await client.get("/probe_tenant", headers=_auth(ADMIN_TOKEN))
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_unknown_token_is_anonymous_hence_401(self, client):
        resp = await client.get("/probe_tenant", headers=_auth("no-such-token"))
        assert resp.status_code == 401


class TestTransportSeams:
    @pytest.mark.asyncio
    async def test_handler_declaring_request_receives_it(self, client):
        resp = await client.get("/probe_tenant", headers=_auth(ADMIN_TOKEN))
        assert resp.json()["method"] == "GET"

    @pytest.mark.asyncio
    async def test_invalid_arguments_answer_422_like_v1(self, client):
        resp = await client.get("/probe_typed?count=not-a-number", headers=_auth(ADMIN_TOKEN))
        assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_typed_argument_binds(self, client):
        resp = await client.get("/probe_typed?count=3", headers=_auth(ADMIN_TOKEN))
        assert resp.status_code == 200
        assert resp.json() == {"count": 3}
