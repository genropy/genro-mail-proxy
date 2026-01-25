# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import time

import pytest

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestValidation:
    """Test input validation."""

    async def test_invalid_message_rejected(self, api_client, setup_test_tenants):
        """Invalid message payload should be rejected."""
        # Missing required fields
        message = {
            "id": "invalid-msg",
            # Missing account_id, from_addr, to_addr
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        # Should fail validation
        assert resp.status_code in (400, 422) or resp.json().get("rejected", 0) > 0

    async def test_invalid_account_rejected(self, api_client):
        """Message with non-existent account should be rejected."""
        ts = int(time.time())
        message = {
            "id": f"nonexistent-acc-{ts}",
            "account_id": "nonexistent-account-id",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test",
        }

        resp = await api_client.post("/commands/add-messages", json={"messages": [message]})
        # Should be rejected
        data = resp.json()
        assert data.get("rejected", 0) > 0 or resp.status_code >= 400


# ============================================
# 12. MESSAGE MANAGEMENT
# ============================================
