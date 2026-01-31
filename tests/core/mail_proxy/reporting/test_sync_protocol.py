# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Integration tests for the sync protocol with FakeClient.

Tests the complete bidirectional sync protocol:
1. Proxy calls FakeClient sync endpoint with delivery_report
2. FakeClient submits messages to proxy via POST /messages/add_batch
3. FakeClient responds with {"ok": true, "queued": N}
4. If queued > 0, proxy immediately re-calls sync
5. Cycle repeats until queued = 0

Uses CSV fixtures to define test scenarios.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from core.mail_proxy.interface.api_base import create_app
from core.mail_proxy.proxy import MailProxy

# Import FakeClient from fixtures
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "fixtures" / "reporting"))
from fake_client import FakeClient


FIXTURES_DIR = Path(__file__).parent.parent.parent.parent / "fixtures" / "reporting"


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def proxy(tmp_path) -> AsyncGenerator[MailProxy, None]:
    """Create a MailProxy instance for testing."""
    from core.mail_proxy.proxy_config import ProxyConfig

    p = MailProxy(ProxyConfig(
        db_path=str(tmp_path / "test.db"),
        test_mode=True,
        start_active=False,
    ))
    await p.db.connect()
    await p.db.check_structure()
    yield p
    await p.close()


@pytest.fixture
def app(proxy: MailProxy) -> FastAPI:
    """Create FastAPI app with all endpoints registered."""

    @asynccontextmanager
    async def null_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        yield

    return create_app(proxy, api_token=None, lifespan=null_lifespan)


