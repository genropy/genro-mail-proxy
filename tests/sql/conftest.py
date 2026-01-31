# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""PostgreSQL fixtures for database tests.

These fixtures connect to an existing PostgreSQL container running on port 5433.
Start the container with: docker compose -f tests/_legacy/docker/docker-compose.fulltest.yml up -d db

Connection: postgresql://mailproxy:testpassword@localhost:5433/mailproxy
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio

# Default PostgreSQL URL for tests (can be overridden via environment)
PG_URL = os.environ.get(
    "GMP_TEST_PG_URL",
    "postgresql://mailproxy:testpassword@localhost:5433/mailproxy"
)


def pytest_configure(config):
    """Register postgres marker."""
    config.addinivalue_line(
        "markers",
        "postgres: marks tests requiring PostgreSQL database"
    )


@pytest.fixture(scope="session")
def pg_url() -> str:
    """Return PostgreSQL connection URL."""
    return PG_URL


@pytest_asyncio.fixture
async def pg_db(pg_url: str) -> AsyncGenerator:
    """Create a SqlDb instance connected to PostgreSQL.

    Creates a fresh test schema for each test run to ensure isolation.
    Drops all tables before and after each test.
    """
    from core.mail_proxy.proxy_base import MailProxyBase
    from core.mail_proxy.proxy_config import ProxyConfig

    # Create proxy to get all tables registered
    proxy = MailProxyBase(ProxyConfig(db_path=pg_url))
    await proxy.db.connect()

    # Drop all tables first to ensure clean state (CASCADE handles FK order)
    # Include test tables that may be created by tests
    for table_name in ["test_items", "auto_items", "no_pk_items", "message_events", "messages", "accounts", "tenants", "storage_nodes", "instance", "command_log"]:
        with contextlib.suppress(Exception):
            await proxy.db.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')

    # Create tables in FK dependency order (PostgreSQL enforces FK constraints)
    # Order: independent tables first, then tables with FK references
    table_order = ["instance", "command_log", "tenants", "accounts", "messages", "message_events"]
    for table_name in table_order:
        if table_name in proxy.db.tables:
            await proxy.db.tables[table_name].create_schema()

    yield proxy.db

    # Cleanup: drop all tables (including test tables)
    for table_name in ["test_items", "auto_items", "no_pk_items", "message_events", "messages", "accounts", "tenants", "storage_nodes", "instance", "command_log"]:
        with contextlib.suppress(Exception):
            await proxy.db.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')

    await proxy.close()


@pytest_asyncio.fixture
async def pg_proxy(pg_url: str) -> AsyncGenerator:
    """Create a full MailProxy instance with PostgreSQL backend.

    Useful for testing complete workflows with the proxy.
    """
    from core.mail_proxy.proxy import MailProxy
    from core.mail_proxy.proxy_config import ProxyConfig

    config = ProxyConfig(db_path=pg_url, test_mode=True, start_active=False)
    proxy = MailProxy(config)
    await proxy.db.connect()

    # Drop all tables first
    for table_name in ["message_events", "messages", "accounts", "tenants", "storage_nodes", "instance", "command_log"]:
        with contextlib.suppress(Exception):
            await proxy.db.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')

    # Create tables in FK dependency order
    table_order = ["instance", "command_log", "tenants", "accounts", "messages", "message_events"]
    for table_name in table_order:
        if table_name in proxy.db.tables:
            await proxy.db.tables[table_name].create_schema()

    yield proxy

    # Cleanup
    for table_name in ["message_events", "messages", "accounts", "tenants", "storage_nodes", "instance", "command_log"]:
        with contextlib.suppress(Exception):
            await proxy.db.execute(f'DROP TABLE IF EXISTS "{table_name}" CASCADE')

    await proxy.close()


def skip_if_no_postgres():
    """Skip test if PostgreSQL is not available."""
    import socket

    # Extract host and port from URL
    # postgresql://mailproxy:testpassword@localhost:5433/mailproxy
    host = "localhost"
    port = 5433

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((host, port))
        sock.close()
        if result != 0:
            pytest.skip(f"PostgreSQL not available at {host}:{port}")
    except Exception as e:
        pytest.skip(f"PostgreSQL connection test failed: {e}")


@pytest.fixture(autouse=True)
def check_postgres_available():
    """Auto-skip tests if PostgreSQL is not available."""
    skip_if_no_postgres()
