# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SQLite async adapter using aiosqlite."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiosqlite

from .base import DbAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence


class SqliteAdapter(DbAdapter):
    """SQLite async adapter. Uses :name placeholders natively."""

    placeholder = ":name"

    # Column name patterns that should be converted from 0/1 to False/True
    _BOOL_PREFIXES = ("is_", "use_", "has_")
    _BOOL_NAMES = frozenset({"active", "enabled", "ssl", "tls"})

    def __init__(self, db_path: str):
        self.db_path = db_path or ":memory:"

    def _normalize_booleans(self, row: dict[str, Any]) -> dict[str, Any]:
        """Convert SQLite 0/1 to Python False/True for boolean-like columns."""
        for key, value in row.items():
            if value in (0, 1):
                if key.startswith(self._BOOL_PREFIXES) or key in self._BOOL_NAMES:
                    row[key] = bool(value)
        return row

    async def connect(self) -> None:
        """SQLite connections are opened per-operation, this is a no-op."""
        pass

    async def close(self) -> None:
        """SQLite connections are closed per-operation, this is a no-op."""
        pass

    async def execute(self, query: str, params: dict[str, Any] | None = None) -> int:
        """Execute query, return affected row count."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params or {})
            await db.commit()
            return cursor.rowcount

    async def insert_returning_id(
        self, table: str, values: dict[str, Any], pk_col: str = "id"
    ) -> Any:
        """Insert a row and return the generated primary key (autoincrement).

        Args:
            table: Table name.
            values: Column-value pairs.
            pk_col: Primary key column name (used for RETURNING in PostgreSQL).

        Returns:
            The generated primary key value (lastrowid for SQLite).
        """
        cols = list(values.keys())
        placeholders = ", ".join(self._placeholder(c) for c in cols)
        col_list = ", ".join(self._sql_name(c) for c in cols)
        query = f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})"
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, values)
            await db.commit()
            return cursor.lastrowid

    async def execute_many(self, query: str, params_list: Sequence[dict[str, Any]]) -> int:
        """Execute query multiple times with different params (batch insert)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(query, params_list)
            await db.commit()
            return len(params_list)

    async def fetch_one(
        self, query: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Execute query, return single row as dict or None."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params or {}) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                cols = [c[0] for c in cursor.description]
                return self._normalize_booleans(dict(zip(cols, row, strict=True)))

    async def fetch_all(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute query, return all rows as list of dicts."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params or {}) as cursor:
                rows = await cursor.fetchall()
                cols = [c[0] for c in cursor.description]
                return [self._normalize_booleans(dict(zip(cols, row, strict=True))) for row in rows]

    async def execute_script(self, script: str) -> None:
        """Execute multiple statements (for schema creation)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(script)
            await db.commit()

    async def commit(self) -> None:
        """Commit is handled per-operation in this implementation."""
        pass

    async def rollback(self) -> None:
        """Rollback is handled per-operation in this implementation."""
        pass
