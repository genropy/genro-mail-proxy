# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""What the proxy asks its client for, when a message carries an attachment.

A ``storage_path`` that is neither a URL, nor an absolute path, nor base64
puts the fetch in endpoint mode: the proxy POSTs ``{"storage_path": ...}`` to
the tenant's attachment endpoint and expects the bytes back. The three
answers this file drives are the three the fake client can give.

The proxy does NOT tell 404 and 503 apart: ``HttpFetcher.fetch`` calls
``raise_for_status`` on both, the dispatcher turns the exception into the
same ValueError, and the message ends in error either way. The two tests
below assert exactly that, and read the status out of the recorded error
text — which is where the difference does survive.
"""

from __future__ import annotations

import time

import pytest

from tests import api_routes
from tests.fullstack.helpers import (
    trigger_dispatch,
    wait_for_message_status,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


def message_with_attachment(msg_id: str, storage_path: str) -> dict:
    """A message whose only attachment must be fetched from the client."""
    return {
        "id": msg_id,
        "tenant_id": "test-tenant-1",
        "account_id": "test-account-1",
        "from": "sender@test.com",
        "to": ["recipient@example.com"],
        "subject": "Client protocol - attachment",
        "body": "The attachment comes from the client.",
        "attachments": [{"filename": "invoice.pdf", "storage_path": storage_path}],
    }


def attachment_requests(calls: list[dict]) -> list[dict]:
    """The attachment fetches among the recorded calls."""
    return [call for call in calls if call["path"] == api_routes.CLIENT_ATTACHMENT_PATH]


class TestAttachmentFetch:
    """The three answers the client can give to an attachment request."""

    async def test_programmed_bytes_are_fetched_and_the_message_is_sent(
        self, api_client, quiet_tenant1, tenant1_fetches_from_fake_client
    ):
        """The proxy asks for the storage_path and sends the message it gets back."""
        ts = int(time.time())
        msg_id = f"attachment-ok-{ts}"
        storage_path = f"vol:invoice-{ts}.pdf"
        await quiet_tenant1.queue_attachment_response(b"%PDF-1.4 fake invoice")

        resp = await api_client.post(
            api_routes.ADD_MESSAGES,
            json={"messages": [message_with_attachment(msg_id, storage_path)]},
        )
        assert resp.status_code == 200, resp.text
        await trigger_dispatch(api_client)

        msg = await wait_for_message_status(api_client, msg_id, ("sent",))
        assert msg, f"message {msg_id} never reached sent"

        requests = attachment_requests(await quiet_tenant1.get_call_history())
        assert requests, "the proxy never asked the client for the attachment"
        assert requests[-1]["body"] == {"storage_path": storage_path}

    async def test_not_found_attachment_fails_the_message(
        self, api_client, quiet_tenant1, tenant1_fetches_from_fake_client
    ):
        """A 404 leaves the message in error, and the reason names the status."""
        ts = int(time.time())
        msg_id = f"attachment-404-{ts}"
        await quiet_tenant1.queue_attachment_response(status=404)

        resp = await api_client.post(
            api_routes.ADD_MESSAGES,
            json={"messages": [message_with_attachment(msg_id, f"vol:missing-{ts}.pdf")]},
        )
        assert resp.status_code == 200, resp.text
        await trigger_dispatch(api_client)

        msg = await wait_for_message_status(api_client, msg_id, ("error",))
        assert msg, f"message {msg_id} never reached error"
        assert "invoice.pdf" in msg["error"]
        assert "404" in msg["error"]

    async def test_unavailable_attachment_fails_the_message_the_same_way(
        self, api_client, quiet_tenant1, tenant1_fetches_from_fake_client
    ):
        """A 503 ends exactly like a 404 — only the recorded status differs."""
        ts = int(time.time())
        msg_id = f"attachment-503-{ts}"
        await quiet_tenant1.queue_attachment_response(status=503)

        resp = await api_client.post(
            api_routes.ADD_MESSAGES,
            json={"messages": [message_with_attachment(msg_id, f"vol:unavailable-{ts}.pdf")]},
        )
        assert resp.status_code == 200, resp.text
        await trigger_dispatch(api_client)

        msg = await wait_for_message_status(api_client, msg_id, ("error",))
        assert msg, f"message {msg_id} never reached error"
        assert "503" in msg["error"]
