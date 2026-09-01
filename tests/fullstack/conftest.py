# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Pytest fixtures for fullstack integration tests."""

from __future__ import annotations

import imaplib
import re
import subprocess
from pathlib import Path

import pytest
import pytest_asyncio

from tests import api_routes

httpx = pytest.importorskip("httpx")

from .helpers import (
    MAILPROXY_URL,
    MAILPROXY_TOKEN,
    MAILHOG_TENANT1_API,
    MAILHOG_TENANT2_API,
    MAILHOG_TENANT1_SMTP,
    MAILHOG_TENANT2_SMTP,
    CLIENT_TENANT1_URL,
    CLIENT_TENANT2_URL,
    DOVECOT_IMAP_HOST,
    DOVECOT_IMAP_PORT,
    DOVECOT_BOUNCE_USER,
    DOVECOT_BOUNCE_PASS,
    DOVECOT_POLL_INTERVAL,
    DOVECOT_PEC_USER,
    DOVECOT_PEC_PASS,
    is_dovecot_available,
    is_pec_imap_available,
)

# The fullstack and asyncio markers are declared by each test module: pytest
# reads pytestmark in modules and classes only, never in a conftest.


# ============================================
# SUITE STATE
# ============================================

COMPOSE_FILE = Path(__file__).resolve().parents[2] / "tests/docker/docker-compose.fulltest.yml"

# Tenants the tests create carry a unix timestamp in their id and are never
# removed; the fixed ones the fixtures below create do not. Every dispatch
# cycle with no work to do still round-trips the whole table, so the sweep
# gets slower every run the suite is left to accumulate.
THROWAWAY_TENANT_ID = re.compile(r"\d{10}")


def restart_mailhog() -> bool:
    """Restart both MailHog services; True when docker actually did it.

    MailHog's RSS follows the bytes it has ever received and clearing the
    mailbox returns none of it (measured: unchanged across a DELETE), so one
    full run takes tenant1 from 16 MiB to 1.56 GiB. Only a restart puts that
    back. Addressed by compose SERVICE name because the container names carry
    the compose project, which is just whatever the directory is called.
    """
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(COMPOSE_FILE),
             "restart", "mailhog-tenant1", "mailhog-tenant2"],
            check=True, capture_output=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return True


@pytest.fixture(scope="session", autouse=True)
def empty_test_queues():
    """Reset the shared stack once per run: MailHog, the queues, the tenants.

    The stack is long-lived, so without this each run inherits the previous
    one's state: leftover pending and deferred messages are picked up by the
    dispatch cycles a later test triggers, and counts like "max 2 sent for this
    account" then read a queue somebody else filled.

    MailHog is restarted rather than emptied, because emptying it frees no
    memory (see restart_mailhog). Where docker is not reachable from the test
    process the mailboxes are emptied instead: that keeps the suite runnable,
    and it is the isolation half that matters there — the memory half only
    bites a machine running the suite over and over.

    With no stack up at all the proxy is unreachable and the reset has nothing
    to do. This fixture is session-scoped, so it runs before the module-scoped
    guards that skip those tests: it must return quietly, or every dependent
    test becomes an ERROR before its own skip is ever evaluated.
    """
    mailhog_restarted = restart_mailhog()
    try:
        _reset_stack_state(mailhog_restarted)
    except httpx.HTTPError:
        return


def _reset_stack_state(mailhog_restarted: bool) -> None:
    """Empty the queues and drop the throwaway tenants; raises when unreachable."""
    with httpx.Client(
        base_url=MAILPROXY_URL, headers={"X-API-Token": MAILPROXY_TOKEN}, timeout=30.0
    ) as client:
        resp = client.get(api_routes.tenants())
        if resp.status_code != 200:
            return
        for tenant in resp.json().get("tenants", []):
            tenant_id = tenant["id"]
            if THROWAWAY_TENANT_ID.search(tenant_id):
                client.delete(api_routes.tenant(tenant_id))
                continue
            resp = client.get(api_routes.messages(tenant_id=tenant_id))
            if resp.status_code != 200:
                continue
            ids = [m["id"] for m in resp.json().get("messages", [])]
            if ids:
                client.post(
                    api_routes.delete_messages(tenant_id=tenant_id), json={"ids": ids}
                )
        if not mailhog_restarted:
            for api in (MAILHOG_TENANT1_API, MAILHOG_TENANT2_API):
                client.delete(f"{api}/api/v1/messages")


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
        "client_sync_path": api_routes.CLIENT_SYNC_PATH,
        "client_auth": {"method": "none"},
        "active": True,
    }
    resp = await api_client.post(api_routes.TENANT, json=tenant1_data)
    assert resp.status_code in (200, 201, 409), resp.text

    # Create account for tenant1
    account1_data = {
        "id": "test-account-1",
        "tenant_id": "test-tenant-1",
        "host": MAILHOG_TENANT1_SMTP[0],
        "port": MAILHOG_TENANT1_SMTP[1],
        "use_tls": False,
    }
    resp = await api_client.post(api_routes.ACCOUNT, json=account1_data)
    assert resp.status_code in (200, 201, 409), resp.text

    # Create tenant2
    tenant2_data = {
        "id": "test-tenant-2",
        "name": "Test Tenant 2",
        "client_base_url": CLIENT_TENANT2_URL,
        "client_sync_path": api_routes.CLIENT_SYNC_PATH,
        "client_auth": {"method": "bearer", "token": "tenant2-secret-token"},
        "active": True,
    }
    resp = await api_client.post(api_routes.TENANT, json=tenant2_data)
    assert resp.status_code in (200, 201, 409), resp.text

    # Create account for tenant2
    account2_data = {
        "id": "test-account-2",
        "tenant_id": "test-tenant-2",
        "host": MAILHOG_TENANT2_SMTP[0],
        "port": MAILHOG_TENANT2_SMTP[1],
        "use_tls": False,
    }
    resp = await api_client.post(api_routes.ACCOUNT, json=account2_data)
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
        "client_sync_path": api_routes.CLIENT_SYNC_PATH,
        "client_auth": {"method": "none"},
        "active": True,
    }
    resp = await api_client.post(api_routes.TENANT, json=tenant_data)
    assert resp.status_code in (200, 201, 409), resp.text

    account_data = {
        "id": "bounce-account",
        "tenant_id": "bounce-tenant",
        "host": MAILHOG_TENANT1_SMTP[0],
        "port": MAILHOG_TENANT1_SMTP[1],
        "use_tls": False,
    }
    resp = await api_client.post(api_routes.ACCOUNT, json=account_data)
    assert resp.status_code in (200, 201, 409), resp.text

    return {"tenant": tenant_data, "account": account_data}


