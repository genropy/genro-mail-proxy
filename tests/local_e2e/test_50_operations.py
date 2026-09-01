# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The six operational routes, over HTTP against the in-process app.

Prometheus metrics, the singleton instance configuration in read and write,
the bounce reload, and the two command-log reads.

``PUT /instance`` and ``POST /instance/reload-bounce`` are the pair the stress
suite reaches only through ``configure_bounce_receiver``, which skips on a
machine without Dovecot — there, those two routes are never called at all.
Here they run everywhere: a fresh SQLite database has bounce disabled, so
``reload-bounce`` takes its "not enabled" branch and there is no receiver to
tear down.
"""

from __future__ import annotations

import time
import uuid

import pytest

from tests import api_routes

pytestmark = pytest.mark.asyncio


class TestMetrics:
    """GET /metrics — the Prometheus exposition, with no token of its own."""

    async def test_metrics_answers_the_prometheus_media_type(self, api_client):
        resp = await api_client.get(api_routes.METRICS)
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")

    async def test_metrics_needs_no_token(self, bare_client):
        resp = await bare_client.get(api_routes.METRICS)
        assert resp.status_code == 200


class TestInstanceRead:
    """GET /instance — the singleton row, minus what must not travel."""

    async def test_instance_carries_the_bounce_configuration(self, api_client):
        resp = await api_client.get(api_routes.INSTANCE)
        assert resp.status_code == 200
        body = resp.json()
        assert body["id"] == 1
        assert isinstance(body["bounce_enabled"], bool)
        assert "bounce_imap_port" in body

    async def test_instance_never_returns_the_bounce_password(self, api_client):
        resp = await api_client.put(
            api_routes.INSTANCE, json={"bounce_imap_password": "the-imap-password"}
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.INSTANCE)
        assert resp.status_code == 200
        assert "bounce_imap_password" not in resp.json()
        assert "the-imap-password" not in resp.text


class TestInstanceUpdate:
    """PUT /instance — partial, and the booleans survive the round trip."""

    async def test_update_stores_what_it_names(self, api_client):
        name = f"local-instance-{uuid.uuid4().hex[:8]}"
        resp = await api_client.put(api_routes.INSTANCE, json={"name": name})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

        resp = await api_client.get(api_routes.INSTANCE)
        assert resp.json()["name"] == name

    async def test_the_booleans_survive_the_round_trip(self, api_client):
        """They are stored as integers and answered as booleans."""
        resp = await api_client.put(
            api_routes.INSTANCE, json={"bounce_imap_ssl": False}
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.INSTANCE)
        assert resp.json()["bounce_imap_ssl"] is False

        resp = await api_client.put(api_routes.INSTANCE, json={"bounce_imap_ssl": True})
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.INSTANCE)
        assert resp.json()["bounce_imap_ssl"] is True

    async def test_update_leaves_the_fields_it_does_not_name(self, api_client):
        name = f"local-instance-{uuid.uuid4().hex[:8]}"
        await api_client.put(api_routes.INSTANCE, json={"name": name})

        resp = await api_client.put(api_routes.INSTANCE, json={"bounce_poll_interval": 90})
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.INSTANCE)
        body = resp.json()
        assert body["bounce_poll_interval"] == 90
        assert body["name"] == name


class TestReloadBounce:
    """POST /instance/reload-bounce — applies what PUT /instance stored."""

    async def test_reload_answers_ok_while_bounce_is_disabled(self, api_client):
        resp = await api_client.put(
            api_routes.INSTANCE, json={"bounce_enabled": False}
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.post(api_routes.INSTANCE_RELOAD_BOUNCE)
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True

    async def test_reload_refuses_bounce_enabled_without_a_host(self, api_client):
        resp = await api_client.put(
            api_routes.INSTANCE,
            json={"bounce_enabled": True, "bounce_imap_host": ""},
        )
        assert resp.status_code == 200, resp.text
        try:
            resp = await api_client.post(api_routes.INSTANCE_RELOAD_BOUNCE)
            assert resp.status_code == 400
        finally:
            await api_client.put(api_routes.INSTANCE, json={"bounce_enabled": False})
            await api_client.post(api_routes.INSTANCE_RELOAD_BOUNCE)


class TestCommandLog:
    """GET /command-log — the audit trail of the state-modifying commands."""

    async def test_the_trail_records_a_logged_command(self, api_client, throwaway_tenant):
        batch_code = f"batch-{uuid.uuid4().hex[:8]}"
        resp = await api_client.post(
            api_routes.suspend(throwaway_tenant, batch_code=batch_code)
        )
        assert resp.status_code == 200, resp.text

        resp = await api_client.get(api_routes.command_log(tenant_id=throwaway_tenant))
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True

        entries = body["commands"]
        assert entries, "the suspend was not logged"
        entry = entries[0]
        assert entry["tenant_id"] == throwaway_tenant
        assert entry["response_status"] == 200
        assert entry["payload"]["batch_code"] == batch_code

    async def test_the_endpoint_column_holds_the_command_name(
        self, api_client, throwaway_tenant
    ):
        """Characterization: ``endpoint`` is the internal command, not a path.

        ``entities/command_log/table.py`` documents it as "HTTP method + path";
        the value stored is ``suspend``. Pinned as it is — src/ is untouched by
        this workflow, and the 0.7.7 swap must not change it either way.
        """
        await api_client.post(api_routes.suspend(throwaway_tenant))

        resp = await api_client.get(api_routes.command_log(tenant_id=throwaway_tenant))
        assert "suspend" in [e["endpoint"] for e in resp.json()["commands"]]

    async def test_total_counts_the_returned_page(self, api_client, throwaway_tenant):
        """Characterization: ``total`` is ``len(commands)``, not the whole trail."""
        for _ in range(3):
            await api_client.post(api_routes.suspend(throwaway_tenant))

        resp = await api_client.get(
            api_routes.command_log(tenant_id=throwaway_tenant, limit=2)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["commands"]) == 2
        assert body["total"] == 2

    async def test_the_tenant_filter_scopes_the_trail(
        self, api_client, throwaway_tenant
    ):
        await api_client.post(api_routes.suspend(throwaway_tenant))

        resp = await api_client.get(api_routes.command_log(tenant_id=throwaway_tenant))
        tenants = {e["tenant_id"] for e in resp.json()["commands"]}
        assert tenants == {throwaway_tenant}

    async def test_the_endpoint_filter_binds(self, api_client, throwaway_tenant):
        await api_client.post(api_routes.suspend(throwaway_tenant))
        await api_client.post(api_routes.activate(throwaway_tenant))

        resp = await api_client.get(
            api_routes.command_log(
                tenant_id=throwaway_tenant, endpoint_filter="activate"
            )
        )
        assert resp.status_code == 200
        endpoints = {e["endpoint"] for e in resp.json()["commands"]}
        assert endpoints == {"activate"}

    async def test_the_time_window_binds(self, api_client, throwaway_tenant):
        before = int(time.time()) - 5
        await api_client.post(api_routes.suspend(throwaway_tenant))

        resp = await api_client.get(
            api_routes.command_log(tenant_id=throwaway_tenant, since_ts=before)
        )
        assert resp.json()["commands"], "the window dropped a command inside it"

        resp = await api_client.get(
            api_routes.command_log(tenant_id=throwaway_tenant, until_ts=before)
        )
        assert resp.json()["commands"] == []


class TestCommandLogExport:
    """GET /command-log/export — the same trail, replay-shaped."""

    async def test_export_answers_an_object_carrying_the_commands(
        self, api_client, throwaway_tenant
    ):
        """Characterization: the answer is ``{"ok", "commands"}``, not a list.

        Its docstring promises "List of replay-ready command objects".
        """
        await api_client.post(api_routes.suspend(throwaway_tenant))

        resp = await api_client.get(
            api_routes.command_log_export(tenant_id=throwaway_tenant)
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert isinstance(body["commands"], list)
        assert body["commands"], "the suspend is missing from the export"

    async def test_the_export_tenant_filter_binds(self, api_client, throwaway_tenant):
        await api_client.post(api_routes.suspend(throwaway_tenant))

        resp = await api_client.get(
            api_routes.command_log_export(tenant_id=throwaway_tenant)
        )
        assert resp.status_code == 200
        commands = resp.json()["commands"]
        assert commands, "the suspend is missing from the filtered export"
        assert {c["tenant_id"] for c in commands} == {throwaway_tenant}
