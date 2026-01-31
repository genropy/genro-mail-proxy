# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Async database manager with adapter pattern and table registration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .adapters import DbAdapter, get_adapter

if TYPE_CHECKING:
    from .table import Table


class SqlDb:
    """Async database manager with adapter pattern.

    Supports multiple database types via adapters:
    - SQLite: "/path/to/db.sqlite" or "sqlite:/path/to/db"
    - PostgreSQL: "postgresql://user:pass@host/db"

    Features:
    - Table class registration via add_table()
    - Table access via table(name)
    - Schema creation and verification
    - CRUD operations via adapter
    - Encryption key access via parent.encryption_key

    Usage:
        db = SqlDb("/data/mail.db", parent=proxy)
        await db.connect()

        db.add_table(TenantsTable)
        db.add_table(AccountsTable)
        await db.check_structure()

        tenant = await db.table('tenants').select_one(where={"id": "acme"})

        await db.close()
    """

    def __init__(self, connection_string: str, parent: Any = None):
        """Initialize database manager.

        Args:
            connection_string: Database connection string.
            parent: Parent object (e.g., proxy) that provides encryption_key.
        """
        self.connection_string = connection_string
        self.parent = parent
        self.adapter: DbAdapter = get_adapter(connection_string)
        self.tables: dict[str, Table] = {}

    @property
    def encryption_key(self) -> bytes | None:
        """Get encryption key from parent. Returns None if not configured."""
        if self.parent is None:
            return None
        return getattr(self.parent, "encryption_key", None)

    async def connect(self) -> None:
        """Connect to database."""
        await self.adapter.connect()

    async def close(self) -> None:
        """Close database connection."""
        await self.adapter.close()

    def add_table(self, table_class: type[Table]) -> Table:
        """Register and instantiate a table class.

        Args:
            table_class: Table manager class (must have name attribute).

        Returns:
            The instantiated table.
        """
        if not hasattr(table_class, "name") or not table_class.name:
            raise ValueError(f"Table class {table_class.__name__} must define 'name'")

        instance = table_class(self)
        self.tables[instance.name] = instance
        return instance

    def table(self, name: str) -> Table:
        """Get table instance by name.

        Args:
            name: Table name.

        Returns:
            Table instance.

        Raises:
            ValueError: If table not registered.
        """
        if name not in self.tables:
            raise ValueError(f"Table '{name}' not registered. Use add_table() first.")
        return self.tables[name]

    async def check_structure(self) -> None:
        """Create all registered tables if they don't exist."""
        for table in self.tables.values():
            await table.create_schema()

    # -------------------------------------------------------------------------
    # Direct adapter access
    # -------------------------------------------------------------------------

    async def execute(self, query: str, params: dict[str, Any] | None = None) -> int:
        """Execute raw query, return affected row count."""
        return await self.adapter.execute(query, params)

    async def fetch_one(
        self, query: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Execute raw query, return single row."""
        return await self.adapter.fetch_one(query, params)

    async def fetch_all(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute raw query, return all rows."""
        return await self.adapter.fetch_all(query, params)

    async def commit(self) -> None:
        """Commit transaction."""
        await self.adapter.commit()

    async def rollback(self) -> None:
        """Rollback transaction."""
        await self.adapter.rollback()


__all__ = ["SqlDb"]
