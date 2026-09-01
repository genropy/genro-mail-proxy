# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""What the proxy sends its client when a message reaches an outcome.

Every assertion here reads the fake client's recorded call, never only the
message status: a test that passed without the client being called would
prove nothing about the outbound protocol, which is what this group exists
to hold still across the 0.7.7 transport swap.
"""

from __future__ import annotations

import time

import pytest

from tests import api_routes
from tests.fullstack.helpers import (
    CLIENT_TENANT1_URL,
    MAILHOG_TENANT2_SMTP,
    SMTP_REJECT_HOST,
    ClientControl,
    create_dsn_bounce_email,
    inject_bounce_email_to_imap,
    trigger_dispatch,
    wait_for_message_status,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


async def queue_message(api_client, message: dict) -> None:
    """Hand one message to the proxy."""
    resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
    assert resp.status_code == 200, resp.text


def message_to_mailhog(msg_id: str, tenant_id: str = "test-tenant-1") -> dict:
    """A message that will be delivered, so the report carries sent_ts."""
    return {
        "id": msg_id,
        "tenant_id": tenant_id,
        "account_id": "test-account-1" if tenant_id == "test-tenant-1" else "test-account-2",
        "from": "sender@test.com",
        "to": ["recipient@example.com"],
        "subject": "Client protocol",
        "body": "Reported to the client.",
    }


class TestReportShape:
    """The fields the proxy puts in a delivery report."""

    async def test_sent_message_is_reported_with_sent_ts(
        self, api_client, quiet_tenant1, wait_for_report_entry
    ):
        """A delivered message reaches the client as id plus sent_ts, nothing else."""
        msg_id = f"report-shape-sent-{int(time.time())}"
        await queue_message(api_client, message_to_mailhog(msg_id))
        await trigger_dispatch(api_client)

        entry, call = await wait_for_report_entry(quiet_tenant1, msg_id)

        assert entry, f"the client never received a report for {msg_id}"
        assert set(entry) == {"id", "sent_ts"}
        assert isinstance(entry["sent_ts"], int)
        assert set(call["body"]) == {"delivery_report"}

    async def test_failed_message_is_reported_with_its_own_outcome(
        self, api_client, quiet_tenant1, wait_for_report_entry
    ):
        """A refused message reaches the client as a failure, never as sent.

        The reject SMTP produces a deferred event on the first attempt and an
        error one once the retries run out, so the report can legitimately
        carry either — and each shape is asserted whole, because the field
        pair is the part 0.7.7 must not move.
        """
        await api_client.post(
            api_routes.ACCOUNT,
            json={
                "id": "account-protocol-reject",
                "tenant_id": "test-tenant-1",
                "host": SMTP_REJECT_HOST,
                "port": 1025,
                "use_tls": False,
            },
        )
        msg_id = f"report-shape-error-{int(time.time())}"
        message = message_to_mailhog(msg_id)
        message["account_id"] = "account-protocol-reject"
        await queue_message(api_client, message)
        await trigger_dispatch(api_client)

        entry, _ = await wait_for_report_entry(quiet_tenant1, msg_id)

        assert entry, f"the client never received a report for {msg_id}"
        assert "sent_ts" not in entry
        if "error_ts" in entry:
            assert set(entry) == {"id", "error_ts", "error"}
            assert entry["error"], "the error report carries no reason"
        else:
            assert set(entry) == {"id", "deferred_ts", "deferred_reason"}

    async def test_reported_event_gets_a_reported_ts(
        self, api_client, quiet_tenant1, wait_for_report_entry, wait_for_reported_event
    ):
        """Once the client has acknowledged, the event carries reported_ts."""
        msg_id = f"report-reported-ts-{int(time.time())}"
        await queue_message(api_client, message_to_mailhog(msg_id))
        await trigger_dispatch(api_client)

        entry, _ = await wait_for_report_entry(quiet_tenant1, msg_id)
        assert entry, f"the client never received a report for {msg_id}"

        event = await wait_for_reported_event(api_client, msg_id, "sent")

        assert event, f"the sent event of {msg_id} never got a reported_ts"
        assert event["reported_ts"] >= event["event_ts"]


class TestReportAuthentication:
    """The credentials the proxy presents, one tenant's configuration at a time."""

    async def test_tenant_without_auth_sends_no_authorization(
        self, api_client, quiet_tenant1, wait_for_report_entry
    ):
        """Tenant 1 is configured method none, so the call carries no header."""
        msg_id = f"report-auth-none-{int(time.time())}"
        await queue_message(api_client, message_to_mailhog(msg_id))
        await trigger_dispatch(api_client)

        entry, call = await wait_for_report_entry(quiet_tenant1, msg_id)

        assert entry, f"the client never received a report for {msg_id}"
        assert call["authorization"] is None

    async def test_bearer_tenant_sends_its_token(
        self, api_client, quiet_tenant2, wait_for_report_entry
    ):
        """Tenant 2 is configured bearer, so the call carries its token."""
        await api_client.post(
            api_routes.ACCOUNT,
            json={
                "id": "test-account-2",
                "tenant_id": "test-tenant-2",
                "host": MAILHOG_TENANT2_SMTP[0],
                "port": MAILHOG_TENANT2_SMTP[1],
                "use_tls": False,
            },
        )
        msg_id = f"report-auth-bearer-{int(time.time())}"
        await queue_message(api_client, message_to_mailhog(msg_id, "test-tenant-2"))
        await trigger_dispatch(api_client, "test-tenant-2")

        entry, call = await wait_for_report_entry(quiet_tenant2, msg_id)

        assert entry, f"the client never received a report for {msg_id}"
        assert call["authorization"] == "Bearer tenant2-secret-token"


