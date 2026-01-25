# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Fixtures for database integration tests using testcontainers."""

import pytest
import pytest_asyncio


@pytest.fixture(scope="session")
def pg_container():
    """Spin up a real PostgreSQL container for integration tests.

    Returns the connection URL for use with SqlDb or PostgresAdapter.
    The container is automatically stopped and removed after the test session.

    Requires Docker to be running.
    """
    try:
        from testcontainers.postgres import PostgresContainer
    except ImportError:
        pytest.skip("testcontainers not installed")

    with PostgresContainer("postgres:15") as postgres:
        # testcontainers returns 'postgresql+psycopg2://' but our SqlDb expects 'postgresql://'
        url = postgres.get_connection_url()
        url = url.replace("postgresql+psycopg2://", "postgresql://")
        yield url


@pytest_asyncio.fixture
async def pg_db(pg_container):
    """Create a SqlDb instance connected to PostgreSQL container.

    Yields a connected SqlDb instance with clean tables.
    Tables are dropped after each test to ensure isolation.
    """
    from mail_proxy.entities import AccountsTable, MessagesTable, SendLogTable, TenantsTable
    from mail_proxy.sql import SqlDb

    db = SqlDb(pg_container)
    await db.connect()

    # Register and create tables
    db.add_table(TenantsTable)
    db.add_table(AccountsTable)
    db.add_table(MessagesTable)
    db.add_table(SendLogTable)
    await db.check_structure()

    yield db

    # Cleanup: drop all tables in reverse order (to respect FK constraints)
    import contextlib
    for table_name in ["send_log", "messages", "accounts", "tenants"]:
        with contextlib.suppress(Exception):
            await db.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")

    await db.close()


@pytest_asyncio.fixture
async def pg_adapter(pg_container):
    """Create a raw PostgresAdapter connected to the container.

    Useful for lower-level tests that don't need table managers.
    """
    from mail_proxy.sql.adapters.postgresql import PostgresAdapter

    adapter = PostgresAdapter(pg_container)
    await adapter.connect()

    yield adapter

    await adapter.close()