@pytest.fixture
async def http_client(app: FastAPI) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Create HTTP client connected to test app via ASGITransport."""
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture
async def setup_tenant_and_account(http_client: httpx.AsyncClient) -> tuple[str, str]:
    """Create test tenant and account, return (tenant_id, account_id)."""
    # Create tenant
    resp = await http_client.post(
        "/tenants/add",
        json={"id": "fake-tenant", "name": "Fake Tenant"},
    )
    assert resp.status_code == 200

    # Create account
    resp = await http_client.post(
        "/accounts/add",
        json={
            "id": "fake-smtp",
            "tenant_id": "fake-tenant",
            "host": "smtp.test.local",
            "port": 587,
            "user": "testuser",
            "password": "testpass",
        },
    )
    assert resp.status_code == 200

    return ("fake-tenant", "fake-smtp")


# =============================================================================
# Test Classes
# =============================================================================


class TestSyncProtocol:
    """Test the bidirectional sync protocol with FakeClient."""

    @pytest.mark.asyncio
    async def test_fakeclient_starts_and_stops(self):
        """FakeClient can be started and stopped without errors."""
        csv_path = FIXTURES_DIR / "scenario_01_simple.csv"

        async with FakeClient(
            csv_path=csv_path,
            proxy_url="http://localhost:8000",
            tenant_id="test-tenant",
            account_id="test-account",
        ) as client:
            assert client.port > 0
            assert client.base_url.startswith("http://")
            assert client.total_messages == 3
            assert client.pending_count == 3

    @pytest.mark.asyncio
    async def test_fakeclient_health_endpoint(self):
        """FakeClient health endpoint returns OK."""
        csv_path = FIXTURES_DIR / "scenario_01_simple.csv"

        async with FakeClient(
            csv_path=csv_path,
            proxy_url="http://localhost:8000",
            tenant_id="test-tenant",
            account_id="test-account",
        ) as client:
            async with httpx.AsyncClient() as http:
                resp = await http.get(f"{client.base_url}/health")
                assert resp.status_code == 200
                data = resp.json()
                assert data["ok"] is True
                assert data["tenant_id"] == "test-tenant"

    @pytest.mark.asyncio
    async def test_fakeclient_sync_submits_messages(
        self,
        http_client: httpx.AsyncClient,
        setup_tenant_and_account: tuple[str, str],
    ):
        """FakeClient submits messages when sync is called."""
        tenant_id, account_id = setup_tenant_and_account
        csv_path = FIXTURES_DIR / "scenario_01_simple.csv"

        async with FakeClient(
            csv_path=csv_path,
            proxy_url="http://testserver",
            tenant_id=tenant_id,
            account_id=account_id,
            batch_size=10,
        ) as client:
            # Patch FakeClient to use our test HTTP client
            original_submit = client._submit_messages_to_proxy

            async def patched_submit(messages):
                """Submit via test transport instead of real HTTP."""
                resp = await http_client.post(
                    "/messages/add_batch",
                    json={"messages": messages},
                )
                return resp.json()

            client._submit_messages_to_proxy = patched_submit

            # Call sync endpoint (simulating proxy call)
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"{client.base_url}/sync",
                    json={"delivery_report": []},
                )
                assert resp.status_code == 200
                data = resp.json()

                # Should have submitted messages and report queued=0
                # (all 3 messages fit in one batch)
                assert data["ok"] is True
                assert data["queued"] == 3  # 3 messages submitted

            # Messages should be in proxy
            resp = await http_client.get(
                "/messages/list",
                params={"tenant_id": tenant_id},
            )
            messages = resp.json()
            assert len(messages) == 3

    @pytest.mark.asyncio
    async def test_fakeclient_multi_batch(
        self,
        http_client: httpx.AsyncClient,
        setup_tenant_and_account: tuple[str, str],
    ):
        """FakeClient handles multiple batches correctly."""
        tenant_id, account_id = setup_tenant_and_account
        csv_path = FIXTURES_DIR / "scenario_07_multi_batch.csv"

        async with FakeClient(
            csv_path=csv_path,
            proxy_url="http://testserver",
            tenant_id=tenant_id,
            account_id=account_id,
            batch_size=2,  # Small batch to force multiple cycles
        ) as client:
            # Patch FakeClient to use our test HTTP client
            async def patched_submit(messages):
                resp = await http_client.post(
                    "/messages/add_batch",
                    json={"messages": messages},
                )
                return resp.json()

            client._submit_messages_to_proxy = patched_submit

            # Simulate multiple sync cycles
            # With 5 messages and batch_size=2:
            # Cycle 1: submit 2, queued=5 (2 submitted + 3 remaining)
            # Cycle 2: submit 2, queued=3 (2 submitted + 1 remaining)
            # Cycle 3: submit 1, queued=1 (1 submitted + 0 remaining)
            # Cycle 4: submit 0, queued=0 (done)
            cycles = 0

            async with httpx.AsyncClient() as http:
                while True:
                    cycles += 1
                    resp = await http.post(
                        f"{client.base_url}/sync",
                        json={"delivery_report": []},
                    )
                    data = resp.json()

                    if data["queued"] == 0:
                        break

                    if cycles > 10:  # Safety limit
                        raise AssertionError("Too many cycles")

            # Should have taken 4 cycles to complete (3 submit cycles + 1 final with queued=0)
            assert cycles == 4
            assert client.pending_count == 0

            # All messages in proxy
            resp = await http_client.get(
                "/messages/list",
                params={"tenant_id": tenant_id},
            )
            messages = resp.json()
            assert len(messages) == 5

    @pytest.mark.asyncio
    async def test_fakeclient_receives_delivery_reports(
        self,
        http_client: httpx.AsyncClient,
        setup_tenant_and_account: tuple[str, str],
    ):
        """FakeClient stores received delivery reports."""
        tenant_id, account_id = setup_tenant_and_account
        csv_path = FIXTURES_DIR / "scenario_01_simple.csv"

        async with FakeClient(
            csv_path=csv_path,
            proxy_url="http://testserver",
            tenant_id=tenant_id,
            account_id=account_id,
        ) as client:
            # Call sync with some delivery reports
            reports = [
                {"id": "old-msg-1", "sent_ts": 1700000000},
                {"id": "old-msg-2", "error_ts": 1700000100, "error": "Failed"},
            ]

            async with httpx.AsyncClient() as http:
                await http.post(
                    f"{client.base_url}/sync",
                    json={"delivery_report": reports},
                )

            # FakeClient should have stored the reports
            assert len(client.received_reports) == 2
            assert client.received_reports[0]["id"] == "old-msg-1"
            assert client.received_reports[1]["id"] == "old-msg-2"


class TestAttachmentEndpoint:
    """Test FakeClient attachment endpoint for fetch_mode=endpoint."""

    @pytest.mark.asyncio
    async def test_attachment_endpoint_returns_registered_content(self):
        """FakeClient returns registered attachment content."""
        csv_path = FIXTURES_DIR / "scenario_04_endpoint_attach.csv"

        async with FakeClient(
            csv_path=csv_path,
            proxy_url="http://localhost:8000",
            tenant_id="test-tenant",
            account_id="test-account",
        ) as client:
            # Register attachment content
            content = b"PDF content here"
            client.register_attachment("doc_id=INV-2025-001", content)

            # Request attachment
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"{client.base_url}/attachments",
                    json={"storage_path": "doc_id=INV-2025-001"},
                )
                assert resp.status_code == 200
                assert resp.content == content

    @pytest.mark.asyncio
    async def test_attachment_endpoint_returns_404_for_unknown(self):
        """FakeClient returns 404 for unknown attachment."""
        csv_path = FIXTURES_DIR / "scenario_01_simple.csv"

        async with FakeClient(
            csv_path=csv_path,
            proxy_url="http://localhost:8000",
            tenant_id="test-tenant",
            account_id="test-account",
        ) as client:
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"{client.base_url}/attachments",
                    json={"storage_path": "unknown"},
                )
                assert resp.status_code == 404


class TestCSVScenarios:
    """Test different CSV scenarios."""

    @pytest.mark.asyncio
    async def test_scenario_base64_attachments(
        self,
        http_client: httpx.AsyncClient,
        setup_tenant_and_account: tuple[str, str],
    ):
        """Messages with base64 attachments are submitted correctly."""
        tenant_id, account_id = setup_tenant_and_account
        csv_path = FIXTURES_DIR / "scenario_02_base64_attach.csv"

        async with FakeClient(
            csv_path=csv_path,
            proxy_url="http://testserver",
            tenant_id=tenant_id,
            account_id=account_id,
        ) as client:
            async def patched_submit(messages):
                resp = await http_client.post(
                    "/messages/add_batch",
                    json={"messages": messages},
                )
                return resp.json()

            client._submit_messages_to_proxy = patched_submit

            async with httpx.AsyncClient() as http:
                await http.post(
                    f"{client.base_url}/sync",
                    json={"delivery_report": []},
                )

            # Check messages have attachments
            resp = await http_client.get(
                "/messages/list",
                params={"tenant_id": tenant_id},
            )
            messages = resp.json()
            assert len(messages) == 2

            # Both should have attachments in message (decoded payload)
            for msg in messages:
                message = msg.get("message", {})
                assert "attachments" in message, f"No attachments in {msg}"
                assert len(message["attachments"]) == 1

    @pytest.mark.asyncio
    async def test_scenario_mixed_attachments(
        self,
        http_client: httpx.AsyncClient,
        setup_tenant_and_account: tuple[str, str],
    ):
        """Mixed attachment types are handled correctly."""
        tenant_id, account_id = setup_tenant_and_account
        csv_path = FIXTURES_DIR / "scenario_05_mixed_attach.csv"

        async with FakeClient(
            csv_path=csv_path,
            proxy_url="http://testserver",
            tenant_id=tenant_id,
            account_id=account_id,
        ) as client:
            async def patched_submit(messages):
                resp = await http_client.post(
                    "/messages/add_batch",
                    json={"messages": messages},
                )
                return resp.json()

            client._submit_messages_to_proxy = patched_submit

            async with httpx.AsyncClient() as http:
                await http.post(
                    f"{client.base_url}/sync",
                    json={"delivery_report": []},
                )

            # Check messages
            resp = await http_client.get(
                "/messages/list",
                params={"tenant_id": tenant_id},
            )
            messages = resp.json()
            assert len(messages) == 4

            # Count attachments by type
            attachment_modes = {}
            for msg in messages:
                message = msg.get("message", {})
                attachments = message.get("attachments", [])
                if attachments:
                    mode = attachments[0].get("fetch_mode", "none")
                    attachment_modes[mode] = attachment_modes.get(mode, 0) + 1

            # Should have base64, storage, endpoint (1 message has no attachment)
            assert "base64" in attachment_modes
            assert "storage" in attachment_modes
            assert "endpoint" in attachment_modes


__all__ = ["FakeClient", "FIXTURES_DIR"]
