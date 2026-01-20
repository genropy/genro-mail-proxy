"""Docker-based integration tests for multi-tenant email flow.

These tests require Docker and docker-compose to be installed.
Run with: pytest tests/test_docker_integration.py -v -m docker

The tests use MailHog containers for capturing SMTP emails and
HTTP echo servers for simulating tenant client endpoints.
"""

import asyncio
import os
from pathlib import Path

import pytest
import pytest_asyncio

# Skip all tests if httpx is not available
httpx = pytest.importorskip("httpx")

# Mark all tests in this module as docker tests
pytestmark = [pytest.mark.docker, pytest.mark.asyncio]

# Docker compose file path
COMPOSE_FILE = Path(__file__).parent / "docker" / "docker-compose.test.yml"

# Service URLs when running locally with docker-compose
MAILHOG_TENANT1_SMTP = ("localhost", 1025)
MAILHOG_TENANT1_API = "http://localhost:8025"
MAILHOG_TENANT2_SMTP = ("localhost", 1026)
MAILHOG_TENANT2_API = "http://localhost:8026"
CLIENT_TENANT1_URL = "http://localhost:8081"
CLIENT_TENANT2_URL = "http://localhost:8082"


def docker_compose_available() -> bool:
    """Check if docker-compose is available."""
    import shutil
    return shutil.which("docker-compose") is not None or shutil.which("docker") is not None


@pytest.fixture(scope="module")
def docker_services():
    """Start docker-compose services for the test module."""
    if not docker_compose_available():
        pytest.skip("Docker/docker-compose not available")

    import subprocess

    # Start services
    subprocess.run(
        ["docker-compose", "-f", str(COMPOSE_FILE), "up", "-d"],
        check=True,
        capture_output=True,
    )

    # Wait for services to be ready
    import time
    time.sleep(5)

    yield

    # Stop services
    subprocess.run(
        ["docker-compose", "-f", str(COMPOSE_FILE), "down", "-v"],
        check=True,
        capture_output=True,
    )


@pytest_asyncio.fixture
async def mail_proxy_core(tmp_path):
    """Create a mail proxy core instance configured for Docker services."""
    import types
    from async_mail_service.core import AsyncMailCore

    db_path = tmp_path / "docker_test.db"
    core = AsyncMailCore(
        db_path=str(db_path),
        start_active=True,
        test_mode=True,
    )
    await core.persistence.init_db()

    # Mock rate limiter
    class DummyRateLimiter:
        async def check_and_plan(self, account):
            return None
        async def log_send(self, account_id: str):
            pass

    # Mock metrics
    class DummyMetrics:
        def set_pending(self, value: int): pass
        def inc_sent(self, account_id: str): pass
        def inc_error(self, account_id: str): pass
        def inc_deferred(self, account_id: str): pass
        def inc_rate_limited(self, account_id: str): pass

    # Mock attachments
    class DummyAttachments:
        async def fetch(self, attachment):
            return (b"content", "file.txt")
        def guess_mime(self, filename):
            return "text", "plain"

    core.rate_limiter = DummyRateLimiter()
    core.metrics = DummyMetrics()
    core.attachments = DummyAttachments()
    core.logger = types.SimpleNamespace(
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
    )

    return core


@pytest_asyncio.fixture
async def setup_tenants(mail_proxy_core):
    """Setup two tenants with their SMTP accounts pointing to Docker MailHog instances."""
    core = mail_proxy_core

    # Create tenant1
    await core.persistence.add_tenant({
        "id": "tenant1",
        "name": "Tenant 1",
        "client_sync_url": f"{CLIENT_TENANT1_URL}/proxy_sync",
        "client_sync_auth": {"method": "none"},
        "active": True,
    })
    await core.handle_command("addAccount", {
        "id": "tenant1-smtp",
        "tenant_id": "tenant1",
        "host": MAILHOG_TENANT1_SMTP[0],
        "port": MAILHOG_TENANT1_SMTP[1],
        "use_tls": False,
    })

    # Create tenant2
    await core.persistence.add_tenant({
        "id": "tenant2",
        "name": "Tenant 2",
        "client_sync_url": f"{CLIENT_TENANT2_URL}/proxy_sync",
        "client_sync_auth": {"method": "bearer", "token": "tenant2-secret"},
        "active": True,
    })
    await core.handle_command("addAccount", {
        "id": "tenant2-smtp",
        "tenant_id": "tenant2",
        "host": MAILHOG_TENANT2_SMTP[0],
        "port": MAILHOG_TENANT2_SMTP[1],
        "use_tls": False,
    })

    return core


