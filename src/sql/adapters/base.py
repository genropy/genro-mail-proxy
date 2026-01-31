# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Base adapter class for async database backends with CRUD helpers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


class DbAdapter(ABC):
    """Abstract base class for async database adapters with CRUD helpers.

    Provides a unified interface for SQLite and PostgreSQL with:
    - Connection management (connect, close)
    - Raw query execution (execute, fetch_one, fetch_all)
    - CRUD helpers (insert, select, update, delete)

    Subclasses must implement the abstract methods and set the placeholder
    attribute for parameter binding (`:name` for SQLite, `%(name)s` for PostgreSQL).
    """

    placeholder: str = ":name"  # Override in subclass

    def pk_column(self, name: str) -> str:
        """Return SQL definition for autoincrement primary key column."""
        return f'"{name}" INTEGER PRIMARY KEY'

    def for_update_clause(self) -> str:
        """Return FOR UPDATE clause if supported, empty string otherwise."""
        return ""

    @abstractmethod
    async def connect(self) -> None:
        """Establish database connection."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close database connection."""
        ...

    @abstractmethod
    async def execute(self, query: str, params: dict[str, Any] | None = None) -> int:
        """Execute query, return affected row count."""
        ...

    @abstractmethod
    async def execute_many(self, query: str, params_list: Sequence[dict[str, Any]]) -> int:
        """Execute query multiple times with different params (batch insert)."""
        ...

    @abstractmethod
    async def fetch_one(
        self, query: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Execute query, return single row as dict or None."""
        ...

    @abstractmethod
    async def fetch_all(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute query, return all rows as list of dicts."""
        ...

    @abstractmethod
    async def execute_script(self, script: str) -> None:
        """Execute multiple statements (for schema creation)."""
        ...

    @abstractmethod
    async def commit(self) -> None:
        """Commit current transaction."""
        ...

    @abstractmethod
    async def rollback(self) -> None:
        """Rollback current transaction."""
        ...

    # -------------------------------------------------------------------------
    # CRUD Helpers
    # -------------------------------------------------------------------------

    def _sql_name(self, name: str) -> str:
        """Return quoted SQL identifier for column/table name.

        Quotes with double quotes to handle reserved words like 'user'.
        Works for both SQLite and PostgreSQL.
        """
        return f'"{name}"'

    def _placeholder(self, name: str) -> str:
        """Return placeholder for named parameter."""
        return self.placeholder.replace("name", name)

    async def insert(self, table: str, values: dict[str, Any]) -> int:
        """Insert a row, return rowcount.

        Args:
            table: Table name.
            values: Column-value pairs.

        Returns:
            Number of affected rows (typically 1).
        """
        cols = list(values.keys())
        placeholders = ", ".join(self._placeholder(c) for c in cols)
        col_list = ", ".join(self._sql_name(c) for c in cols)
        query = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        return await self.execute(query, values)

    async def insert_returning_id(
        self, table: str, values: dict[str, Any], pk_col: str = "id"
    ) -> Any:
        """Insert a row and return the generated primary key.

        Override in subclasses for database-specific implementation
        (e.g., RETURNING for PostgreSQL, lastrowid for SQLite).

        Args:
            table: Table name.
            values: Column-value pairs.
            pk_col: Primary key column name.

        Returns:
            The generated primary key value, or None if not supported.
        """
        # Default: just insert and return None (subclasses override)
        await self.insert(table, values)
        return None

    async def select(
        self,
        table: str,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Select rows, return list of dicts.

        Args:
            table: Table name.
            columns: Columns to select (None = all).
            where: WHERE conditions (AND).
            order_by: ORDER BY clause.
            limit: LIMIT clause.

        Returns:
            List of row dicts.
        """
        cols_sql = ", ".join(self._sql_name(c) for c in columns) if columns else "*"
        query = f"SELECT {cols_sql} FROM {table}"

        params: dict[str, Any] = {}
        if where:
            conditions = [f"{self._sql_name(k)} = {self._placeholder(k)}" for k in where.keys()]
            query += " WHERE " + " AND ".join(conditions)
            params.update(where)

        if order_by:
            query += f" ORDER BY {order_by}"

        if limit:
            query += f" LIMIT {limit}"

        return await self.fetch_all(query, params)

    async def select_one(
        self,
        table: str,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Select single row, return dict or None."""
        results = await self.select(table, columns, where, limit=1)
        return results[0] if results else None

    async def update(self, table: str, values: dict[str, Any], where: dict[str, Any]) -> int:
        """Update rows, return rowcount.

        Args:
            table: Table name.
            values: Column-value pairs to update.
            where: WHERE conditions.

        Returns:
            Number of affected rows.
        """
        # Prefix value params to avoid collision with where params
        set_parts = [f"{self._sql_name(k)} = {self._placeholder('val_' + k)}" for k in values]
        where_parts = [f"{self._sql_name(k)} = {self._placeholder('whr_' + k)}" for k in where]

        query = f"UPDATE {table} SET {', '.join(set_parts)} WHERE {' AND '.join(where_parts)}"

        params = {f"val_{k}": v for k, v in values.items()}
        params.update({f"whr_{k}": v for k, v in where.items()})

        return await self.execute(query, params)

    async def delete(self, table: str, where: dict[str, Any]) -> int:
        """Delete rows, return rowcount.

        Args:
            table: Table name.
            where: WHERE conditions.

        Returns:
            Number of deleted rows.
        """
        where_parts = [f"{self._sql_name(k)} = {self._placeholder(k)}" for k in where]
        query = f"DELETE FROM {table} WHERE {' AND '.join(where_parts)}"
        return await self.execute(query, where)

    async def exists(self, table: str, where: dict[str, Any]) -> bool:
        """Check if row exists."""
        conditions = [f"{self._sql_name(k)} = {self._placeholder(k)}" for k in where.keys()]
        query = f"SELECT 1 FROM {table} WHERE {' AND '.join(conditions)} LIMIT 1"
        result = await self.fetch_one(query, where)
        return result is not None

    async def count(self, table: str, where: dict[str, Any] | None = None) -> int:
        """Count rows in table.

        Args:
            table: Table name.
            where: Optional WHERE conditions.

        Returns:
            Row count.
        """
        query = f"SELECT COUNT(*) as cnt FROM {table}"
        params: dict[str, Any] = {}

        if where:
            conditions = [f"{self._sql_name(k)} = {self._placeholder(k)}" for k in where.keys()]
            query += " WHERE " + " AND ".join(conditions)
            params.update(where)

        result = await self.fetch_one(query, params)
        return result["cnt"] if result else 0
