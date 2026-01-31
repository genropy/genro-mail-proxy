# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Table base class with Columns-based schema (async version)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from genro_toolbox import get_uuid

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
    Supports both single-column keys and composite keys (dict).

    Single key: record("uuid-123") or record("uuid-123", pkey="pk")
    Composite:  record({"tenant_id": "t1", "id": "acc1"})
    """

    def __init__(
        self,
        table: Table,
        pkey: str | None,
        pkey_value: Any,
        insert_missing: bool = False,
        for_update: bool = True,
    ):
        self.table = table
        self.insert_missing = insert_missing
        self.for_update = for_update
        self.record: dict[str, Any] | None = None
        self.old_record: dict[str, Any] | None = None
        self.is_insert = False

        # Support composite keys: record({"tenant_id": "t1", "id": "acc1"})
        if isinstance(pkey_value, dict):
            self.where: dict[str, Any] = pkey_value
        else:
            self.where = {pkey: pkey_value}  # type: ignore[dict-item]

    async def __aenter__(self) -> dict[str, Any]:
        if self.for_update:
            self.old_record = await self.table.select_for_update(self.where)
        else:
            self.old_record = await self.table.select_one(where=self.where)

        if self.old_record is None:
            if self.insert_missing:
                self.record = dict(self.where)  # Initialize with key columns
                self.is_insert = True
            else:
                self.record = {}
        else:
            self.record = dict(self.old_record)

        return self.record  # type: ignore[return-value]

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if exc_type is not None:
            return

        if not self.record:
            return

        if self.is_insert:
            await self.table.insert(self.record)
        elif self.old_record:
            await self.table.update(self.record, self.where)


class Table:
    """Base class for async table managers.

    Subclasses define columns via configure() hook and implement
    domain-specific operations.

    Attributes:
        name: Table name in database.
        pkey: Primary key column name (e.g., "pk" or "id").
        db: SqlDb instance reference.
        columns: Column definitions.
    """

    name: str
    pkey: str | None = None

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
    # Primary Key
    # -------------------------------------------------------------------------

    def pkey_value(self, record: dict[str, Any]) -> Any:
        """Get primary key value from a record."""
        return record.get(self.pkey) if self.pkey else None

    def new_pkey_value(self) -> Any:
        """Generate a new primary key value. Override in subclasses for custom pk.

        Default: returns UUID. Tables with autoincrement pk should return None.
        """
        return get_uuid()

    # -------------------------------------------------------------------------
    # Trigger Hooks
    # -------------------------------------------------------------------------

    async def trigger_on_inserting(self, record: dict[str, Any]) -> dict[str, Any]:
        """Called before insert. Can modify record. Return the record to insert.

        Auto-generates pk via new_pkey_value() if pk column is not in record.
        """
        if self.pkey and self.pkey not in record:
            pk_value = self.new_pkey_value()
            if pk_value is not None:
                record[self.pkey] = pk_value
        return record

    async def trigger_on_inserted(self, record: dict[str, Any]) -> None:
        """Called after successful insert."""
        pass

    async def trigger_on_updating(
        self, record: dict[str, Any], old_record: dict[str, Any]
    ) -> dict[str, Any]:
        """Called before update. Can modify record. Return the record to update."""
        return record

    async def trigger_on_updated(self, record: dict[str, Any], old_record: dict[str, Any]) -> None:
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
        # Check if pk is autoincrement (new_pkey_value returns None)
        is_autoincrement = self.pkey and self.new_pkey_value() is None

        col_defs = []
        for col in self.columns.values():
            if col.name == self.pkey and is_autoincrement and col.type_ == "INTEGER":
                # Use adapter's pk_column for autoincrement primary key
                col_defs.append(self.db.adapter.pk_column(col.name))
            elif col.name == self.pkey:
                # UUID or other non-autoincrement primary key
                col_defs.append(col.to_sql(primary_key=True))
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
            await self.db.adapter.execute(f"ALTER TABLE {self.name} ADD COLUMN {col.to_sql()}")
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
            if col.name == self.pkey:
                continue  # Skip primary key, it's created with the table
            try:
                await self.db.adapter.execute(f"ALTER TABLE {self.name} ADD COLUMN {col.to_sql()}")
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
    # Encryption
    # -------------------------------------------------------------------------

    def _encrypt_fields(self, data: dict[str, Any]) -> dict[str, Any]:
        """Encrypt fields marked with encrypted=True before storage.

        Uses AES-256-GCM encryption. If no encryption key is configured,
        fields are stored as plaintext.
        """
        encrypted_cols = self.columns.encrypted_columns()
        if not encrypted_cols:
            return data

        key = self.db.encryption_key
        if key is None:
            return data

        from tools.encryption import encrypt_value_with_key

        result = dict(data)
        for col_name in encrypted_cols:
            if col_name in result and result[col_name] is not None:
                value = result[col_name]
                if isinstance(value, str) and not value.startswith("ENC:"):
                    result[col_name] = encrypt_value_with_key(value, key)
        return result

    def _decrypt_fields(self, row: dict[str, Any]) -> dict[str, Any]:
        """Decrypt fields marked with encrypted=True after reading.

        If decryption fails (wrong key, corrupted data), returns the
        encrypted value as-is.
        """
        encrypted_cols = self.columns.encrypted_columns()
        if not encrypted_cols:
            return row

        key = self.db.encryption_key
        if key is None:
            return row

        from tools.encryption import decrypt_value_with_key

        result = dict(row)
        for col_name in encrypted_cols:
            if col_name in result and result[col_name] is not None:
                value = result[col_name]
                if isinstance(value, str) and value.startswith("ENC:"):
                    try:
                        result[col_name] = decrypt_value_with_key(value, key)
                    except Exception:
                        pass  # Keep encrypted value if decryption fails
        return result

    # -------------------------------------------------------------------------
    # CRUD Operations
    # -------------------------------------------------------------------------

    async def insert(self, data: dict[str, Any]) -> int:
        """Insert a row. Calls trigger_on_inserting before and trigger_on_inserted after.

        The data dict is mutated: if the pk is auto-generated (UUID or autoincrement),
        it will be populated in data after insert.
        """
        record = await self.trigger_on_inserting(data)
        encoded = self._encrypt_fields(self._encode_json_fields(record))

        # Check if pk is autoincrement (new_pkey_value returns None)
        if self.pkey and self.pkey not in record:
            # Autoincrement: use insert_returning_id to get the generated id
            generated_id = await self.db.adapter.insert_returning_id(self.name, encoded, self.pkey)
            if generated_id is not None:
                data[self.pkey] = generated_id
                record[self.pkey] = generated_id
        else:
            # UUID pk already in record from trigger_on_inserting, or no pk
            await self.db.adapter.insert(self.name, encoded)
            # Ensure data has the pk (trigger may have added it to record)
            if self.pkey and self.pkey in record and self.pkey not in data:
                data[self.pkey] = record[self.pkey]

        await self.trigger_on_inserted(record)
        return 1

    async def select(
        self,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Select rows."""
        rows = await self.db.adapter.select(self.name, columns, where, order_by, limit)
        return [self._decrypt_fields(self._decode_json_fields(row)) for row in rows]

    async def select_one(
        self,
        columns: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Select single row."""
        row = await self.db.adapter.select_one(self.name, columns, where)
        return self._decrypt_fields(self._decode_json_fields(row)) if row else None

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

        conditions = [f"{k} = {adapter._placeholder(k)}" for k in where]
        where_sql = " AND ".join(conditions)
        lock_clause = adapter.for_update_clause()

        query = f"SELECT {cols_sql} FROM {self.name} WHERE {where_sql}{lock_clause}"
        row = await adapter.fetch_one(query, where)
        return self._decrypt_fields(self._decode_json_fields(row)) if row else None

    def record(
        self,
        pkey_value: Any,
        insert_missing: bool = False,
        for_update: bool = True,
    ) -> RecordUpdater:
        """Return async context manager for record update.

        Args:
            pkey_value: Primary key value, or dict for composite keys.
            insert_missing: If True, insert new record if not found (upsert).
            for_update: If True, use SELECT FOR UPDATE (PostgreSQL).

        Returns:
            RecordUpdater context manager.

        Usage:
            # Single key:
            async with table.record('uuid-123') as rec:
                rec['name'] = 'New Name'

            # Composite key (dict):
            async with table.record({'tenant_id': 't1', 'id': 'acc1'}) as rec:
                rec['host'] = 'smtp.example.com'

            # Upsert (insert if missing):
            async with table.record({'tenant_id': 't1', 'id': 'new'}, insert_missing=True) as rec:
                rec['host'] = 'smtp.new.com'
        """
        # For composite keys (dict), pkey is not needed
        if isinstance(pkey_value, dict):
            return RecordUpdater(self, None, pkey_value, insert_missing, for_update)

        # For single key, use self.pkey
        if self.pkey is None:
            raise ValueError(f"Table {self.name} has no primary key defined")

        return RecordUpdater(self, self.pkey, pkey_value, insert_missing, for_update)

    async def update(self, values: dict[str, Any], where: dict[str, Any]) -> int:
        """Update rows. Calls trigger_on_updating before and trigger_on_updated after.

        Uses SELECT FOR UPDATE to lock the row during update (PostgreSQL).
        """
        old_record = await self.select_for_update(where)
        record = await self.trigger_on_updating(values, old_record or {})
        encoded = self._encrypt_fields(self._encode_json_fields(record))
        result = await self.db.adapter.update(self.name, encoded, where)
        if result > 0 and old_record:
            await self.trigger_on_updated(record, old_record)
        return result

    async def update_batch(
        self,
        pkeys: list[Any],
        updater: dict[str, Any] | None = None,
    ) -> int:
        """Update multiple records by primary key, calling triggers for each.

        Performs ONE read (SELECT all records) then N writes (UPDATE per record)
        with trigger_on_updating/trigger_on_updated called for each.

        Args:
            pkeys: List of primary key values to update.
            updater: Dict of field:value to set on each record.

        Returns:
            Number of records updated.
        """
        if not pkeys:
            return 0

        pkey = self.pkey
        if pkey is None:
            raise ValueError(f"Table {self.name} has no primary key defined")

        # Single SELECT to fetch all records
        adapter = self.db.adapter
        params: dict[str, Any] = {}
        params.update({f"pk_{i}": pk for i, pk in enumerate(pkeys)})
        placeholders = ", ".join(f"{adapter._placeholder(f'pk_{i}')}" for i in range(len(pkeys)))
        query = f"SELECT * FROM {self.name} WHERE {pkey} IN ({placeholders})"
        rows = await adapter.fetch_all(query, params)

        # Index by pk for fast lookup
        records_by_pk = {row[pkey]: dict(row) for row in rows}

        # N writes with triggers
        updated = 0
        for pk_value in pkeys:
            old_record = records_by_pk.get(pk_value)
            if not old_record:
                continue

            new_record = dict(old_record)
            if updater:
                new_record.update(updater)

            # Call triggers and update
            new_record = await self.trigger_on_updating(new_record, old_record)
            encoded = self._encode_json_fields(new_record)
            result = await adapter.update(self.name, encoded, {pkey: pk_value})
            if result > 0:
                await self.trigger_on_updated(new_record, old_record)
                updated += 1

        return updated

    async def update_batch_raw(
        self,
        pkeys: list[Any],
        updater: dict[str, Any],
    ) -> int:
        """Update multiple records with a single UPDATE statement. No triggers.

        Use when you know there are no triggers to call and want maximum efficiency.
        Performs a single UPDATE ... WHERE pk IN (...) query.

        Args:
            pkeys: List of primary key values to update.
            updater: Dict of field:value to set on all records.

        Returns:
            Number of records updated.
        """
        if not pkeys or not updater:
            return 0

        pkey = self.pkey
        if pkey is None:
            raise ValueError(f"Table {self.name} has no primary key defined")

        adapter = self.db.adapter

        # Build SET clause
        set_parts = [f"{k} = {adapter._placeholder(k)}" for k in updater]
        set_clause = ", ".join(set_parts)

        # Build IN clause
        params: dict[str, Any] = dict(updater)
        params.update({f"pk_{i}": pk for i, pk in enumerate(pkeys)})
        placeholders = ", ".join(f"{adapter._placeholder(f'pk_{i}')}" for i in range(len(pkeys)))

        query = f"UPDATE {self.name} SET {set_clause} WHERE {pkey} IN ({placeholders})"
        return await adapter.execute(query, params)

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

    async def execute(self, query: str, params: dict[str, Any] | None = None) -> int:
        """Execute raw query, return affected row count."""
        return await self.db.adapter.execute(query, params)


__all__ = ["Table", "RecordUpdater"]