async def clear_mailhog(api_url: str):
    """Clear all messages from a MailHog instance."""
    async with httpx.AsyncClient() as client:
        await client.delete(f"{api_url}/api/v1/messages")


async def get_mailhog_messages(api_url: str) -> list:
    """Get all messages from a MailHog instance."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{api_url}/api/v2/messages")
        if resp.status_code == 200:
            return resp.json().get("items", [])
        return []


async def test_docker_services_available(docker_services):
    """Test that Docker services are running and accessible."""
    async with httpx.AsyncClient() as client:
        # Check MailHog tenant1
        resp = await client.get(f"{MAILHOG_TENANT1_API}/api/v2/messages")
        assert resp.status_code == 200

        # Check MailHog tenant2
        resp = await client.get(f"{MAILHOG_TENANT2_API}/api/v2/messages")
        assert resp.status_code == 200

        # Check echo server tenant1
        resp = await client.get(CLIENT_TENANT1_URL)
        assert resp.status_code == 200

        # Check echo server tenant2
        resp = await client.get(CLIENT_TENANT2_URL)
        assert resp.status_code == 200


async def test_send_email_to_tenant1_mailhog(docker_services, setup_tenants):
    """Test sending an email through tenant1's MailHog SMTP server."""
    core = setup_tenants

    # Clear any existing messages
    await clear_mailhog(MAILHOG_TENANT1_API)

    # Add a message for tenant1
    await core.handle_command("addMessages", {
        "messages": [{
            "id": "docker-msg-t1",
            "account_id": "tenant1-smtp",
            "from": "sender@tenant1.com",
            "to": ["recipient@example.com"],
            "subject": "Docker Test - Tenant 1",
            "body": "Hello from tenant 1 via Docker!",
        }]
    })

    # Process the SMTP cycle
    await core._process_smtp_cycle()

    # Wait a bit for MailHog to receive the message
    await asyncio.sleep(1)

    # Verify message was received by MailHog
    messages = await get_mailhog_messages(MAILHOG_TENANT1_API)
    assert len(messages) == 1

    msg = messages[0]
    assert msg["Content"]["Headers"]["Subject"][0] == "Docker Test - Tenant 1"
    assert msg["Content"]["Headers"]["From"][0] == "sender@tenant1.com"


async def test_send_email_to_tenant2_mailhog(docker_services, setup_tenants):
    """Test sending an email through tenant2's MailHog SMTP server."""
    core = setup_tenants

    # Clear any existing messages
    await clear_mailhog(MAILHOG_TENANT2_API)

    # Add a message for tenant2
    await core.handle_command("addMessages", {
        "messages": [{
            "id": "docker-msg-t2",
            "account_id": "tenant2-smtp",
            "from": "sender@tenant2.com",
            "to": ["recipient@example.com"],
            "subject": "Docker Test - Tenant 2",
            "body": "Hello from tenant 2 via Docker!",
        }]
    })

    # Process the SMTP cycle
    await core._process_smtp_cycle()

    # Wait a bit for MailHog to receive the message
    await asyncio.sleep(1)

    # Verify message was received by MailHog
    messages = await get_mailhog_messages(MAILHOG_TENANT2_API)
    assert len(messages) == 1

    msg = messages[0]
    assert msg["Content"]["Headers"]["Subject"][0] == "Docker Test - Tenant 2"
    assert msg["Content"]["Headers"]["From"][0] == "sender@tenant2.com"


