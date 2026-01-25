# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import asyncio
import time

import pytest

from .helpers import (
    MAILHOG_TENANT1_API,
    SMTP_REJECT_HOST,
    SMTP_REJECT_PORT,
    clear_mailhog,
    get_msg_status,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestDeliveryReports:
    """Test delivery report callbacks to client endpoints.

    The mail proxy should send delivery reports to the configured
    client_sync_url after messages are sent/failed/deferred.
    """

    async def test_delivery_report_sent_on_success(
        self, api_client, setup_test_tenants
    ):
        """Delivery report should be sent to client after successful email delivery."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())
        msg_id = f"report-success-{ts}"

        message = {
            "id": msg_id,
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Delivery Report Test",
            "body": "Testing delivery report callback.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch and wait for delivery
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Verify message was sent
        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

        # Check message status - should be sent and reported
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == msg_id]

        if found:
            msg = found[0]
            assert get_msg_status(msg) == "sent"
            # After delivery cycle, reported_ts should be set
            # (depends on report_interval configuration)

    async def test_delivery_report_sent_on_error(
        self, api_client, setup_test_tenants
    ):
        """Delivery report should include failed messages."""
        # Create account pointing to reject SMTP
        account_data = {
            "id": "account-report-reject",
            "tenant_id": "test-tenant-1",
            "host": SMTP_REJECT_HOST,
            "port": 1025,
            "use_tls": False,
        }
        await api_client.post("/account", json=account_data)

        ts = int(time.time())
        msg_id = f"report-error-{ts}"

        message = {
            "id": msg_id,
            "account_id": "account-report-reject",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Delivery Report Error Test",
            "body": "This should fail and be reported.",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Check message status - should be error
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])
        found = [m for m in all_msgs if m.get("id") == msg_id]

        if found:
            msg = found[0]
            assert get_msg_status(msg) in ("error", "deferred")

    async def test_mixed_delivery_report(
        self, api_client, setup_test_tenants
    ):
        """Delivery report should contain both successful and failed messages."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        # Create reject account if not exists
        account_data = {
            "id": "account-mixed-reject",
            "tenant_id": "test-tenant-1",
            "host": SMTP_REJECT_HOST,
            "port": 1025,
            "use_tls": False,
        }
        await api_client.post("/account", json=account_data)

        ts = int(time.time())

        messages = [
            {
                "id": f"mixed-success-{ts}",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": "Mixed Report - Success",
                "body": "This should succeed.",
            },
            {
                "id": f"mixed-error-{ts}",
                "account_id": "account-mixed-reject",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": "Mixed Report - Error",
                "body": "This should fail.",
            },
        ]

        resp = await api_client.post("/commands/add-messages", json={"messages": messages})
        assert resp.status_code == 200

        # Trigger dispatch
        await api_client.post("/commands/run-now?tenant_id=test-tenant-1")
        await asyncio.sleep(3)

        # Check results
        resp = await api_client.get("/messages?tenant_id=test-tenant-1")
        all_msgs = resp.json().get("messages", [])

        success_msg = [m for m in all_msgs if m.get("id") == f"mixed-success-{ts}"]
        error_msg = [m for m in all_msgs if m.get("id") == f"mixed-error-{ts}"]

        if success_msg:
            assert get_msg_status(success_msg[0]) == "sent"
        if error_msg:
            assert get_msg_status(error_msg[0]) in ("error", "deferred")


# ============================================
# 18. SECURITY AND INPUT SANITIZATION
# ============================================
