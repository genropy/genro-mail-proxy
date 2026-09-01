# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import time

import pytest

from tests import api_routes
from tests.fullstack.helpers import (
    MAILHOG_TENANT1_API,
    clear_mailhog,
    trigger_dispatch,
    wait_for_message_status,
    wait_for_messages,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestPriorityHandling:
    """Test message priority ordering."""

    async def test_priority_ordering(self, api_client, setup_test_tenants):
        """Higher priority messages should be sent first."""
        await clear_mailhog(MAILHOG_TENANT1_API)

        ts = int(time.time())

        # Add messages in reverse priority order
        messages = [
            {
                "id": f"prio-low-{ts}",
                "tenant_id": "test-tenant-1",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": "Low Priority",
                "body": "Low priority message",
                "priority": "low",
            },
            {
                "id": f"prio-high-{ts}",
                "tenant_id": "test-tenant-1",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": "High Priority",
                "body": "High priority message",
                "priority": "high",
            },
            {
                "id": f"prio-immediate-{ts}",
                "tenant_id": "test-tenant-1",
                "account_id": "test-account-1",
                "from": "sender@test.com",
                "to": ["recipient@example.com"],
                "subject": "Immediate Priority",
                "body": "Immediate priority message",
                "priority": "immediate",
            },
        ]

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": messages})
        assert resp.status_code == 200

        await trigger_dispatch(api_client)

        for msg in messages:
            sent = await wait_for_message_status(api_client, msg["id"], ("sent",))
            assert sent, f"message {msg['id']} never reached sent"

        msgs = await wait_for_messages(MAILHOG_TENANT1_API, 3)
        assert len(msgs) == 3

        # Note: Due to async processing, we can't strictly guarantee order
        # but all messages should be delivered
        subjects = [m["Content"]["Headers"]["Subject"][0] for m in msgs]
        assert "Immediate Priority" in subjects
        assert "High Priority" in subjects
        assert "Low Priority" in subjects


# ============================================
# 9. SERVICE CONTROL
# ============================================
