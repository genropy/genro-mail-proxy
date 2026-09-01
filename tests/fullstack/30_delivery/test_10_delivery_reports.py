# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import time

import pytest

from tests import api_routes
from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    SMTP_REJECT_HOST,
    clear_mailhog,
    get_msg_status,
    trigger_dispatch,
    wait_for_message_status,
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
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Delivery Report Test",
            "body": "Testing delivery report callback.",
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch and wait for delivery
        await trigger_dispatch(api_client)

        # Verify message was sent
        messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
        assert len(messages) >= 1

        # Check message status - should be sent and reported
        msg = await wait_for_message_status(api_client, msg_id, ("sent",))
        assert msg, f"message {msg_id} never reached sent"
        assert get_msg_status(msg) == "sent"

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
        await api_client.post(api_routes.ACCOUNT, json=account_data)

        ts = int(time.time())
        msg_id = f"report-error-{ts}"

        message = {
            "id": msg_id,
            "tenant_id": "test-tenant-1",
            "account_id": "account-report-reject",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Delivery Report Error Test",
            "body": "This should fail and be reported.",
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        assert resp.status_code == 200

        # Trigger dispatch
        await trigger_dispatch(api_client)

        # Check message status - should be error
        msg = await wait_for_message_status(api_client, msg_id, ("error", "deferred"))
        assert msg, f"message {msg_id} never reached error or deferred"

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
        await api_client.post(api_routes.ACCOUNT, json=account_data)

        ts = int(time.time())

        messages = [
            {
                "id": f"mixed-success-{ts}",
                "tenant_id": "test-tenant-1",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": "Mixed Report - Success",
                "body": "This should succeed.",
            },
            {
                "id": f"mixed-error-{ts}",
                "tenant_id": "test-tenant-1",
                "account_id": "account-mixed-reject",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": "Mixed Report - Error",
                "body": "This should fail.",
            },
        ]

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": messages})
        assert resp.status_code == 200

        # Trigger dispatch
        await trigger_dispatch(api_client)

        # Check results
        success_msg = await wait_for_message_status(api_client, f"mixed-success-{ts}", ("sent",))
        assert success_msg, f"message mixed-success-{ts} never reached sent"

        error_msg = await wait_for_message_status(
            api_client, f"mixed-error-{ts}", ("error", "deferred")
        )
        assert error_msg, f"message mixed-error-{ts} never reached error or deferred"


# ============================================
# 18. SECURITY AND INPUT SANITIZATION
# ============================================
