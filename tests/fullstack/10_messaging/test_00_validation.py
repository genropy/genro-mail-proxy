# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations

import time

import pytest

from tests import api_routes

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

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        # Should fail validation
        assert resp.status_code in (400, 422) or resp.json().get("rejected", 0) > 0

    async def test_invalid_account_rejected(self, api_client):
        """Message with non-existent account should be rejected."""
        ts = int(time.time())
        message = {
            "id": f"nonexistent-acc-{ts}",
            "tenant_id": "test-tenant-1",
            "account_id": "nonexistent-account-id",
            "from": "sender@test.com",
            "to": ["recipient@example.com"],
            "subject": "Test",
            "body": "Test",
        }

        resp = await api_client.post(api_routes.ADD_MESSAGES, json={"messages": [message]})
        # Should be rejected
        data = resp.json()
        assert data.get("rejected", 0) > 0 or resp.status_code >= 400


class TestPayloadSchema:
    """The schema rejects an incomplete payload with 422, not with a 200.

    Ported from tests/unit/10_api/test_00_api.py, where the same two payloads
    were posted to a FastAPI TestClient. The pair above asserts loosely
    (``in (400, 422) or rejected > 0``) because it also covers the service-level
    rejection of an unknown account; these two pin the schema layer exactly.
    """

    async def test_message_missing_required_fields_is_422(self, api_client):
        """A message with nothing but an id fails schema validation."""
        resp = await api_client.post(
            api_routes.ADD_MESSAGES, json={"messages": [{"id": "schema-msg-1"}]}
        )
        assert resp.status_code == 422
        assert "detail" in resp.json()

    async def test_account_missing_host_and_port_is_422(self, api_client):
        """An account payload without host and port fails schema validation."""
        resp = await api_client.post(api_routes.ACCOUNT, json={"id": "schema-acc-1"})
        assert resp.status_code == 422
        assert "detail" in resp.json()


# ============================================
# 12. MESSAGE MANAGEMENT
# ============================================
