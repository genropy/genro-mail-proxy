# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Fixtures for interface tests using HTTP client.

These fixtures provide:
- FastAPI app with full endpoint registration
- HTTP client (MailProxyClient) connected via ASGITransport
- Database and proxy lifecycle management
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI

from core.mail_proxy.interface.api_base import create_app
from core.mail_proxy.proxy import MailProxy
from tools.http_client.client import MailProxyClient


# =============================================================================
# Database and Proxy Fixtures
# =============================================================================


@pytest.fixture
async def proxy(tmp_path) -> AsyncGenerator[MailProxy, None]:
    """Create a MailProxy instance with temporary database in test mode.

    test_mode=True disables automatic background loops (SMTP dispatch, sync).
    start_active=False prevents starting until explicitly requested.
    """
    from core.mail_proxy.proxy_config import ProxyConfig

    config = ProxyConfig(
        db_path=str(tmp_path / "test.db"),
        test_mode=True,
        start_active=False,
    )
    p = MailProxy(config)
    await p.db.connect()
    await p.db.check_structure()
    yield p
    await p.close()


# =============================================================================
# FastAPI App Fixture
# =============================================================================


@pytest.fixture
def app(proxy: MailProxy) -> FastAPI:
    """Create FastAPI app with all endpoints registered.

    Uses a null lifespan since proxy is already started via fixture.
    """

    @asynccontextmanager
    async def null_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield

    return create_app(proxy, api_token=None, lifespan=null_lifespan)


# =============================================================================
# HTTP Client Fixture
# =============================================================================


@pytest.fixture
async def client(app: FastAPI) -> AsyncGenerator[MailProxyClient, None]:
    """Create MailProxyClient connected to the test app via ASGITransport.

    This allows testing the full HTTP stack without a real server.
    """
    transport = httpx.ASGITransport(app=app)

    # Create a custom client class that uses the ASGI transport
    class TestMailProxyClient(MailProxyClient):
        """Client variant that uses ASGI transport for testing."""

        def __init__(self, transport: httpx.ASGITransport):
            super().__init__(url="http://testserver", token=None)
            self._transport = transport

        async def _get(self, path, params=None):
            async with httpx.AsyncClient(transport=self._transport) as http:
                resp = await http.get(
                    f"{self.url}{path}",
                    headers=self._headers(),
                    params=params,
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()

        async def _post(self, path, data=None, params=None):
            async with httpx.AsyncClient(transport=self._transport) as http:
                resp = await http.post(
                    f"{self.url}{path}",
                    headers=self._headers(),
                    json=data or {},
                    params=params,
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()

        async def _put(self, path, data=None):
            async with httpx.AsyncClient(transport=self._transport) as http:
                resp = await http.put(
                    f"{self.url}{path}",
                    headers=self._headers(),
                    json=data or {},
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()

        async def _delete(self, path, params=None):
            async with httpx.AsyncClient(transport=self._transport) as http:
                resp = await http.delete(
                    f"{self.url}{path}",
                    headers=self._headers(),
                    params=params,
                    timeout=30,
                )
                resp.raise_for_status()
                if resp.content:
                    return resp.json()
                return {"ok": True}

        async def _patch(self, path, data=None):
            async with httpx.AsyncClient(transport=self._transport) as http:
                resp = await http.patch(
                    f"{self.url}{path}",
                    headers=self._headers(),
                    json=data or {},
                    timeout=30,
                )
                resp.raise_for_status()
                return resp.json()

    yield TestMailProxyClient(transport)


# =============================================================================
# Setup Fixtures (Tenant + Account)
# =============================================================================


@pytest.fixture
async def setup_tenant(client: MailProxyClient) -> str:
    """Create a test tenant and return its ID."""
    await client.tenants.add(id="test-tenant", name="Test Tenant")
    return "test-tenant"


@pytest.fixture
async def setup_account(client: MailProxyClient, setup_tenant: str) -> tuple[str, str]:
    """Create a test account and return (tenant_id, account_id)."""
    await client.accounts.add(
        id="test-smtp",
        tenant_id=setup_tenant,
        host="smtp.test.local",
        port=587,
        user="testuser",
        password="testpass",
    )
    return (setup_tenant, "test-smtp")
