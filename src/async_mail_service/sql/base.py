# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Base adapter class for async database backends.

Minimal SQL layer with adapter pattern, ready to be extracted to genro-sql.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence


class DbAdapter(ABC):
    """Abstract base class for async database adapters.

    Adapters handle dialect-specific differences:
    - Placeholder syntax (? for SQLite, %s for PostgreSQL)
    - UPSERT syntax differences
    - Connection management
    """

    placeholder: str = "?"  # Override in subclass

    @abstractmethod
    async def connect(self) -> None:
        """Establish database connection."""
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close database connection."""
        ...

    @abstractmethod
    async def execute(self, query: str, params: Sequence[Any] | None = None) -> int:
        """Execute query, return affected row count."""
        ...

    @abstractmethod
    async def fetch_one(
        self, query: str, params: Sequence[Any] | None = None
    ) -> dict[str, Any] | None:
        """Execute query, return single row as dict or None."""
        ...

    @abstractmethod
    async def fetch_all(
        self, query: str, params: Sequence[Any] | None = None
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
