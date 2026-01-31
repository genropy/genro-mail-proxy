# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""PostgreSQL async adapter using psycopg3 with connection pooling."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import DbAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence


class PostgresAdapter(DbAdapter):
    """PostgreSQL async adapter using psycopg3 with connection pooling.

    Converts :name placeholders to %(name)s for psycopg compatibility.
    """

    placeholder = "%(name)s"

    def pk_column(self, name: str) -> str:
        """Return SQL definition for autoincrement primary key column (PostgreSQL)."""
        return f'"{name}" SERIAL PRIMARY KEY'

    def __init__(self, dsn: str, pool_size: int = 10):
        self.dsn = dsn
        self.pool_size = pool_size
        self._pool: Any = None

        # Verify psycopg is available at init time
        try:
            import psycopg  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "PostgreSQL support requires psycopg. "
                "Install with: pip install genro-mail-proxy[postgresql]"
            ) from e

    def _convert_placeholders(self, query: str) -> str:
        """Convert :name placeholders to %(name)s for psycopg.

        Uses negative lookbehind to preserve PostgreSQL :: cast operators.
        """
        return re.sub(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)", r"%(\1)s", query)

    async def connect(self) -> None:
        """Establish connection pool.

        Sets search_path to 'public' schema to ensure tables are created
        in a valid schema even when the connection default is unset.
        """
        from psycopg_pool import AsyncConnectionPool

        # Configure connection to use 'public' schema by default
        async def configure(conn):
            await conn.execute("SET search_path TO public")
            await conn.commit()

        self._pool = AsyncConnectionPool(
            self.dsn,
            min_size=1,
            max_size=self.pool_size,
            open=False,
            configure=configure,
        )
        await self._pool.open()

    async def close(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def execute(self, query: str, params: dict[str, Any] | None = None) -> int:
        """Execute query, return affected row count."""
        query = self._convert_placeholders(query)
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.execute(query, params or {})
            await conn.commit()
            return cur.rowcount

    async def insert_returning_id(
        self, table: str, values: dict[str, Any], pk_col: str = "id"
    ) -> Any:
        """Insert a row and return the generated primary key (RETURNING).

        Args:
            table: Table name.
            values: Column-value pairs.
            pk_col: Primary key column name for RETURNING clause.

        Returns:
            The generated primary key value.
        """
        from psycopg.rows import dict_row

        cols = list(values.keys())
        placeholders = ", ".join(self._placeholder(c) for c in cols)
        col_list = ", ".join(self._sql_name(c) for c in cols)
        query = f'INSERT INTO {table} ({col_list}) VALUES ({placeholders}) RETURNING "{pk_col}"'
        query = self._convert_placeholders(query)
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, values)
                await conn.commit()
                row = await cur.fetchone()
                return row[pk_col] if row else None

    async def execute_many(self, query: str, params_list: Sequence[dict[str, Any]]) -> int:
        """Execute query multiple times with different params (batch insert)."""
        query = self._convert_placeholders(query)
        async with self._pool.connection() as conn, conn.cursor() as cur:
            await cur.executemany(query, params_list)
            await conn.commit()
            return len(params_list)

    async def fetch_one(
        self, query: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Execute query, return single row as dict or None."""
        from psycopg.rows import dict_row

        query = self._convert_placeholders(query)
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params or {})
                return await cur.fetchone()

    async def fetch_all(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute query, return all rows as list of dicts."""
        from psycopg.rows import dict_row

        query = self._convert_placeholders(query)
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params or {})
                return await cur.fetchall()

    async def execute_script(self, script: str) -> None:
        """Execute multiple statements (for schema creation)."""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(script)
            await conn.commit()

    def _sql_name(self, name: str) -> str:
        """Quote identifier for PostgreSQL (handles reserved words like 'user')."""
        return f'"{name}"'

    def for_update_clause(self) -> str:
        """Return FOR UPDATE clause for row locking."""
        return " FOR UPDATE"

    async def commit(self) -> None:
        """Commit is handled per-operation with connection pooling."""
        pass

    async def rollback(self) -> None:
        """Rollback is handled per-operation with connection pooling."""
        pass
