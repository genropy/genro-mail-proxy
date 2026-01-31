# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Fullstack tests for SMTP delivery via Mailpit.

Tests the complete flow using HTTP APIs only:
1. Submit messages via HTTP API (POST /messages/add_batch)
2. Proxy sends via SMTP to Mailpit
3. Verify delivery via Mailpit REST API
4. (Optional) Inject bounces via IMAP and verify handling

Run with: pytest tests/fullstack/ -v -m fullstack
Requires: docker compose up -d (in tests/fullstack/)
"""

from __future__ import annotations

import asyncio
import uuid

import httpx
import pytest

from .conftest import FIXTURES_DIR, MailpitAPI


def unique_id(prefix: str = "test") -> str:
    """Generate a unique message ID for each test run."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"

# Proxy API base URL (running in Docker)
PROXY_URL = "http://localhost:8000"


class TestSMTPDeliveryViaAPI:
    """Test SMTP delivery through HTTP API calls."""

    @pytest.fixture
    async def http_client(self):
        """Create an async HTTP client for API calls."""
        async with httpx.AsyncClient(base_url=PROXY_URL, timeout=30.0) as client:
            yield client

    @pytest.fixture
    async def setup_tenant(self, http_client: httpx.AsyncClient):
        """Ensure test tenant and account exist."""
        # Create tenant
        resp = await http_client.post(
            "/tenants/add",
            json={"id": "test", "name": "Test Tenant"},
        )
        # Ignore if already exists
        if resp.status_code not in (200, 201, 409):
            resp.raise_for_status()

        # Create account pointing to Mailpit
        resp = await http_client.post(
            "/accounts/add",
            json={
                "id": "mailpit",
                "tenant_id": "test",
                "host": "mailpit",  # Docker service name
                "port": 1025,
                "use_tls": False,
            },
        )
        if resp.status_code not in (200, 201, 409):
            resp.raise_for_status()

    async def test_simple_message_delivery(
        self, setup_tenant, http_client: httpx.AsyncClient, mailpit_api: MailpitAPI
    ):
        """Send a single message via API and verify it arrives in Mailpit."""
        await mailpit_api.delete_all()

        # Submit message via HTTP API
        msg_id = unique_id("simple")
        subject = f"API Test Simple Delivery {msg_id}"
        resp = await http_client.post(
            "/messages/add_batch",
            json={
                "messages": [
                    {
                        "id": msg_id,
                        "tenant_id": "test",
                        "account_id": "mailpit",
                        "from": "sender@test.com",
                        "to": ["recipient@test.com"],
                        "subject": subject,
                        "body": "Hello from fullstack API test!",
                    }
                ]
            },
        )
        resp.raise_for_status()
        result = resp.json()
        assert result.get("queued", 0) == 1

        # Trigger dispatch via API (run_now triggers immediate dispatch)
        resp = await http_client.post("/instance/run_now", json={})
        resp.raise_for_status()

        # Verify message arrived in Mailpit
        await asyncio.sleep(2.0)
        msg = await mailpit_api.find_by_subject(subject)
        assert msg is not None, "Message not found in Mailpit"
        assert "recipient@test.com" in str(msg.get("To", []))

    async def test_multiple_messages_delivery(
        self, setup_tenant, http_client: httpx.AsyncClient, mailpit_api: MailpitAPI
    ):
        """Send multiple messages via API and verify all arrive."""
        await mailpit_api.delete_all()

        # Submit batch of messages with unique IDs
        batch_prefix = unique_id("batch")
        messages = [
            {
                "id": f"{batch_prefix}-{i:03d}",
                "tenant_id": "test",
                "account_id": "mailpit",
                "from": "sender@test.com",
                "to": [f"recipient{i}@test.com"],
                "subject": f"Batch API Test {batch_prefix} {i}",
                "body": f"Message {i} content",
            }
            for i in range(5)
        ]

        resp = await http_client.post("/messages/add_batch", json={"messages": messages})
        resp.raise_for_status()
        result = resp.json()
        assert result.get("queued", 0) == 5

        # Trigger dispatch
        resp = await http_client.post("/instance/run_now", json={})
        resp.raise_for_status()

        await asyncio.sleep(3.0)
        count = await mailpit_api.count_messages()
        assert count == 5, f"Expected 5 messages, got {count}"

    async def test_message_status_tracking(
        self, setup_tenant, http_client: httpx.AsyncClient, mailpit_api: MailpitAPI
    ):
        """Verify message status is updated after delivery."""
        await mailpit_api.delete_all()

        # Submit message with unique ID
        msg_id = unique_id("status")
        resp = await http_client.post(
            "/messages/add_batch",
            json={
                "messages": [
                    {
                        "id": msg_id,
                        "tenant_id": "test",
                        "account_id": "mailpit",
                        "from": "sender@test.com",
                        "to": ["recipient@test.com"],
                        "subject": f"Status Tracking Test {msg_id}",
                        "body": "Test body",
                    }
                ]
            },
        )
        resp.raise_for_status()

        # Check initial status (GET with query params)
        resp = await http_client.get(
            "/messages/get",
            params={"message_id": msg_id, "tenant_id": "test"}
        )
        resp.raise_for_status()
        msg = resp.json()
        assert msg["status"] in ("pending", "queued", "deferred")

        # Dispatch
        resp = await http_client.post("/instance/run_now", json={})
        resp.raise_for_status()

        await asyncio.sleep(2.0)

        # Check final status
        resp = await http_client.get(
            "/messages/get",
            params={"message_id": msg_id, "tenant_id": "test"}
        )
        resp.raise_for_status()
        msg = resp.json()
        assert msg["status"] == "sent", f"Expected 'sent', got '{msg['status']}'"


