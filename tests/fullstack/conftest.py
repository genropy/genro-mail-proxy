# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Pytest fixtures for fullstack integration tests."""

from __future__ import annotations

import imaplib
import subprocess

import pytest
import pytest_asyncio

httpx = pytest.importorskip("httpx")

from .helpers import (
    MAILPROXY_URL,
    MAILPROXY_TOKEN,
    CLIENT_TENANT1_URL,
    CLIENT_TENANT2_URL,
    DOVECOT_IMAP_HOST,
    DOVECOT_IMAP_PORT,
    DOVECOT_BOUNCE_USER,
    DOVECOT_BOUNCE_PASS,
)

# Mark all tests in this package as fullstack
pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]


# ============================================
# API CLIENT FIXTURES
# ============================================

@pytest.fixture
def api_headers():
    """Standard API headers with auth token."""
    return {
        "X-API-Token": MAILPROXY_TOKEN,
        "Content-Type": "application/json",
    }


@pytest_asyncio.fixture
async def api_client(api_headers):
    """HTTP client for API calls."""
    async with httpx.AsyncClient(
        base_url=MAILPROXY_URL,
        headers=api_headers,
        timeout=30.0,
    ) as client:
        yield client


# ============================================
# TENANT SETUP FIXTURES
# ============================================

@pytest_asyncio.fixture
async def setup_test_tenants(api_client):
    """Setup two test tenants with their SMTP accounts."""
    # Create tenant1
    tenant1_data = {
        "id": "test-tenant-1",
        "name": "Test Tenant 1",
        "client_base_url": CLIENT_TENANT1_URL,
        "client_sync_path": "/proxy_sync",
        "client_auth": {"method": "none"},
        "active": True,
    }
    resp = await api_client.post("/tenant", json=tenant1_data)
    assert resp.status_code in (200, 201, 409), resp.text

    # Create account for tenant1
    account1_data = {
        "id": "test-account-1",
        "tenant_id": "test-tenant-1",
        "host": "mailhog-tenant1",  # Docker network name
        "port": 1025,
        "use_tls": False,
    }
    resp = await api_client.post("/account", json=account1_data)
    assert resp.status_code in (200, 201, 409), resp.text

    # Create tenant2
    tenant2_data = {
        "id": "test-tenant-2",
        "name": "Test Tenant 2",
        "client_base_url": CLIENT_TENANT2_URL,
        "client_sync_path": "/proxy_sync",
        "client_auth": {"method": "bearer", "token": "tenant2-secret-token"},
        "active": True,
    }
    resp = await api_client.post("/tenant", json=tenant2_data)
    assert resp.status_code in (200, 201, 409), resp.text

    # Create account for tenant2
    account2_data = {
        "id": "test-account-2",
        "tenant_id": "test-tenant-2",
        "host": "mailhog-tenant2",
        "port": 1025,
        "use_tls": False,
    }
    resp = await api_client.post("/account", json=account2_data)
    assert resp.status_code in (200, 201, 409), resp.text

    return {"tenant1": tenant1_data, "tenant2": tenant2_data}


# ============================================
# IMAP FIXTURES
# ============================================

@pytest.fixture
def imap_bounce():
    """IMAP client connected to bounce mailbox for testing.

    Yields an imaplib.IMAP4 connection to Dovecot configured for
    bounce email injection and verification.
    """
    try:
        M = imaplib.IMAP4(DOVECOT_IMAP_HOST, DOVECOT_IMAP_PORT)
        M.login(DOVECOT_BOUNCE_USER, DOVECOT_BOUNCE_PASS)
        M.select("INBOX")
        yield M
        M.logout()
    except Exception:
        pytest.skip("Dovecot IMAP server not available")


@pytest.fixture
def clean_imap(imap_bounce):
    """Clear IMAP mailbox before and after test."""
    def _clear():
        _, message_ids = imap_bounce.search(None, "ALL")
        if message_ids[0]:
            for msg_id in message_ids[0].split():
                imap_bounce.store(msg_id, "+FLAGS", "\\Deleted")
            imap_bounce.expunge()

    _clear()
    yield imap_bounce
    _clear()


# ============================================
# BOUNCE TENANT FIXTURE
# ============================================

@pytest_asyncio.fixture
async def setup_bounce_tenant(api_client):
    """Setup a tenant configured for bounce detection testing."""
    tenant_data = {
        "id": "bounce-tenant",
        "name": "Bounce Test Tenant",
        "client_base_url": CLIENT_TENANT1_URL,
        "client_sync_path": "/proxy_sync",
        "client_auth": {"method": "none"},
        "active": True,
    }
    resp = await api_client.post("/tenant", json=tenant_data)
    assert resp.status_code in (200, 201, 409), resp.text

    account_data = {
        "id": "bounce-account",
        "tenant_id": "bounce-tenant",
        "host": "mailhog-tenant1",
        "port": 1025,
        "use_tls": False,
    }
    resp = await api_client.post("/account", json=account_data)
    assert resp.status_code in (200, 201, 409), resp.text

    return {"tenant": tenant_data, "account": account_data}


# ============================================
# DX: ON-FAILURE DIAGNOSTICS
# ============================================

@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Collect diagnostics on test failure."""
    outcome = yield
    report = outcome.get_result()

    if report.when == "call" and report.failed:
        # Only for fullstack tests
        markers = [m.name for m in item.iter_markers()]
        if "fullstack" not in markers:
            return

        print("\n" + "=" * 60)
        print("FAILURE DIAGNOSTICS")
        print("=" * 60)

        # Docker service status
        try:
            result = subprocess.run(
                ["docker", "compose", "-f",
                 "tests/docker/docker-compose.fulltest.yml", "ps"],
                capture_output=True, text=True, timeout=10, cwd="."
            )
            print(f"\n--- Docker Status ---\n{result.stdout}")
        except Exception as e:
            print(f"Could not get Docker status: {e}")

        # Mail proxy logs (last 20 lines)
        try:
            result = subprocess.run(
                ["docker", "compose", "-f",
                 "tests/docker/docker-compose.fulltest.yml",
                 "logs", "mailproxy", "--tail", "20"],
                capture_output=True, text=True, timeout=10, cwd="."
            )
            print(f"\n--- Mail Proxy Logs ---\n{result.stdout}")
        except Exception as e:
            print(f"Could not get mailproxy logs: {e}")

        # MailHog message count
        try:
            import httpx as hx
            resp = hx.get("http://localhost:8025/api/v2/messages", timeout=5)
            count = len(resp.json().get("items", []))
            print(f"\n--- MailHog T1 Messages: {count} ---")
        except Exception as e:
            print(f"Could not check MailHog: {e}")

        print("=" * 60)
