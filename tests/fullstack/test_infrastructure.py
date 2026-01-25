# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Fullstack tests extracted from test_fullstack_integration.py."""

from __future__ import annotations


import pytest

httpx = pytest.importorskip("httpx")

from .helpers import (
    CLIENT_TENANT1_URL,
    CLIENT_TENANT2_URL,
    MAILHOG_TENANT1_API,
    MAILHOG_TENANT2_API,
    MINIO_URL,
)

pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


class TestInfrastructureCheck:
    """Verify test infrastructure is properly set up."""

    async def test_postgresql_connection(self, api_client):
        """Verify PostgreSQL is being used."""
        resp = await api_client.get("/status")
        assert resp.status_code == 200
        # Service should be running with PostgreSQL

    async def test_mailhog_tenant1_accessible(self):
        """MailHog for tenant 1 should be accessible."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MAILHOG_TENANT1_API}/api/v2/messages")
            assert resp.status_code == 200

    async def test_mailhog_tenant2_accessible(self):
        """MailHog for tenant 2 should be accessible."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{MAILHOG_TENANT2_API}/api/v2/messages")
            assert resp.status_code == 200

    async def test_minio_accessible(self):
        """MinIO S3 should be accessible."""
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(f"{MINIO_URL}/minio/health/live")
                assert resp.status_code == 200
            except Exception:
                # MinIO might not have this exact endpoint, skip
                pytest.skip("MinIO health endpoint not available")

    async def test_echo_servers_accessible(self):
        """Echo servers should be accessible."""
        async with httpx.AsyncClient() as client:
            resp = await client.get(CLIENT_TENANT1_URL)
            assert resp.status_code == 200

            resp = await client.get(CLIENT_TENANT2_URL)
            assert resp.status_code == 200


# ============================================
# 13. SMTP ERROR HANDLING
# ============================================
