# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Health endpoint and basic API functionality tests."""

from __future__ import annotations

import pytest

httpx = pytest.importorskip("httpx")

from tests.fullstack.helpers import MAILPROXY_URL

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestHealthAndBasics:
    """Test basic API functionality."""

    async def test_health_endpoint_no_auth(self):
        """Health endpoint should work without auth."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MAILPROXY_URL}/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("status") == "ok"

    async def test_status_endpoint_requires_auth(self):
        """Status endpoint should require authentication."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MAILPROXY_URL}/status")
            assert resp.status_code == 401

    async def test_status_endpoint_with_auth(self, api_client):
        """Status endpoint should work with valid token."""
        resp = await api_client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("ok") is True

    async def test_invalid_token_rejected(self):
        """Invalid token should be rejected."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{MAILPROXY_URL}/status",
                headers={"X-API-Token": "wrong-token"},
            )
            assert resp.status_code == 401
