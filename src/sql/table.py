# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Table base class with Columns-based schema (async version)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from .column import Columns

if TYPE_CHECKING:
    from .sqldb import SqlDb


class RecordUpdater:
    """Async context manager for record update with locking and triggers.

    Usage:
        async with table.record(pk) as record:
            record['field'] = 'value'
        # → triggers update() with old_record

        async with table.record(pk, insert_missing=True) as record:
            record['field'] = 'value'
        # → insert() if not exists, update() if exists

    The context manager:
    - __aenter__: SELECT FOR UPDATE (PostgreSQL) or SELECT (SQLite), saves old_record
    - __aexit__: calls insert() or update() with proper trigger chain
    """

    def __init__(
        self,
        table: Table,
        pkey: str,
        pkey_value: Any,
        insert_missing: bool = False,
        for_update: bool = True,
    ):
        self.table = table
        self.pkey = pkey
        self.pkey_value = pkey_value
        self.insert_missing = insert_missing
        self.for_update = for_update
        self.record: dict[str, Any] | None = None
        self.old_record: dict[str, Any] | None = None
        self.is_insert = False

    async def __aenter__(self) -> dict[str, Any]:
        where = {self.pkey: self.pkey_value}

        if self.for_update:
            self.old_record = await self.table.select_for_update(where)
        else:
            self.old_record = await self.table.select_one(where=where)

        if self.old_record is None:
            if self.insert_missing:
                self.record = {self.pkey: self.pkey_value}
                self.is_insert = True
            else:
                self.record = {}
        else:
            self.record = dict(self.old_record)

        return self.record

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            return

        if not self.record:
            return

        if self.is_insert:
            await self.table.insert(self.record)
        elif self.old_record:
            await self.table.update(
                self.record,
                {self.pkey: self.pkey_value},
            )


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

    async def sync_schema(self) -> None:
        """Sync table schema by adding any missing columns.

        Iterates over all columns defined in configure() and adds them
        if they don't exist in the database. This enables automatic
        schema migration when new columns are added to the codebase.

        Safe to call on every startup - existing columns are ignored.
        Works with both SQLite and PostgreSQL.
        """
        for col in self.columns.values():
            if col.primary_key:
                continue  # Skip primary key, it's created with the table
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
        """Insert a row. Calls trigger_on_inserting before and trigger_on_inserted after."""
        record = await self.trigger_on_inserting(data)
        encoded = self._encode_json_fields(record)
        result = await self.db.adapter.insert(self.name, encoded)
        await self.trigger_on_inserted(record)
        return result

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

    async def select_for_update(
        self,
        where: dict[str, Any],
        columns: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Select single row with FOR UPDATE lock (PostgreSQL) or regular select (SQLite).

        Args:
            where: WHERE conditions to identify the row.
            columns: Columns to select (None = all).

        Returns:
            Row dict or None if not found.
        """
        cols_sql = ", ".join(columns) if columns else "*"
        adapter = self.db.adapter

        conditions = [f"{k} = {adapter._placeholder(k)}" for k in where.keys()]
        where_sql = " AND ".join(conditions)

        # PostgreSQL supports FOR UPDATE, SQLite doesn't need it (implicit locking)
        is_postgres = hasattr(adapter, "_pool") and adapter._pool is not None  # type: ignore[attr-defined]
        lock_clause = " FOR UPDATE" if is_postgres else ""

        query = f"SELECT {cols_sql} FROM {self.name} WHERE {where_sql}{lock_clause}"
        row = await adapter.fetch_one(query, where)
        return self._decode_json_fields(row) if row else None

    def record(
        self,
        pkey_value: Any,
        pkey: str | None = None,
        insert_missing: bool = False,
        for_update: bool = True,
    ) -> RecordUpdater:
        """Return async context manager for record update.

        Args:
            pkey_value: Primary key value to look up.
            pkey: Primary key column name (auto-detected if None).
            insert_missing: If True, insert new record if not found.
            for_update: If True, use SELECT FOR UPDATE (PostgreSQL).

        Returns:
            RecordUpdater context manager.

        Usage:
            async with table.record('uuid-123') as rec:
                rec['name'] = 'New Name'
            # → update() called automatically with old_record for triggers
        """
        if pkey is None:
            for col in self.columns.values():
                if col.primary_key:
                    pkey = col.name
                    break
            if pkey is None:
                raise ValueError(f"Table {self.name} has no primary key defined")

        return RecordUpdater(self, pkey, pkey_value, insert_missing, for_update)

    async def update(self, values: dict[str, Any], where: dict[str, Any]) -> int:
        """Update rows. Calls trigger_on_updating before and trigger_on_updated after."""
        # Fetch old record for triggers (only if triggers might be overridden)
        old_record = await self.select_one(where=where)
        record = await self.trigger_on_updating(values, old_record or {})
        encoded = self._encode_json_fields(record)
        result = await self.db.adapter.update(self.name, encoded, where)
        if result > 0 and old_record:
            await self.trigger_on_updated(record, old_record)
        return result

    async def delete(self, where: dict[str, Any]) -> int:
        """Delete rows. Calls trigger_on_deleting before and trigger_on_deleted after."""
        # Fetch record for triggers before deletion
        record = await self.select_one(where=where)
        if record:
            await self.trigger_on_deleting(record)
        result = await self.db.adapter.delete(self.name, where)
        if result > 0 and record:
            await self.trigger_on_deleted(record)
        return result

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


__all__ = ["Table", "RecordUpdater"]
