# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""SQLite async adapter using aiosqlite."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import aiosqlite

from .base import DbAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence


class SqliteAdapter(DbAdapter):
    """SQLite async adapter. Opens connection per-operation for thread safety."""

    placeholder = "?"

    def __init__(self, db_path: str):
        """Initialize SQLite adapter.

        Args:
            db_path: Path to SQLite file, or ":memory:" for in-memory DB.
        """
        self.db_path = db_path or ":memory:"

    async def connect(self) -> None:
        """SQLite connections are opened per-operation, this is a no-op."""
        pass

    async def close(self) -> None:
        """SQLite connections are closed per-operation, this is a no-op."""
        pass

    async def execute(self, query: str, params: Sequence[Any] | None = None) -> int:
        """Execute query, return affected row count."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params or ())
            await db.commit()
            return cursor.rowcount

    async def fetch_one(
        self, query: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any] | None:
        """Execute query, return single row as dict or None."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params or ()) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    return None
                cols = [c[0] for c in cursor.description]
                return dict(zip(cols, row, strict=True))

    async def fetch_all(
        self, query: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute query, return all rows as list of dicts."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(query, params or ()) as cursor:
                rows = await cursor.fetchall()
                cols = [c[0] for c in cursor.description]
                return [dict(zip(cols, row, strict=True)) for row in rows]

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
