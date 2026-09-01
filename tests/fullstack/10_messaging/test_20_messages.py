# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import time

import pytest

from tests import api_routes

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestMessageManagement:
    """Test message listing and deletion."""

    async def test_list_messages(self, api_client, setup_test_tenants):
        """Can list all messages."""
        resp = await api_client.get(api_routes.messages(tenant_id="test-tenant-1"))
        assert resp.status_code == 200
        # Response should be {"ok": True, "messages": [...]}
        data = resp.json()
        assert data.get("ok") is True
        assert isinstance(data.get("messages"), list)

    async def test_delete_messages(self, api_client, setup_test_tenants):
        """Can delete messages by ID."""
        ts = int(time.time())
        msg_id = f"to-delete-{ts}"

        # Add a message
        message = {
            "id": msg_id,
            "tenant_id": "test-tenant-1",
            "account_id": "test-account-1",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "To Delete",
            "body": "This will be deleted",
        }
        await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})

        # Delete it (tenant_id is required query param)
        resp = await api_client.post(
            api_routes.delete_messages(tenant_id="test-tenant-1"),
            json={"ids": [msg_id]}
        )
        assert resp.status_code == 200


# ============================================
# INFRASTRUCTURE CHECK
# ============================================