class TestCSVScenariosViaAPI:
    """Test scenarios defined in CSV files, submitted via API."""

    @pytest.fixture
    async def http_client(self):
        """Create an async HTTP client."""
        async with httpx.AsyncClient(base_url=PROXY_URL, timeout=30.0) as client:
            yield client

    @pytest.fixture
    async def setup_tenant(self, http_client: httpx.AsyncClient):
        """Ensure test tenant and account exist."""
        await http_client.post("/tenants/add", json={"id": "test", "name": "Test Tenant"})
        await http_client.post(
            "/accounts/add",
            json={"id": "mailpit", "tenant_id": "test", "host": "mailpit", "port": 1025, "use_tls": False},
        )

    async def test_scenario_from_csv(
        self, setup_tenant, http_client: httpx.AsyncClient, mailpit_api: MailpitAPI
    ):
        """Load messages from CSV and submit via API."""
        import csv

        await mailpit_api.delete_all()

        csv_path = FIXTURES_DIR / "scenario_with_bounces.csv"
        if not csv_path.exists():
            pytest.skip(f"CSV not found: {csv_path}")

        # Read CSV and build messages with unique prefix
        messages = []
        expected = {}
        run_prefix = unique_id("csv")

        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Add unique prefix to avoid collisions with previous runs
                msg_id = f"{run_prefix}-{row['id']}"
                subject = f"{row['subject']} ({run_prefix})"
                expected[msg_id] = {
                    "status": row.get("expected_status", "sent"),
                    "bounce": row.get("simulate_bounce", ""),
                    "subject": subject,
                }

                # Only add messages that should be sent (not pre-bounced)
                if row.get("simulate_bounce", "") == "":
                    messages.append({
                        "id": msg_id,
                        "tenant_id": "test",
                        "account_id": "mailpit",
                        "from": row["from"],
                        "to": [row["to"]],
                        "subject": subject,
                        "body": row.get("body", ""),
                    })

        # Submit via API
        if messages:
            resp = await http_client.post("/messages/add_batch", json={"messages": messages})
            resp.raise_for_status()

            # Dispatch
            resp = await http_client.post("/instance/run_now", json={})
            resp.raise_for_status()

        await asyncio.sleep(3.0)

        # Verify non-bounce messages arrived
        for msg_id, exp in expected.items():
            if exp["bounce"] == "" and exp["status"] == "sent":
                msg = await mailpit_api.find_by_subject(exp["subject"])
                assert msg is not None, f"Message {msg_id} not delivered"


class TestBounceInjectionViaIMAP:
    """Test bounce handling via IMAP injection.

    Note: Mailpit does not support IMAP (only POP3), so these tests require
    a real IMAP server. They are skipped when running with Mailpit.
    """

    @pytest.fixture
    async def http_client(self):
        """Create an async HTTP client."""
        async with httpx.AsyncClient(base_url=PROXY_URL, timeout=30.0) as client:
            yield client

    @pytest.fixture
    async def setup_tenant(self, http_client: httpx.AsyncClient):
        """Ensure test tenant and account exist."""
        await http_client.post("/tenants/add", json={"id": "test", "name": "Test Tenant"})
        await http_client.post(
            "/accounts/add",
            json={"id": "mailpit", "tenant_id": "test", "host": "mailpit", "port": 1025, "use_tls": False},
        )

    @pytest.mark.skip(reason="Mailpit does not support IMAP - requires real IMAP server")
    async def test_inject_and_detect_bounce(
        self, setup_tenant, http_client: httpx.AsyncClient, mailpit_api: MailpitAPI, imap_injector
    ):
        """Inject a bounce via IMAP and verify proxy detects it."""
        await mailpit_api.delete_all()

        # Submit message via API
        resp = await http_client.post(
            "/messages/add_batch",
            json={
                "messages": [
                    {
                        "id": "bounce-api-001",
                        "tenant_id": "test",
                        "account_id": "mailpit",
                        "from": "sender@test.com",
                        "to": ["will-bounce@test.com"],
                        "subject": "This will bounce",
                        "body": "Test bounce detection via API",
                    }
                ]
            },
        )
        resp.raise_for_status()

        # Dispatch
        resp = await http_client.post("/instance/run_now", json={})
        resp.raise_for_status()
        await asyncio.sleep(2.0)

        # Get the message to find its Message-ID
        resp = await http_client.get(
            "/messages/get",
            params={"message_id": "bounce-api-001", "tenant_id": "test"}
        )
        resp.raise_for_status()
        msg = resp.json()
        message_id = msg.get("smtp_message_id", "<bounce-api-001@test.local>")

        # Inject bounce via IMAP
        with imap_injector:
            imap_injector.inject_hard_bounce(
                original_message_id=message_id,
                original_from="sender@test.com",
                original_to="will-bounce@test.com",
            )

        # Note: For full bounce detection, the proxy needs to poll IMAP
        # This test verifies the injection works and bounce message is in mailbox
        await asyncio.sleep(1.0)
        messages_in_mailpit = await mailpit_api.get_messages()

        # Should have original message + bounce notification
        assert len(messages_in_mailpit) >= 1
