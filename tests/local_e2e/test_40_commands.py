# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The three scheduler-command routes, over HTTP against the in-process app.

``run-now`` wakes the dispatch cycle, ``suspend`` holds a tenant or one of its
batches, ``activate`` releases what suspend held. Only the HTTP contract is
asserted: run-now answers ``ok`` and the suspension state is read back from
the answer, never from a message that was or was not delivered — this suite
has no SMTP server.
"""

from __future__ import annotations

import uuid

import pytest

from tests import api_routes

pytestmark = pytest.mark.asyncio


class TestRunNow:
    """POST /commands/run-now — the manual dispatch trigger."""

    async def test_run_now_answers_ok(self, api_client):
        resp = await api_client.post(api_routes.run_now())
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

    async def test_run_now_ignores_the_tenant_query(self, api_client, local_tenants):
        """Characterization: the route reads its tenant from the token only.

        ``run_now`` takes no ``tenant_id`` parameter — it deduces one from the
        API token — so the query string ``api_routes.run_now(tenant_id)``
        builds is accepted and discarded. Recorded as a Phase 5 finding and
        asserted here so the 0.7.7 swap cannot change it unnoticed.
        """
        resp = await api_client.post(api_routes.run_now(local_tenants["tenant1"]))
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True


class TestSuspendAndActivate:
    """POST /commands/suspend and /commands/activate."""

    async def test_suspend_holds_the_whole_tenant(self, api_client, throwaway_tenant):
        resp = await api_client.post(api_routes.suspend(throwaway_tenant))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["tenant_id"] == throwaway_tenant
        assert isinstance(body["pending_messages"], int)

    async def test_activate_releases_what_suspend_held(
        self, api_client, throwaway_tenant
    ):
        await api_client.post(api_routes.suspend(throwaway_tenant))

        resp = await api_client.post(api_routes.activate(throwaway_tenant))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["suspended_batches"] == []

    async def test_a_single_batch_can_be_held_and_released(
        self, api_client, throwaway_tenant
    ):
        batch_code = f"batch-{uuid.uuid4().hex[:8]}"

        resp = await api_client.post(
            api_routes.suspend(throwaway_tenant, batch_code=batch_code)
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["batch_code"] == batch_code
        assert batch_code in body["suspended_batches"]

        resp = await api_client.post(
            api_routes.activate(throwaway_tenant, batch_code=batch_code)
        )
        assert resp.status_code == 200, resp.text
        assert batch_code not in resp.json()["suspended_batches"]

    async def test_suspend_of_an_unknown_tenant_is_refused(self, api_client):
        resp = await api_client.post(api_routes.suspend("no-such-tenant"))
        assert resp.status_code == 400

    async def test_activate_of_an_unknown_tenant_is_refused(self, api_client):
        resp = await api_client.post(api_routes.activate("no-such-tenant"))
        assert resp.status_code == 400

    async def test_suspend_requires_tenant_id(self, api_client):
        resp = await api_client.post(api_routes.suspend())
        assert resp.status_code == 422

    async def test_activate_requires_tenant_id(self, api_client):
        resp = await api_client.post(api_routes.activate())
        assert resp.status_code == 422