class TestAcknowledgementIsUnconditional:
    """Characterization: an HTTP 200 marks the events reported, whatever it says.

    ``reporting.py`` builds ``processed_ids`` from the payloads BEFORE parsing
    the response body; the ``error`` and ``not_found`` lists it then reads are
    used only in a log line. So a client that rejects a message still gets it
    marked reported, and the proxy never sends it again.

    These two tests assert that behaviour as it is, not as it could be. They
    are the tripwire under the 0.7.7 transport swap: if the acknowledgement
    contract moves, the suite says so here.
    """

    async def test_error_answer_still_marks_reported(
        self, api_client, quiet_tenant1, wait_for_report_entry, wait_for_reported_event
    ):
        """A client answering error for the message still gets it marked reported."""
        msg_id = f"report-ack-error-{int(time.time())}"
        await quiet_tenant1.queue_sync_response({"error": [msg_id], "queued": 0})
        await queue_message(api_client, message_to_mailhog(msg_id))
        await trigger_dispatch(api_client)

        entry, _ = await wait_for_report_entry(quiet_tenant1, msg_id)
        assert entry, f"the client never received a report for {msg_id}"

        event = await wait_for_reported_event(api_client, msg_id, "sent")

        assert event, "an error answer left the event unreported — the contract moved"

    async def test_not_found_answer_still_marks_reported(
        self, api_client, quiet_tenant1, wait_for_report_entry, wait_for_reported_event
    ):
        """A client answering not_found for the message still gets it marked reported."""
        msg_id = f"report-ack-notfound-{int(time.time())}"
        await quiet_tenant1.queue_sync_response({"not_found": [msg_id], "queued": 0})
        await queue_message(api_client, message_to_mailhog(msg_id))
        await trigger_dispatch(api_client)

        entry, _ = await wait_for_report_entry(quiet_tenant1, msg_id)
        assert entry, f"the client never received a report for {msg_id}"

        event = await wait_for_reported_event(api_client, msg_id, "sent")

        assert event, "a not_found answer left the event unreported — the contract moved"


# The report side of the bounce defect the receiver side already measures:
# _events_to_payloads has an `elif event_type == "bounce"` branch, and nothing
# in src/ ever writes a message_event of that type, so no bounce can reach a
# client. Same root cause as the five xfails in 60_imap/test_10_bounce_live.py.
# See https://github.com/genropy/genro-mail-proxy/issues/103.
# strict=True on purpose: the day the fix lands this XPASSES, pytest reports
# that as an error, and the marker has to come off — from then on the report
# shape of a bounce is asserted like every other outcome in this file.
@pytest.mark.bounce_e2e
@pytest.mark.xfail(
    strict=True,
    reason="bounce never reaches the client report (issue #103)",
)
class TestBounceReachesTheClient:
    """A detected bounce should be reported to the client like any other event."""

    async def test_bounce_is_reported_with_bounce_ts(
        self,
        api_client,
        setup_bounce_tenant,
        configure_bounce_receiver,
        clean_imap,
        wait_for_report_entry,
    ):
        """The client receives bounce_ts for a message a DSN bounced."""
        control = ClientControl(CLIENT_TENANT1_URL)
        await control.reset()

        ts = int(time.time())
        msg_id = f"report-bounce-{ts}"
        recipient = f"invalid-{ts}@example.com"
        await queue_message(
            api_client,
            {
                "id": msg_id,
                "tenant_id": "bounce-tenant",
                "account_id": "bounce-account",
                "from": "sender@test.com",
                "to": [recipient],
                "subject": "Client protocol - bounce",
                "body": "This message will bounce.",
            },
        )
        await trigger_dispatch(api_client, "bounce-tenant")
        sent = await wait_for_message_status(
            api_client, msg_id, ("sent",), tenant_id="bounce-tenant"
        )
        assert sent, f"message {msg_id} never reached sent"

        await inject_bounce_email_to_imap(
            create_dsn_bounce_email(
                original_message_id=msg_id,
                recipient=recipient,
                bounce_code="550",
                bounce_reason="5.1.1 User unknown",
            )
        )

        entry, _ = await wait_for_report_entry(control, msg_id, timeout=40.0)

        assert entry, f"the client never received a report for {msg_id}"
        assert entry.get("bounce_ts"), "the report carries no bounce_ts"
        assert entry.get("bounce_type") == "hard"