async def test_tenant_isolation_smtp(docker_services, setup_tenants):
    """Test that emails are routed to the correct tenant's SMTP server."""
    core = setup_tenants

    # Clear all mailboxes
    await clear_mailhog(MAILHOG_TENANT1_API)
    await clear_mailhog(MAILHOG_TENANT2_API)

    # Add messages for both tenants
    await core.handle_command("addMessages", {
        "messages": [
            {
                "id": "isolation-msg-t1",
                "account_id": "tenant1-smtp",
                "from": "sender@tenant1.com",
                "to": ["user@example.com"],
                "subject": "Isolation Test - Tenant 1",
                "body": "This should go to MailHog 1",
            },
            {
                "id": "isolation-msg-t2",
                "account_id": "tenant2-smtp",
                "from": "sender@tenant2.com",
                "to": ["user@example.com"],
                "subject": "Isolation Test - Tenant 2",
                "body": "This should go to MailHog 2",
            },
        ]
    })

    # Process the SMTP cycle
    await core._process_smtp_cycle()

    # Wait for messages
    await asyncio.sleep(1)

    # Verify isolation - each MailHog should have exactly 1 message
    messages_t1 = await get_mailhog_messages(MAILHOG_TENANT1_API)
    messages_t2 = await get_mailhog_messages(MAILHOG_TENANT2_API)

    assert len(messages_t1) == 1
    assert len(messages_t2) == 1

    # Verify correct routing
    assert messages_t1[0]["Content"]["Headers"]["Subject"][0] == "Isolation Test - Tenant 1"
    assert messages_t2[0]["Content"]["Headers"]["Subject"][0] == "Isolation Test - Tenant 2"


async def test_batch_emails_same_tenant(docker_services, setup_tenants):
    """Test sending multiple emails for the same tenant."""
    core = setup_tenants

    # Clear mailbox
    await clear_mailhog(MAILHOG_TENANT1_API)

    # Add multiple messages for tenant1
    messages = []
    for i in range(5):
        messages.append({
            "id": f"batch-msg-{i}",
            "account_id": "tenant1-smtp",
            "from": "sender@tenant1.com",
            "to": [f"recipient{i}@example.com"],
            "subject": f"Batch Test Message {i}",
            "body": f"Batch message content {i}",
        })

    await core.handle_command("addMessages", {"messages": messages})

    # Process the SMTP cycle
    await core._process_smtp_cycle()

    # Wait for messages
    await asyncio.sleep(2)

    # Verify all messages were sent
    received = await get_mailhog_messages(MAILHOG_TENANT1_API)
    assert len(received) == 5


async def test_html_email_via_docker(docker_services, setup_tenants):
    """Test sending HTML email through Docker SMTP."""
    core = setup_tenants

    # Clear mailbox
    await clear_mailhog(MAILHOG_TENANT1_API)

    # Add HTML message
    await core.handle_command("addMessages", {
        "messages": [{
            "id": "html-docker-msg",
            "account_id": "tenant1-smtp",
            "from": "sender@tenant1.com",
            "to": ["recipient@example.com"],
            "subject": "HTML Docker Test",
            "body": "<html><body><h1>Hello Docker!</h1><p>This is an HTML email.</p></body></html>",
            "content_type": "html",
        }]
    })

    # Process the SMTP cycle
    await core._process_smtp_cycle()

    # Wait for message
    await asyncio.sleep(1)

    # Verify message
    messages = await get_mailhog_messages(MAILHOG_TENANT1_API)
    assert len(messages) == 1

    # Check content type header
    content_type = messages[0]["Content"]["Headers"].get("Content-Type", [""])[0]
    assert "text/html" in content_type


async def test_email_with_custom_headers(docker_services, setup_tenants):
    """Test sending email with custom headers through Docker SMTP."""
    core = setup_tenants

    # Clear mailbox
    await clear_mailhog(MAILHOG_TENANT1_API)

    # Add message with custom headers
    await core.handle_command("addMessages", {
        "messages": [{
            "id": "custom-headers-msg",
            "account_id": "tenant1-smtp",
            "from": "sender@tenant1.com",
            "to": ["recipient@example.com"],
            "subject": "Custom Headers Test",
            "body": "Testing custom headers",
            "reply_to": "reply@tenant1.com",
            "headers": {
                "X-Custom-Header": "custom-value",
                "X-Priority": "1",
            },
        }]
    })

    # Process the SMTP cycle
    await core._process_smtp_cycle()

    # Wait for message
    await asyncio.sleep(1)

    # Verify message
    messages = await get_mailhog_messages(MAILHOG_TENANT1_API)
    assert len(messages) == 1

    headers = messages[0]["Content"]["Headers"]
    assert headers.get("Reply-To", [""])[0] == "reply@tenant1.com"
    assert headers.get("X-Custom-Header", [""])[0] == "custom-value"
    assert headers.get("X-Priority", [""])[0] == "1"
