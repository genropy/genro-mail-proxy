# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""The programmable fake client — the suite's own infrastructure.

A test programs an answer through the control surface and reads back what the
client received. Nothing here goes through the proxy: what the proxy does with
those answers is the subject of 35_client_protocol.
"""

from __future__ import annotations

import pytest

from tests import api_routes

httpx = pytest.importorskip("httpx")

from tests.fullstack.helpers import (
    CLIENT_TENANT1_URL,
    CLIENT_TENANT2_URL,
    ClientControl,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]

DEFAULT_SYNC_ANSWER = {"ok": True, "queued": 0}


@pytest.fixture
def control():
    """The control surface of the tenant-1 fake client."""
    return ClientControl(CLIENT_TENANT1_URL)


async def post_sync_report(base_url: str, report: list[dict]):
    """Call /proxy_sync the way the proxy calls it."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        return await client.post(
            api_routes.CLIENT_SYNC_PATH, json={"delivery_report": report}
        )


async def post_attachment_request(base_url: str, storage_path: str):
    """Call /proxy_get_attachments the way the proxy calls it."""
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        return await client.post(
            api_routes.CLIENT_ATTACHMENT_PATH, json={"storage_path": storage_path}
        )


class TestControlSurface:
    """Programming answers and reading back the call history."""

    async def test_programmed_answer_is_served_and_call_recorded(self, control):
        """The client answers what the test queued, and records what it received."""
        await control.reset()
        await control.queue_sync_response({"ok": True, "queued": 7})

        resp = await post_sync_report(CLIENT_TENANT1_URL, [{"id": "msg-1", "sent_ts": 1}])

        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "queued": 7}

        calls = await control.get_call_history()
        assert len(calls) == 1
        assert calls[0]["path"] == api_routes.CLIENT_SYNC_PATH
        assert calls[0]["body"] == {"delivery_report": [{"id": "msg-1", "sent_ts": 1}]}

    async def test_default_answer_when_nothing_programmed(self, control):
        """With no answer queued the client acknowledges with queued 0."""
        await control.reset()

        resp = await post_sync_report(CLIENT_TENANT1_URL, [])

        assert resp.status_code == 200
        assert resp.json() == DEFAULT_SYNC_ANSWER

    async def test_queued_answers_are_consumed_in_order(self, control):
        """Each call takes the next queued answer, then the default."""
        await control.reset()
        await control.queue_sync_response({"ok": True, "queued": 3})
        await control.queue_sync_response({"ok": True, "queued": 1})

        first = await post_sync_report(CLIENT_TENANT1_URL, [])
        second = await post_sync_report(CLIENT_TENANT1_URL, [])
        third = await post_sync_report(CLIENT_TENANT1_URL, [])

        assert first.json()["queued"] == 3
        assert second.json()["queued"] == 1
        assert third.json() == DEFAULT_SYNC_ANSWER

    async def test_reset_drops_answers_and_history(self, control):
        """Reset leaves the client as if the run had just started."""
        await control.reset()
        await control.queue_sync_response({"ok": True, "queued": 5})
        await post_sync_report(CLIENT_TENANT1_URL, [])

        await control.reset()

        assert await control.get_call_history() == []
        resp = await post_sync_report(CLIENT_TENANT1_URL, [])
        assert resp.json() == DEFAULT_SYNC_ANSWER

    async def test_each_tenant_has_its_own_client(self, control):
        """The two instances are independent: one call does not show in the other."""
        other = ClientControl(CLIENT_TENANT2_URL)
        await control.reset()
        await other.reset()

        await post_sync_report(CLIENT_TENANT1_URL, [{"id": "for-tenant-1"}])

        assert len(await control.get_call_history()) == 1
        assert await other.get_call_history() == []


class TestAttachmentAnswers:
    """The three answers the attachment protocol distinguishes."""

    async def test_programmed_bytes_are_served(self, control):
        """Queued content comes back as the attachment body."""
        await control.reset()
        await control.queue_attachment_response(b"PDF-BYTES")

        resp = await post_attachment_request(CLIENT_TENANT1_URL, "vol:invoice.pdf")

        assert resp.status_code == 200
        assert resp.content == b"PDF-BYTES"

        calls = await control.get_call_history()
        assert calls[0]["body"] == {"storage_path": "vol:invoice.pdf"}

    async def test_programmed_error_status_is_served(self, control):
        """A bare status answers without content — 503 here."""
        await control.reset()
        await control.queue_attachment_response(status=503)

        resp = await post_attachment_request(CLIENT_TENANT1_URL, "vol:unavailable.pdf")

        assert resp.status_code == 503

    async def test_unprogrammed_attachment_is_not_found(self, control):
        """An attachment nobody queued does not exist."""
        await control.reset()

        resp = await post_attachment_request(CLIENT_TENANT1_URL, "vol:missing.pdf")

        assert resp.status_code == 404
