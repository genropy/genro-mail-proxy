# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""PostgreSQL async adapter using psycopg3 with connection pooling."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import DbAdapter

if TYPE_CHECKING:
    from collections.abc import Sequence


class PostgresAdapter(DbAdapter):
    """PostgreSQL async adapter. Queries with ? are converted to %s automatically."""

    placeholder = "%s"

    def __init__(self, dsn: str, pool_size: int = 10):
        """Initialize PostgreSQL adapter.

        Args:
            dsn: PostgreSQL connection string (e.g., "postgresql://user:pass@host/db").
            pool_size: Maximum connections in pool.

        Raises:
            ImportError: If psycopg is not installed.
        """
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
        """Convert ? placeholders to %s for psycopg."""
        return query.replace("?", "%s")

    async def connect(self) -> None:
        """Establish connection pool."""
        from psycopg_pool import AsyncConnectionPool

        self._pool = AsyncConnectionPool(
            self.dsn,
            min_size=1,
            max_size=self.pool_size,
            open=False,
        )
        await self._pool.open()

    async def close(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def execute(self, query: str, params: Sequence[Any] | None = None) -> int:
        """Execute query, return affected row count."""
        query = self._convert_placeholders(query)
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(query, params or ())
                await conn.commit()
                return cur.rowcount

    async def fetch_one(
        self, query: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any] | None:
        """Execute query, return single row as dict or None."""
        from psycopg.rows import dict_row

        query = self._convert_placeholders(query)
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params or ())
                return await cur.fetchone()

    async def fetch_all(
        self, query: str, params: Sequence[Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute query, return all rows as list of dicts."""
        from psycopg.rows import dict_row

        query = self._convert_placeholders(query)
        async with self._pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                await cur.execute(query, params or ())
                return await cur.fetchall()

    async def execute_script(self, script: str) -> None:
        """Execute multiple statements (for schema creation)."""
        # PostgreSQL can execute multiple statements in one call
        script = self._convert_placeholders(script)
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(script)
            await conn.commit()

    async def commit(self) -> None:
        """Commit is handled per-operation with connection pooling."""
        pass

    async def rollback(self) -> None:
        """Rollback is handled per-operation with connection pooling."""
        pass
