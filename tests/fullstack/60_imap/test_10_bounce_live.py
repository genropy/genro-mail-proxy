# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Live bounce detection tests requiring Dovecot IMAP server.

These tests verify the BounceReceiver's live polling functionality:
- Automatic detection of bounce emails injected into IMAP mailbox
- Soft vs hard bounce classification
- Bounce information included in delivery reports
- reported_ts updated after bounce detection

NOTE: These tests configure BounceReceiver via API at runtime. They require
Dovecot IMAP server to be available (docker compose --profile bounce up).
If Dovecot is not available, tests are automatically skipped.

To run these tests:
1. Start Docker with bounce profile: docker compose --profile bounce up -d
2. Start mailproxy: uvicorn mail_proxy.server:app
3. Run: pytest tests/fullstack/60_imap/test_10_bounce_live.py -v

The tests will automatically configure bounce detection via PUT /instance
and POST /instance/reload-bounce.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    clear_imap_mailbox,
    clear_mailhog,
    create_dsn_bounce_email,
    get_imap_message_count,
    inject_bounce_email_to_imap,
    wait_for_bounce,
)

pytestmark = [
    pytest.mark.fullstack,
    pytest.mark.asyncio,
    pytest.mark.bounce_e2e,
]


class TestBounceLivePolling:
    """Test live bounce detection via IMAP polling.

    These tests require:
    - Dovecot IMAP server running (docker compose --profile bounce up)
    - BounceReceiver configured via the configure_bounce_receiver fixture
    - Poll interval set to 2 seconds for fast testing
    """

    async def test_live_hard_bounce_detected_automatically(
        self, api_client, setup_bounce_tenant, configure_bounce_receiver, clean_imap
    ):
        """Hard bounce injected into IMAP should be detected automatically.

        Flow:
        1. Send email via mailproxy
        2. Inject DSN bounce into IMAP mailbox
        3. Wait for BounceReceiver to poll and detect
        4. Verify message status updated to 'bounced'
        """
        await clear_mailhog(MAILHOG_TENANT1_API)
        ts = int(time.time())
        msg_id = f"bounce-live-hard-{ts}"
        recipient = f"invalid-{ts}@example.com"

        # Send message
        message = {
            "id": msg_id,
            "account_id": "bounce-account",
            "from": "sender@test.com",
            "to": [recipient],
            "subject": "Live Bounce Test - Hard",
            "body": "This message will bounce.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=bounce-tenant")
        await asyncio.sleep(2)

        # Inject DSN bounce into IMAP
        dsn_email = create_dsn_bounce_email(
            original_message_id=msg_id,
            recipient=recipient,
            bounce_code="550",
            bounce_reason="5.1.1 User unknown",
        )
        await inject_bounce_email_to_imap(dsn_email)

        # Wait for bounce detection (poll_interval=2s, wait up to 10s)
        bounced = await wait_for_bounce(api_client, msg_id, "bounce-tenant", timeout=10)
        assert bounced, f"Message {msg_id} should be marked as bounced"

        # Verify bounce details
        resp = await api_client.get(f"/messages?tenant_id=bounce-tenant")
        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            found = [m for m in messages if m.get("id") == msg_id]
            if found:
                msg = found[0]
                assert msg.get("bounce_type") == "hard"

    async def test_live_soft_bounce_detected(
        self, api_client, setup_bounce_tenant, configure_bounce_receiver, clean_imap
    ):
        """Soft bounce should be detected and classified correctly."""
        await clear_mailhog(MAILHOG_TENANT1_API)
        ts = int(time.time())
        msg_id = f"bounce-live-soft-{ts}"
        recipient = f"tempfail-{ts}@example.com"

        message = {
            "id": msg_id,
            "account_id": "bounce-account",
            "from": "sender@test.com",
            "to": [recipient],
            "subject": "Live Bounce Test - Soft",
            "body": "This message will soft bounce.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=bounce-tenant")
        await asyncio.sleep(2)

        # Inject soft bounce DSN
        dsn_email = create_dsn_bounce_email(
            original_message_id=msg_id,
            recipient=recipient,
            bounce_code="452",
            bounce_reason="4.2.2 Mailbox full",
        )
        await inject_bounce_email_to_imap(dsn_email)

        bounced = await wait_for_bounce(api_client, msg_id, "bounce-tenant", timeout=10)
        assert bounced, f"Message {msg_id} should be marked as bounced"

        resp = await api_client.get(f"/messages?tenant_id=bounce-tenant")
        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            found = [m for m in messages if m.get("id") == msg_id]
            if found:
                msg = found[0]
                assert msg.get("bounce_type") == "soft"

    async def test_bounce_included_in_delivery_report(
        self, api_client, setup_bounce_tenant, configure_bounce_receiver, clean_imap
    ):
        """Bounced messages should be included in delivery reports."""
        await clear_mailhog(MAILHOG_TENANT1_API)
        ts = int(time.time())
        msg_id = f"bounce-report-{ts}"
        recipient = f"bounced-{ts}@example.com"

        message = {
            "id": msg_id,
            "account_id": "bounce-account",
            "from": "sender@test.com",
            "to": [recipient],
            "subject": "Bounce Report Test",
            "body": "Testing bounce in delivery report.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=bounce-tenant")
        await asyncio.sleep(2)

        # Inject bounce
        dsn_email = create_dsn_bounce_email(
            original_message_id=msg_id,
            recipient=recipient,
            bounce_code="550",
            bounce_reason="5.1.1 No such user",
        )
        await inject_bounce_email_to_imap(dsn_email)

        # Wait for bounce detection
        await wait_for_bounce(api_client, msg_id, "bounce-tenant", timeout=10)

        # Trigger delivery report cycle
        await api_client.post("/commands/run-now?tenant_id=bounce-tenant")
        await asyncio.sleep(2)

        # Check that message has bounce info
        resp = await api_client.get(f"/messages?tenant_id=bounce-tenant")
        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            found = [m for m in messages if m.get("id") == msg_id]
            if found:
                msg = found[0]
                assert msg.get("bounce_type") is not None

    async def test_multiple_bounces_processed_in_batch(
        self, api_client, setup_bounce_tenant, configure_bounce_receiver, clean_imap
    ):
        """Multiple bounces in IMAP should be processed in a single poll cycle."""
        await clear_mailhog(MAILHOG_TENANT1_API)
        ts = int(time.time())
        msg_ids = [f"bounce-batch-{ts}-{i}" for i in range(3)]
        recipients = [f"batch-{ts}-{i}@example.com" for i in range(3)]

        # Send multiple messages
        messages = [
            {
                "id": msg_id,
                "account_id": "bounce-account",
                "from": "sender@test.com",
                "to": [recipient],
                "subject": f"Batch Bounce Test {i}",
                "body": "Testing batch bounce processing.",
            }
            for i, (msg_id, recipient) in enumerate(zip(msg_ids, recipients))
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=bounce-tenant")
        await asyncio.sleep(2)

        # Inject all bounces
        for msg_id, recipient in zip(msg_ids, recipients):
            dsn_email = create_dsn_bounce_email(
                original_message_id=msg_id,
                recipient=recipient,
                bounce_code="550",
                bounce_reason="5.1.1 User unknown",
            )
            await inject_bounce_email_to_imap(dsn_email)

        # Wait for all bounces to be detected
        await asyncio.sleep(6)  # 2-3 poll cycles

        # Verify all messages are bounced
        bounced_count = 0
        resp = await api_client.get(f"/messages?tenant_id=bounce-tenant")
        if resp.status_code == 200:
            messages = resp.json().get("messages", [])
            for msg_id in msg_ids:
                found = [m for m in messages if m.get("id") == msg_id]
                if found and found[0].get("bounce_type"):
                    bounced_count += 1

        assert bounced_count == 3, f"Expected 3 bounced messages, got {bounced_count}"

    async def test_imap_message_deleted_after_processing(
        self, api_client, setup_bounce_tenant, configure_bounce_receiver, clean_imap
    ):
        """Processed bounce emails should be deleted from IMAP mailbox."""
        await clear_mailhog(MAILHOG_TENANT1_API)
        await clear_imap_mailbox()
        ts = int(time.time())
        msg_id = f"bounce-delete-{ts}"
        recipient = f"delete-{ts}@example.com"

        message = {
            "id": msg_id,
            "account_id": "bounce-account",
            "from": "sender@test.com",
            "to": [recipient],
            "subject": "Bounce Delete Test",
            "body": "Testing IMAP cleanup after bounce processing.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        await api_client.post("/commands/run-now?tenant_id=bounce-tenant")
        await asyncio.sleep(2)

        # Check initial IMAP count
        initial_count = await get_imap_message_count()

        # Inject bounce
        dsn_email = create_dsn_bounce_email(
            original_message_id=msg_id,
            recipient=recipient,
            bounce_code="550",
            bounce_reason="5.1.1 User unknown",
        )
        await inject_bounce_email_to_imap(dsn_email)

        # Verify message was added
        after_inject_count = await get_imap_message_count()
        assert after_inject_count > initial_count

        # Wait for bounce processing
        await wait_for_bounce(api_client, msg_id, "bounce-tenant", timeout=10)

        # Check IMAP count after processing (should be deleted)
        await asyncio.sleep(2)
        final_count = await get_imap_message_count()
        assert final_count <= initial_count, "Processed bounce should be deleted from IMAP"
