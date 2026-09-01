# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""How the client's answer steers the proxy's next report cycle.

Two fields do it, and neither is visible in the cycle that carries them —
both act on the one after. ``_client_report_loop`` runs another cycle at once
while the answer's ``queued`` is above zero, and ``next_sync_after`` writes
the tenant's last-sync clock, which decides whether that next cycle calls the
tenant at all (the eventless branch waits out a 300 second interval).

So the acceleration shows up as a CHAIN: one call per accelerating answer,
about half a second apart. That is what the three tests measure, each holding
one field still and moving the other — and the chain is also what makes the
measurement survive a full run. ``run now`` sets the SMTP loop's wake too, and
that loop fires ``_wake_client_event`` when it finishes, so a second cycle
arriving close behind the first is normal whenever other tests left dispatch
work in flight. A stray wake like that adds ONE call; it never adds three.

The cycle is triggered through the tenant's own API key
(``tenant2_sync_trigger``): run-now reads its tenant from the API token and
ignores a ``tenant_id`` query parameter, and only a tenant-scoped token zeroes
that tenant's last-sync clock, which is what lets the first call happen at
all. The calls are filtered by tenant 2's bearer token, because other tenants
share this fake client.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests import api_routes

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]

TENANT2_AUTHORIZATION = "Bearer tenant2-secret-token"

# Answers queued before each trigger. The chain can be at most this long, so
# the two margins below sit either side of a stray wake's single extra call.
ACCELERATING_ANSWERS = 3
STRAY_WAKE_ALLOWANCE = 2


def answer(queued: int, next_sync_after: float) -> dict:
    """One programmed answer to a report call.

    ``next_sync_after`` is always sent explicitly: the proxy stores
    ``next_sync if next_sync else now``, so a value of 0 is falsy and reads
    exactly like sending no field at all. A time in the past leaves the tenant
    free to be called again; a time in the future suspends it.
    """
    return {"ok": True, "queued": queued, "next_sync_after": next_sync_after}


def tenant2_sync_calls(calls: list[dict]) -> list[dict]:
    """The report calls that came from test-tenant-2, by its own token."""
    return [
        call
        for call in calls
        if call["path"] == api_routes.CLIENT_SYNC_PATH
        and call["authorization"] == TENANT2_AUTHORIZATION
    ]


async def calls_after_trigger(control, trigger, wanted: int) -> list[dict]:
    """Trigger one cycle and return tenant 2's calls, waiting for the chain.

    Returns as soon as ``wanted`` calls have arrived; otherwise it waits the
    full window, so a test asserting an upper bound reads a settled history.
    """
    await trigger()
    deadline = time.time() + 20.0
    calls: list[dict] = []
    while time.time() < deadline:
        calls = tenant2_sync_calls(await control.get_call_history())
        if len(calls) >= wanted:
            return calls
        await asyncio.sleep(0.5)
    return calls


class TestQueuedAcceleratesTheCycle:
    """`queued` above zero makes the proxy come back without waiting."""

    async def test_queued_above_zero_produces_a_chain_of_calls(
        self, quiet_tenant2, tenant2_sync_trigger
    ):
        """Every accelerating answer is followed by another report call."""
        free = time.time() - 3600
        for _ in range(ACCELERATING_ANSWERS):
            await quiet_tenant2.queue_sync_response(answer(2, free))

        calls = await calls_after_trigger(
            quiet_tenant2, tenant2_sync_trigger, ACCELERATING_ANSWERS
        )

        assert len(calls) >= ACCELERATING_ANSWERS, f"queued 2 did not chain: {calls}"
        assert all(call["body"] == {"delivery_report": []} for call in calls)

    async def test_queued_zero_ends_the_cycle(
        self, quiet_tenant2, tenant2_sync_trigger
    ):
        """The same answers with queued 0 produce no chain, only the one call."""
        free = time.time() - 3600
        for _ in range(ACCELERATING_ANSWERS):
            await quiet_tenant2.queue_sync_response(answer(0, free))

        calls = await calls_after_trigger(
            quiet_tenant2, tenant2_sync_trigger, ACCELERATING_ANSWERS
        )

        assert calls, "the tenant was never called at all"
        assert len(calls) <= STRAY_WAKE_ALLOWANCE, (
            f"queued 0 still brought the proxy back: {calls}"
        )


class TestNextSyncAfterSuspendsTheTenant:
    """`next_sync_after` in the future holds the following cycle off."""

    async def test_future_next_sync_after_suppresses_the_chain(
        self, quiet_tenant2, tenant2_sync_trigger
    ):
        """The same accelerating answers, and the suspended tenant is not chained."""
        suspended = time.time() + 3600
        for _ in range(ACCELERATING_ANSWERS):
            await quiet_tenant2.queue_sync_response(answer(2, suspended))

        calls = await calls_after_trigger(
            quiet_tenant2, tenant2_sync_trigger, ACCELERATING_ANSWERS
        )

        assert calls, "the tenant was never called at all"
        assert len(calls) <= STRAY_WAKE_ALLOWANCE, (
            f"the tenant was chained while suspended: {calls}"
        )

        # Put the tenant's clock back where the rest of the suite expects it:
        # a tenant-scoped run-now zeroes it, and this answer keeps it there.
        await quiet_tenant2.queue_sync_response(answer(0, time.time() - 3600))
        await tenant2_sync_trigger()
