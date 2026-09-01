# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fixtures for the outbound client-protocol tests.

Every assertion in this group reads the fake client's call history, so each
test starts from a client whose history is empty AND whose tenant has no
report cycle still in flight: a leftover cycle would land its call among the
ones the test is counting. ``quiet_tenant1`` and ``quiet_tenant2`` do both —
drain, then reset.

The helpers the tests share arrive as fixtures rather than imports: the group
directory starts with a digit, so ``35_client_protocol`` is not an importable
package name.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest
import pytest_asyncio

from tests import api_routes
from tests.fullstack.helpers import (
    CLIENT_TENANT1_URL,
    CLIENT_TENANT2_URL,
    MAILPROXY_URL,
    ClientControl,
)

httpx = pytest.importorskip("httpx")


DRAIN_SAMPLE_SECONDS = 1.0
DRAIN_MAX_SAMPLES = 20
DRAIN_MAX_SECONDS = 25.0


async def _drain_and_reset(trigger, control: ClientControl) -> None:
    """Let the pending report cycle finish, then empty the call history.

    Runs one cycle and waits for the history to stop growing. Without it a
    test measuring "exactly one call" can count a cycle an earlier group left
    in flight.

    Bounded on both axes: a report cycle firing at or under the sample period
    keeps the count growing for ever, and pytest.ini configures no timeout, so
    an unbounded wait would hang the run instead of failing it.
    """
    await trigger()
    previous = -1
    current = -1
    settled = False
    samples = 0
    deadline = time.monotonic() + DRAIN_MAX_SECONDS
    while samples < DRAIN_MAX_SAMPLES and time.monotonic() < deadline:
        await asyncio.sleep(DRAIN_SAMPLE_SECONDS)
        samples += 1
        current = len(await control.get_call_history())
        if current == previous:
            settled = True
            break
        previous = current
    if not settled:
        pytest.fail(
            f"the client call history never settled: {current} calls after "
            f"{samples} samples, cap {DRAIN_MAX_SAMPLES} samples / "
            f"{DRAIN_MAX_SECONDS}s"
        )
    await control.reset()


@pytest_asyncio.fixture
async def tenant2_sync_trigger(api_client, setup_test_tenants):
    """Run a report cycle scoped to tenant 2, and reset only its sync clock.

    ``POST /commands/run-now`` reads the tenant from the API token, never from
    a query parameter: with the admin token the cycle is global and every
    tenant's last-sync clock is left where it was, so the eventless branch
    skips a tenant synced less than 300 seconds ago. A key of the tenant's own
    is what makes run-now zero that clock, and a zeroed clock is the
    precondition of every measurement in the sync-cycle tests.

    The key is rotated in and deleted again, so the tenant is left as found.
    """
    resp = await api_client.post(api_routes.tenant_api_key("test-tenant-2"))
    assert resp.status_code == 200, resp.text
    tenant_token = resp.json()["api_key"]

    async def _trigger() -> None:
        async with httpx.AsyncClient(base_url=MAILPROXY_URL, timeout=30.0) as client:
            resp = await client.post(
                api_routes.run_now(), headers={"X-API-Token": tenant_token}
            )
            resp.raise_for_status()

    yield _trigger
    await api_client.delete(api_routes.tenant_api_key("test-tenant-2"))


@pytest_asyncio.fixture
async def quiet_tenant1(api_client, setup_test_tenants):
    """Tenant 1's fake client, drained and reset."""
    control = ClientControl(CLIENT_TENANT1_URL)

    async def _trigger() -> None:
        await api_client.post(api_routes.run_now("test-tenant-1"))

    await _drain_and_reset(_trigger, control)
    return control


@pytest_asyncio.fixture
async def quiet_tenant2(tenant2_sync_trigger):
    """Tenant 2's fake client, drained and reset."""
    control = ClientControl(CLIENT_TENANT2_URL)
    await _drain_and_reset(tenant2_sync_trigger, control)
    return control


@pytest.fixture
def wait_for_report_entry():
    """An async callable returning the delivery_report entry for one message id.

    Polls the fake client's history and looks inside every ``/proxy_sync``
    body, because one cycle reports every message that has an unreported
    event — the entry for this test's message is one of several.
    """
    async def _wait(control: ClientControl, msg_id: str, timeout: float = 30.0):
        start = time.time()
        while time.time() - start < timeout:
            for call in await control.get_call_history():
                if call["path"] != api_routes.CLIENT_SYNC_PATH:
                    continue
                for entry in call["body"].get("delivery_report") or []:
                    if entry.get("id") == msg_id:
                        return entry, call
            await asyncio.sleep(0.5)
        return None, None

    return _wait


@pytest.fixture
def wait_for_reported_event():
    """An async callable returning a message's event once it carries reported_ts.

    ``reported_ts`` lives on the message events, which the API exposes through
    ``GET /messages?include_history=true``.
    """
    async def _wait(
        api_client,
        msg_id: str,
        event_type: str,
        tenant_id: str = "test-tenant-1",
        timeout: float = 30.0,
    ) -> dict[str, Any] | None:
        start = time.time()
        while time.time() - start < timeout:
            resp = await api_client.get(
                api_routes.messages(tenant_id=tenant_id, include_history=True)
            )
            for message in resp.json().get("messages", []):
                if message.get("id") != msg_id:
                    continue
                for event in message.get("history") or []:
                    if event.get("event_type") == event_type and event.get("reported_ts"):
                        return event
            await asyncio.sleep(0.5)
        return None

    return _wait


@pytest_asyncio.fixture
async def tenant1_fetches_from_fake_client(api_client, setup_test_tenants):
    """Point tenant 1's attachment fetch at the fake client, and put it back.

    The conftest tenants set ``client_sync_path`` but not
    ``client_attachment_path``, so attachments would go to the schema default
    ``/mail-proxy/attachments``, which the fake client does not serve. The
    tenant is edited through the API, never in the shared conftest: the
    default the rest of the suite runs on stays exactly as it is.

    Putting it back needs no teardown, and could not have one: ``PUT /tenant``
    drops the fields left at None, so it cannot clear a path. What restores
    the tenant is ``setup_test_tenants`` itself — ``POST /tenant`` upserts
    every column from its payload, and that payload has no attachment path.
    """
    resp = await api_client.put(
        api_routes.tenant("test-tenant-1"),
        json={"client_attachment_path": api_routes.CLIENT_ATTACHMENT_PATH},
    )
    assert resp.status_code == 200, resp.text
