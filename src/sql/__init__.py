# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Async SQL layer with adapter pattern and table registration.

This package provides a lightweight ORM-like interface for SQLite and
PostgreSQL with table class registration and async operations.

Components:
    SqlDb: Database manager with table registry and schema management.
    Table: Base class for table definitions with Columns schema.
    DbAdapter: Abstract base for SQLite/PostgreSQL adapters.
    Column, Columns: Schema definition with types and constraints.

Example:
    Using SqlDb (recommended for table-based access)::

        from sql import SqlDb, Table, Columns, String, Integer

        class UsersTable(Table):
            name = "users"
            def configure(self):
                self.columns.column("id", String, unique=True)
                self.columns.column("name", String)
                self.columns.column("active", Integer, default=1)

        db = SqlDb("/data/app.db")
        await db.connect()
        db.add_table(UsersTable)
        await db.check_structure()

        user = await db.table("users").select_one(where={"id": "u1"})
        await db.close()

    Using adapter directly::

        adapter = get_adapter("postgresql://user:pass@host/db")
        await adapter.connect()
        rows = await adapter.fetch_all(
            "SELECT * FROM users WHERE active = :active",
            {"active": 1}
        )
        await adapter.close()

Note:
    Connection strings: "/path/to/db.sqlite" (SQLite), "sqlite::memory:"
    (in-memory), "postgresql://user:pass@host:port/dbname" (PostgreSQL).
"""

from .adapters import DbAdapter, get_adapter
from .column import Boolean, Column, Columns, Integer, String, Timestamp
from .sqldb import SqlDb
from .table import Table

__all__ = [
    # Main classes
    "SqlDb",
    "Table",
    # Column definitions
    "Column",
    "Columns",
    "Integer",
    "String",
    "Boolean",
    "Timestamp",
    # Adapters
    "DbAdapter",
    "get_adapter",
    # Backward compatibility
    "create_adapter",
]

# Backward compatibility alias
create_adapter = get_adapter
