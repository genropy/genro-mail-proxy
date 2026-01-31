# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Database adapters for SQLite and PostgreSQL.

This package provides async database adapters with a unified interface
for executing queries and CRUD operations.

Components:
    DbAdapter: Abstract base class defining the adapter interface.
    SqliteAdapter: SQLite adapter using aiosqlite with per-operation connections.
    PostgresAdapter: PostgreSQL adapter using psycopg3 with connection pooling.
    get_adapter: Factory function to create adapters from connection strings.

Example:
    Create adapter from connection string::

        from sql.adapters import get_adapter

        # SQLite
        adapter = get_adapter("/data/app.db")
        adapter = get_adapter("sqlite::memory:")

        # PostgreSQL
        adapter = get_adapter("postgresql://user:pass@localhost:5432/mydb")

        await adapter.connect()
        rows = await adapter.fetch_all("SELECT * FROM users")
        await adapter.close()

Note:
    PostgreSQL requires psycopg: `pip install genro-mail-proxy[postgresql]`.
    SQLite uses per-operation connections (no persistent pool).
"""

from .base import DbAdapter
from .sqlite import SqliteAdapter

__all__ = ["DbAdapter", "SqliteAdapter", "ADAPTERS", "get_adapter"]

# Adapter registry
ADAPTERS: dict[str, type[DbAdapter]] = {
    "sqlite": SqliteAdapter,
}


def get_adapter(connection_string: str) -> DbAdapter:
    """Create database adapter from connection string.

    Connection string formats:
        - "/path/to/db.sqlite" → SQLite (absolute path)
        - "./path/to/db.sqlite" → SQLite (relative path)
        - "sqlite:/path/to/db.sqlite" → SQLite
        - "sqlite::memory:" → SQLite in-memory
        - "postgresql://user:pass@host:port/dbname" → PostgreSQL

    Args:
        connection_string: Database connection string.

    Returns:
        Configured DbAdapter instance.

    Raises:
        ValueError: If connection string format is invalid.
        ImportError: If postgresql requested but psycopg not installed.
    """
    # Handle bare paths as SQLite (backward compatibility)
    # Accept absolute paths (/path), relative paths (./path), and :memory:
    if (
        connection_string.startswith("/")
        or connection_string.startswith("./")
        or connection_string == ":memory:"
    ):
        return SqliteAdapter(connection_string)

    # Parse "type:connection_info" format
    if ":" not in connection_string:
        raise ValueError(
            f"Invalid connection string: '{connection_string}'. "
            "Expected 'type:connection_info' or path (absolute or relative)."
        )

    db_type, connection_info = connection_string.split(":", 1)
    db_type = db_type.lower()

    if db_type == "sqlite":
        return SqliteAdapter(connection_info)

    if db_type in ("postgresql", "postgres"):
        # Lazy import to avoid ImportError when psycopg not installed
        from .postgresql import PostgresAdapter

        # Register if not already
        if "postgresql" not in ADAPTERS:
            ADAPTERS["postgresql"] = PostgresAdapter
            ADAPTERS["postgres"] = PostgresAdapter

        # Reconstruct full DSN if needed
        if not connection_info.startswith("postgresql://"):
            dsn = f"postgresql:{connection_info}"
        else:
            dsn = connection_info
        return PostgresAdapter(dsn)

    raise ValueError(f"Unknown database type: '{db_type}'. Supported: sqlite, postgresql")
