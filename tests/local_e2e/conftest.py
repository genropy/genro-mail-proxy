# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Local e2e harness: the real app over real HTTP, no Docker.

Builds the same application src/mail_proxy/server.py builds — MailProxy plus
create_app — on a temporary SQLite file, and serves it with uvicorn on a free
port in a background thread. test_mode=True parks the dispatch and reporting
loops, so no traffic runs behind the tests: this suite asserts the HTTP
contract, never delivery. The Docker fullstack suite remains the stress suite.
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
import uvicorn

from mail_proxy.api import create_app
from mail_proxy.core import MailProxy
from tests import api_routes

httpx = pytest.importorskip("httpx")

LOCAL_API_TOKEN = "local-e2e-token"

# The discard port: every connection to it is refused at once. Accounts and
# client callbacks point here so that a dispatch cycle woken by run-now fails
# immediately and locally, instead of reaching the network.
UNROUTABLE_HOST = "127.0.0.1"
UNROUTABLE_PORT = 9
UNROUTABLE_URL = f"http://{UNROUTABLE_HOST}:{UNROUTABLE_PORT}"


@pytest.fixture(scope="session")
def local_server(tmp_path_factory):
    """The real app served by uvicorn on a free port, for the whole session.

    Yields base_url and db_path; teardown stops the server, joins its thread
    and removes the SQLite file with its WAL/SHM siblings.
    """
    db_path = tmp_path_factory.mktemp("local_e2e") / "mail_proxy.db"
    core = MailProxy(db_path=str(db_path), start_active=True, test_mode=True)

    @asynccontextmanager
    async def lifespan(app):
        await core.start()
        try:
            yield
        finally:
            await core.stop()

    app = create_app(core, api_token=LOCAL_API_TOKEN, lifespan=lifespan)
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, name="local-e2e-uvicorn", daemon=True)
    thread.start()

    deadline = time.monotonic() + 30
    while not server.started:
        if time.monotonic() > deadline or not thread.is_alive():
            raise RuntimeError("local e2e server did not start within 30s")
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]

    yield SimpleNamespace(base_url=f"http://127.0.0.1:{port}", db_path=db_path)

    server.should_exit = True
    thread.join(timeout=30)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)


@pytest_asyncio.fixture
async def api_client(local_server):
    """Authenticated httpx client against the local server."""
    async with httpx.AsyncClient(
        base_url=local_server.base_url,
        headers={"X-API-Token": LOCAL_API_TOKEN},
        timeout=10.0,
    ) as client:
        yield client


@pytest_asyncio.fixture
async def bare_client(local_server):
    """Unauthenticated httpx client, for the routes that must answer 401."""
    async with httpx.AsyncClient(
        base_url=local_server.base_url, timeout=10.0
    ) as client:
        yield client


@pytest_asyncio.fixture
async def local_tenants(api_client):
    """The two shared tenants, each with one SMTP account.

    Fixed ids, because ``POST /tenant`` upserts: taking the fixture twice in a
    session leaves one tenant, not two. Every address is 127.0.0.1:9 — the
    discard port refuses instantly, so a dispatch cycle woken by run-now fails
    locally and sends nothing off the machine.
    """
    for index in (1, 2):
        tenant_id = f"local-tenant-{index}"
        resp = await api_client.post(
            api_routes.TENANT,
            json={
                "id": tenant_id,
                "name": f"Local Tenant {index}",
                "client_base_url": UNROUTABLE_URL,
                "client_sync_path": api_routes.CLIENT_SYNC_PATH,
                "client_auth": {"method": "none"},
                "active": True,
            },
        )
        assert resp.status_code in (200, 201), resp.text

        resp = await api_client.post(
            api_routes.ACCOUNT,
            json={
                "id": f"local-account-{index}",
                "tenant_id": tenant_id,
                "host": UNROUTABLE_HOST,
                "port": UNROUTABLE_PORT,
                "use_tls": False,
            },
        )
        assert resp.status_code in (200, 201), resp.text

    return {
        "tenant1": "local-tenant-1",
        "tenant2": "local-tenant-2",
        "account1": "local-account-1",
        "account2": "local-account-2",
    }


@pytest_asyncio.fixture
async def throwaway_tenant(api_client):
    """A tenant of its own, removed at teardown.

    The id carries a uuid, not the clock: several tests take this fixture
    within the same second, and ``POST /tenant`` upserts — a clock-based id
    would let them share one tenant and one command-log slice.
    """
    tenant_id = f"throwaway-{uuid.uuid4().hex[:12]}"
    resp = await api_client.post(
        api_routes.TENANT,
        json={
            "id": tenant_id,
            "name": f"Throwaway {tenant_id}",
            "client_base_url": UNROUTABLE_URL,
            "active": True,
        },
    )
    assert resp.status_code in (200, 201), resp.text

    yield tenant_id

    await api_client.delete(api_routes.tenant(tenant_id))