@pytest_asyncio.fixture
async def configure_bounce_receiver(api_client):
    """Configure BounceReceiver via API for live testing.

    This fixture:
    1. Updates instance table with bounce config
    2. Calls /instance/reload-bounce to apply the config
    3. Yields the configuration
    4. Disables bounce on teardown
    """
    if not is_dovecot_available():
        pytest.skip("Dovecot IMAP server not available")

    # Configure bounce via API
    config = {
        "bounce_enabled": True,
        "bounce_imap_host": DOVECOT_IMAP_HOST,
        "bounce_imap_port": DOVECOT_IMAP_PORT,
        "bounce_imap_user": DOVECOT_BOUNCE_USER,
        "bounce_imap_password": DOVECOT_BOUNCE_PASS,
        "bounce_imap_ssl": False,
        "bounce_poll_interval": DOVECOT_POLL_INTERVAL,
    }

    resp = await api_client.put(api_routes.INSTANCE, json=config)
    assert resp.status_code == 200, f"Failed to update instance: {resp.text}"

    resp = await api_client.post(api_routes.INSTANCE_RELOAD_BOUNCE)
    assert resp.status_code == 200, f"Failed to reload bounce: {resp.text}"

    yield config

    # Teardown: disable bounce
    await api_client.put(api_routes.INSTANCE, json={"bounce_enabled": False})
    await api_client.post(api_routes.INSTANCE_RELOAD_BOUNCE)


# ============================================
# PEC TENANT FIXTURE
# ============================================

@pytest_asyncio.fixture
async def setup_pec_tenant(api_client):
    """Setup a tenant with a PEC account for testing PEC receipt handling."""
    tenant_data = {
        "id": "pec-tenant",
        "name": "PEC Test Tenant",
        "client_base_url": CLIENT_TENANT1_URL,
        "client_sync_path": api_routes.CLIENT_SYNC_PATH,
        "client_auth": {"method": "none"},
        "active": True,
    }
    resp = await api_client.post(api_routes.TENANT, json=tenant_data)
    assert resp.status_code in (200, 201, 409), resp.text

    # Create a PEC account with IMAP configuration for receipt polling
    pec_account_data = {
        "id": "pec-account",
        "tenant_id": "pec-tenant",
        "host": MAILHOG_TENANT1_SMTP[0],
        "port": MAILHOG_TENANT1_SMTP[1],
        "use_tls": False,
        "is_pec_account": True,
        "imap_host": DOVECOT_IMAP_HOST,
        "imap_port": DOVECOT_IMAP_PORT,
        "imap_user": DOVECOT_PEC_USER,
        "imap_password": DOVECOT_PEC_PASS,
        "imap_ssl": False,
    }
    resp = await api_client.post(api_routes.ACCOUNT, json=pec_account_data)
    assert resp.status_code in (200, 201, 409), resp.text

    return {"tenant": tenant_data, "account": pec_account_data}


@pytest.fixture
def imap_pec():
    """IMAP client connected to PEC mailbox for testing.

    Yields an imaplib.IMAP4 connection to Dovecot configured for
    PEC receipt injection and verification.
    """
    try:
        M = imaplib.IMAP4(DOVECOT_IMAP_HOST, DOVECOT_IMAP_PORT)
        M.login(DOVECOT_PEC_USER, DOVECOT_PEC_PASS)
        M.select("INBOX")
        yield M
        M.logout()
    except Exception:
        pytest.skip("Dovecot PEC IMAP mailbox not available")


@pytest.fixture
def clean_pec_imap(imap_pec):
    """Clear PEC IMAP mailbox before and after test."""
    def _clear():
        _, message_ids = imap_pec.search(None, "ALL")
        if message_ids[0]:
            for msg_id in message_ids[0].split():
                imap_pec.store(msg_id, "+FLAGS", "\\Deleted")
            imap_pec.expunge()

    _clear()
    yield imap_pec
    _clear()


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
            resp = hx.get(f"{MAILHOG_TENANT1_API}/api/v2/messages", timeout=5)
            count = len(resp.json().get("items", []))
            print(f"\n--- MailHog T1 Messages: {count} ---")
        except Exception as e:
            print(f"Could not check MailHog: {e}")

        print("=" * 60)
