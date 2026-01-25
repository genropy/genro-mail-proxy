# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Table base class with Columns-based schema (async version)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .column import Columns

if TYPE_CHECKING:
    from .sqldb import SqlDb


class Table:
    """Base class for async table managers.

    Subclasses define columns via configure() hook and implement
    domain-specific operations.

    Attributes:
        name: Table name in database.
        db: SqlDb instance reference.
        columns: Column definitions.
    """

    name: str

    def __init__(self, db: SqlDb) -> None:
        self.db = db
        if not hasattr(self, "name") or not self.name:
            raise ValueError(f"{type(self).__name__} must define 'name'")

        self.columns = Columns()
        self.configure()

    def configure(self) -> None:
        """Override to define columns. Called during __init__."""
        pass

    # -------------------------------------------------------------------------
    # Trigger Hooks
    # -------------------------------------------------------------------------

    async def trigger_on_inserting(self, record: dict[str, Any]) -> dict[str, Any]:
        """Called before insert. Can modify record. Return the record to insert."""
        return record

    async def trigger_on_inserted(self, record: dict[str, Any]) -> None:
        """Called after successful insert."""
        pass

    async def trigger_on_updating(
        self, record: dict[str, Any], old_record: dict[str, Any]
    ) -> dict[str, Any]:
        """Called before update. Can modify record. Return the record to update."""
        return record

    async def trigger_on_updated(
        self, record: dict[str, Any], old_record: dict[str, Any]
    ) -> None:
        """Called after successful update."""
        pass

    async def trigger_on_deleting(self, record: dict[str, Any]) -> None:
        """Called before delete."""
        pass

    async def trigger_on_deleted(self, record: dict[str, Any]) -> None:
        """Called after successful delete."""
        pass

    # -------------------------------------------------------------------------
    # Schema
    # -------------------------------------------------------------------------

    def create_table_sql(self) -> str:
        """Generate CREATE TABLE IF NOT EXISTS statement."""
        col_defs = []
        for col in self.columns.values():
            if col.primary_key and col.type_ == "INTEGER":
                # Use adapter's pk_column for autoincrement primary key
                col_defs.append(self.db.adapter.pk_column(col.name))
            else:
                col_defs.append(col.to_sql())

        # Add foreign key constraints
        for col in self.columns.values():
            if col.relation_sql and col.relation_table:
                col_defs.append(
                    f'FOREIGN KEY ("{col.name}") REFERENCES {col.relation_table}("{col.relation_pk}")'
                )

        return f"CREATE TABLE IF NOT EXISTS {self.name} (\n    " + ",\n    ".join(col_defs) + "\n)"

    async def create_schema(self) -> None:
        """Create table if not exists."""
        await self.db.adapter.execute(self.create_table_sql())

    async def add_column_if_missing(self, column_name: str) -> None:
        """Add column if it doesn't exist (migration helper)."""
        col = self.columns.get(column_name)
        if not col:
            raise ValueError(f"Column '{column_name}' not defined in {self.name}")

        try:
            await self.db.adapter.execute(
                f"ALTER TABLE {self.name} ADD COLUMN {col.to_sql()}"
            )
        except Exception:
            pass  # Column already exists

    # -------------------------------------------------------------------------
    # JSON Encoding/Decoding
    # -------------------------------------------------------------------------

    def _encode_json_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        """Encode JSON fields for storage."""
        result = dict(data)
        for col_name in self.columns.json_columns():
            if col_name in result and result[col_name] is not None:
                result[col_name] = json.dumps(result[col_name])
        return result

    def _decode_json_fields(self, row: dict[str, Any]) -> dict[str, Any]:
        """Decode JSON fields from storage."""
        result = dict(row)
        for col_name in self.columns.json_columns():
            if col_name in result and result[col_name] is not None:
                result[col_name] = json.loads(result[col_name])
        return result

    def _decode_rows(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Decode JSON fields in multiple rows."""
        return [self._decode_json_fields(row) for row in rows]

    # -------------------------------------------------------------------------
    # CRUD Operations
    # -------------------------------------------------------------------------

    async def insert(self, data: dict[str, Any]) -> int:
        """Insert a row."""
        encoded = self._encode_json_fields(data)
        return await self.db.adapter.insert(self.name, encoded)

    async def select(
        self,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Select rows."""
        rows = await self.db.adapter.select(
            self.name, columns, where, order_by, limit
        )
        return self._decode_rows(rows)

    async def select_one(
        self,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Select single row."""
        row = await self.db.adapter.select_one(self.name, columns, where)
        return self._decode_json_fields(row) if row else None

    async def update(self, values: dict[str, Any], where: dict[str, Any]) -> int:
        """Update rows."""
        encoded = self._encode_json_fields(values)
        return await self.db.adapter.update(self.name, encoded, where)

    async def delete(self, where: dict[str, Any]) -> int:
        """Delete rows."""
        return await self.db.adapter.delete(self.name, where)

    async def exists(self, where: dict[str, Any]) -> bool:
        """Check if row exists."""
        return await self.db.adapter.exists(self.name, where)

    async def count(self, where: dict[str, Any] | None = None) -> int:
        """Count rows."""
        return await self.db.adapter.count(self.name, where)

    async def upsert(
        self,
        data: dict[str, Any],
        conflict_columns: list[str],
        update_extras: list[str] | None = None,
    ) -> int:
        """Insert or update on conflict."""
        encoded = self._encode_json_fields(data)
        return await self.db.adapter.upsert(
            self.name, encoded, conflict_columns, update_extras
        )

    # -------------------------------------------------------------------------
    # Raw Query
    # -------------------------------------------------------------------------

    async def fetch_one(
        self, query: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        """Execute raw query, return single row."""
        row = await self.db.adapter.fetch_one(query, params)
        return self._decode_json_fields(row) if row else None

    async def fetch_all(
        self, query: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Execute raw query, return all rows."""
        rows = await self.db.adapter.fetch_all(query, params)
        return self._decode_rows(rows)

    async def execute(
        self, query: str, params: dict[str, Any] | None = None
    ) -> int:
        """Execute raw query, return affected row count."""
        return await self.db.adapter.execute(query, params)


__all__ = ["Table"]
