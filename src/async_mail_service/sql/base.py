# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Base adapter class for async database backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


class DbAdapter(ABC):
    """Abstract base class for async database adapters.

    All queries use :name placeholders (supported by both SQLite and PostgreSQL).
    """

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
    async def execute_many(
        self, query: str, params_list: Sequence[dict[str, Any]]
    ) -> int:
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
    async def upsert(
        self,
        table: str,
        data: dict[str, Any],
        conflict_columns: Sequence[str],
        update_extras: Sequence[str] | None = None,
    ) -> int:
        """Insert or update row on conflict.

        Args:
            table: Table name.
            data: Column-value pairs to insert/update.
            conflict_columns: Columns that define uniqueness (typically PK).
            update_extras: Extra SQL expressions for UPDATE (e.g., "updated_at = CURRENT_TIMESTAMP").

        Returns:
            Affected row count.
        """
        ...

    @abstractmethod
    async def commit(self) -> None:
        """Commit current transaction."""
        ...

    @abstractmethod
    async def rollback(self) -> None:
        """Rollback current transaction."""
        ...
