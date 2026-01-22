# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Minimal async SQL layer with adapter pattern.

Usage:
    adapter = create_adapter("/data/mail.db")  # SQLite (path)
    adapter = create_adapter("postgresql://user:pass@host/db")  # PostgreSQL

    await adapter.connect()
    rows = await adapter.fetch_all(
        "SELECT * FROM users WHERE active = :active",
        {"active": 1}
    )
    await adapter.close()
"""

from .base import DbAdapter
from .sqlite import SqliteAdapter

__all__ = [
    "DbAdapter",
    "SqliteAdapter",
    "create_adapter",
]


def create_adapter(connection_string: str) -> DbAdapter:
    """Create database adapter from connection string.

    Connection string formats:
        - "sqlite:/path/to/db.sqlite" or just "/path/to/db.sqlite"
        - "sqlite::memory:" for in-memory SQLite
        - "postgresql://user:pass@host:port/dbname"

    Args:
        connection_string: Database connection string.

    Returns:
        Configured DbAdapter instance.

    Raises:
        ValueError: If connection string format is invalid.
        ImportError: If postgresql requested but psycopg not installed.
    """
    # Handle bare paths as SQLite (backward compatibility)
    if connection_string.startswith("/") or connection_string == ":memory:":
        return SqliteAdapter(connection_string)

    # Parse "type:connection_info" format
    if ":" not in connection_string:
        raise ValueError(
            f"Invalid connection string: '{connection_string}'. "
            "Expected 'type:connection_info' or absolute path."
        )

    db_type, connection_info = connection_string.split(":", 1)
    db_type = db_type.lower()

    if db_type == "sqlite":
        return SqliteAdapter(connection_info)

    if db_type in ("postgresql", "postgres"):
        # Lazy import to avoid ImportError when psycopg not installed
        from .postgresql import PostgresAdapter

        # Reconstruct full DSN if needed
        if not connection_info.startswith("postgresql://"):
            dsn = f"postgresql:{connection_info}"
        else:
            dsn = connection_info
        return PostgresAdapter(dsn)

    raise ValueError(
        f"Unknown database type: '{db_type}'. "
        "Supported: sqlite, postgresql"
    )
