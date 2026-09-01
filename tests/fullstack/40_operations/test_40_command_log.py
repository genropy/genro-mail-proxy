# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The command-log audit trail, read from outside.

Two admin routes: ``GET /command-log`` lists what the state-modifying commands
recorded, ``GET /command-log/export`` returns the same trail trimmed to the
fields a replay needs.

Every test works on a tenant of its own, created and removed by
``logged_tenant``: the log is global and the whole suite writes to it, so a
filter on a shared tenant id would read entries other tests left behind. A
tenant nobody else knows makes ``tenant_id`` an exact selector.
"""

from __future__ import annotations

import time
import uuid

import httpx
import pytest
import pytest_asyncio

from tests import api_routes
from tests.fullstack.helpers import MAILPROXY_URL

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


@pytest_asyncio.fixture
async def logged_tenant(api_client):
    """A throwaway tenant whose log holds exactly one suspend and one activate.

    ``suspend`` and ``activate`` are in the proxy's logged-command set and are
    the only two that carry ``tenant_id`` in their payload without moving a
    message, which is what makes them findable and harmless. The batch code is
    unique per run, so the suspension this creates and releases touches nothing
    else.

    ``addTenant`` and ``deleteTenant`` are logged too, but their payload names
    the tenant ``id``, not ``tenant_id``, so they are recorded with no tenant
    and stay out of the filtered slice.

    The id comes from a uuid, not from the clock: several tests take this
    fixture within the same second, and ``POST /tenant`` upserts, so a
    second-grained id would give them one shared tenant and one shared log.
    """
    unique = uuid.uuid4().hex[:8]
    tenant_id = f"cmdlog-tenant-{unique}"
    batch_code = f"cmdlog-batch-{unique}"

    resp = await api_client.post(
        api_routes.TENANT,
        json={"id": tenant_id, "name": f"Command Log Tenant {unique}"},
    )
    assert resp.status_code in (200, 201), resp.text

    resp = await api_client.post(
        api_routes.suspend(tenant_id=tenant_id, batch_code=batch_code)
    )
    assert resp.status_code == 200, resp.text
    resp = await api_client.post(
        api_routes.activate(tenant_id=tenant_id, batch_code=batch_code)
    )
    assert resp.status_code == 200, resp.text

    yield {"tenant_id": tenant_id, "batch_code": batch_code}

    await api_client.delete(api_routes.tenant(tenant_id))


class TestCommandLog:
    """GET /command-log — the audit trail of the state-modifying commands."""

    async def test_the_log_records_endpoint_payload_and_status(
        self, api_client, logged_tenant
    ):
        """Each entry carries what the command was, what it was given, how it went."""
        resp = await api_client.get(
            api_routes.command_log(tenant_id=logged_tenant["tenant_id"])
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        commands = data["commands"]
        assert [c["endpoint"] for c in commands] == ["suspend", "activate"]

        for entry in commands:
            assert entry["tenant_id"] == logged_tenant["tenant_id"]
            assert entry["payload"]["batch_code"] == logged_tenant["batch_code"]
            assert entry["response_status"] == 200
            assert isinstance(entry["id"], int)
            assert isinstance(entry["command_ts"], int)

    async def test_endpoint_holds_the_command_name_not_the_http_path(
        self, api_client, logged_tenant
    ):
        """Characterization: the ``endpoint`` column holds ``suspend``.

        ``entities/command_log/table.py`` documents it as "HTTP method + path"
        (e.g. ``POST /commands/suspend``); what the proxy writes is the internal
        command name. Asserted as it is, so a transport swap cannot change it
        unnoticed.
        """
        resp = await api_client.get(
            api_routes.command_log(tenant_id=logged_tenant["tenant_id"])
        )
        endpoints = [c["endpoint"] for c in resp.json()["commands"]]
        assert endpoints == ["suspend", "activate"]
        assert not any("/" in endpoint for endpoint in endpoints)

    async def test_endpoint_filter_selects_one_command_family(
        self, api_client, logged_tenant
    ):
        """The filter is a partial match on the command name."""
        resp = await api_client.get(
            api_routes.command_log(
                tenant_id=logged_tenant["tenant_id"], endpoint_filter="suspend"
            )
        )
        assert resp.status_code == 200
        assert [c["endpoint"] for c in resp.json()["commands"]] == ["suspend"]

    async def test_total_counts_the_page_not_the_whole_trail(
        self, api_client, logged_tenant
    ):
        """Characterization: ``limit`` bounds the page and ``total`` counts it.

        The tenant has two entries; asking for one returns one and reports
        ``total: 1``. The field is not the size of the trail behind the page.
        """
        resp = await api_client.get(
            api_routes.command_log(tenant_id=logged_tenant["tenant_id"], limit=1)
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["commands"]) == 1
        assert data["total"] == 1

    async def test_since_ts_in_the_future_returns_nothing(
        self, api_client, logged_tenant
    ):
        future = int(time.time()) + 3600
        resp = await api_client.get(
            api_routes.command_log(
                tenant_id=logged_tenant["tenant_id"], since_ts=future
            )
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["commands"] == []
        assert data["total"] == 0

    async def test_until_ts_in_the_past_returns_nothing(
        self, api_client, logged_tenant
    ):
        resp = await api_client.get(
            api_routes.command_log(tenant_id=logged_tenant["tenant_id"], until_ts=1)
        )
        assert resp.status_code == 200
        assert resp.json()["commands"] == []

    async def test_tenant_id_filter_excludes_every_other_tenant(
        self, api_client, logged_tenant, setup_test_tenants
    ):
        """The unfiltered trail carries other tenants; the filtered one does not."""
        tenant_id = logged_tenant["tenant_id"]

        resp = await api_client.get(api_routes.command_log(limit=500))
        assert resp.status_code == 200
        others = {c.get("tenant_id") for c in resp.json()["commands"]} - {tenant_id}
        assert others, "the whole trail knows one tenant only, the filter proves nothing"

        resp = await api_client.get(
            api_routes.command_log(tenant_id=tenant_id, limit=500)
        )
        commands = resp.json()["commands"]
        assert commands, f"no log entry for {tenant_id}"
        assert {c.get("tenant_id") for c in commands} == {tenant_id}


class TestCommandLogExport:
    """GET /command-log/export — the same trail, trimmed for replay."""

    async def test_export_carries_only_the_replay_fields(
        self, api_client, logged_tenant
    ):
        """Four fields per entry: what to replay, on whom, with what, and when."""
        resp = await api_client.get(
            api_routes.command_log_export(tenant_id=logged_tenant["tenant_id"])
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True

        commands = data["commands"]
        assert [c["endpoint"] for c in commands] == ["suspend", "activate"]
        for entry in commands:
            assert set(entry) == {"endpoint", "tenant_id", "payload", "command_ts"}
            assert entry["tenant_id"] == logged_tenant["tenant_id"]
            assert entry["payload"]["batch_code"] == logged_tenant["batch_code"]

    async def test_export_answers_an_object_not_a_bare_list(
        self, api_client, logged_tenant
    ):
        """Characterization: the body is ``{"ok": ..., "commands": [...]}``.

        ``export_command_log``'s docstring promises "List of replay-ready
        command objects"; the handler wraps them in an object. Pinned as it is.
        """
        resp = await api_client.get(
            api_routes.command_log_export(tenant_id=logged_tenant["tenant_id"])
        )
        assert isinstance(resp.json(), dict)

    async def test_export_since_ts_in_the_future_returns_nothing(
        self, api_client, logged_tenant
    ):
        future = int(time.time()) + 3600
        resp = await api_client.get(
            api_routes.command_log_export(
                tenant_id=logged_tenant["tenant_id"], since_ts=future
            )
        )
        assert resp.status_code == 200
        assert resp.json()["commands"] == []


class TestCommandLogIsAdminOnly:
    """The audit trail is the whole instance's, so no tenant may read it."""

    async def test_no_token_is_rejected(self):
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(api_routes.command_log())
            assert resp.status_code == 401

    async def test_export_without_a_token_is_rejected(self):
        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(api_routes.command_log_export())
            assert resp.status_code == 401

    async def test_a_tenant_key_is_refused(self, api_client, logged_tenant):
        """A valid tenant key answers 403, not 401: the token is real, the scope is not."""
        resp = await api_client.post(
            api_routes.tenant_api_key(logged_tenant["tenant_id"])
        )
        assert resp.status_code == 200, resp.text
        tenant_token = resp.json()["api_key"]

        async with httpx.AsyncClient(base_url=MAILPROXY_URL) as client:
            resp = await client.get(
                api_routes.command_log(), headers={"X-API-Token": tenant_token}
            )
            assert resp.status_code == 403
