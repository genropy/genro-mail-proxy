# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for sql/table.py - Table base class and RecordUpdater."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from sql.column import Columns
from sql.table import RecordUpdater, Table

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


# =============================================================================
# Table subclass for testing
# =============================================================================


class TestTable(Table):
    """Test table with various column types."""

    name = "test_items"
    pkey = "pk"

    def configure(self) -> None:
        self.columns.column("pk", "TEXT")
        self.columns.column("name", "TEXT")
        self.columns.column("value", "INTEGER")
        self.columns.column("metadata", "TEXT", json_encoded=True)
        self.columns.column("secret", "TEXT", encrypted=True)


class AutoIncrementTable(Table):
    """Test table with autoincrement primary key."""

    name = "auto_items"
    pkey = "id"

    def configure(self) -> None:
        self.columns.column("id", "INTEGER")
        self.columns.column("name", "TEXT")

    def new_pkey_value(self) -> Any:
        """Autoincrement: return None to let DB generate pk."""
        return None


class NoPkTable(Table):
    """Table without primary key."""

    name = "no_pk_items"
    pkey = None

    def configure(self) -> None:
        self.columns.column("name", "TEXT")
        self.columns.column("value", "INTEGER")


class TableWithoutName(Table):
    """Table that doesn't define name - should fail."""

    pass


# =============================================================================
# Table initialization tests
# =============================================================================


class TestTableInit:
    """Tests for Table.__init__ and configure()."""

    async def test_table_without_name_raises(self, pg_db):
        """Table without name attribute raises ValueError."""
        with pytest.raises(ValueError, match="must define 'name'"):
            TableWithoutName(pg_db)

    async def test_configure_called_during_init(self, pg_db):
        """configure() is called during __init__."""
        table = TestTable(pg_db)
        # Columns should be populated
        assert "pk" in table.columns
        assert "name" in table.columns

    async def test_pkey_value_returns_pk(self, pg_db):
        """pkey_value extracts primary key from record."""
        table = TestTable(pg_db)
        record = {"pk": "test-123", "name": "Test"}
        assert table.pkey_value(record) == "test-123"

    async def test_pkey_value_none_when_no_pkey(self, pg_db):
        """pkey_value returns None for table without pkey."""
        # Create the table first
        await pg_db.adapter.execute(
            "CREATE TABLE IF NOT EXISTS no_pk_items (name TEXT, value INTEGER)"
        )
        table = NoPkTable(pg_db)
        record = {"name": "Test", "value": 42}
        assert table.pkey_value(record) is None


# =============================================================================
# Schema tests
# =============================================================================


class TestTableSchema:
    """Tests for create_table_sql and schema operations."""

    async def test_create_table_sql_basic(self, pg_db):
        """create_table_sql generates valid SQL."""
        table = TestTable(pg_db)
        sql = table.create_table_sql()

        assert "CREATE TABLE IF NOT EXISTS test_items" in sql
        assert '"pk"' in sql
        assert '"name"' in sql
        assert "PRIMARY KEY" in sql

    async def test_create_schema(self, pg_db):
        """create_schema creates the table."""
        table = TestTable(pg_db)
        await table.create_schema()

        # Should be able to insert
        await table.insert({"pk": "test-1", "name": "Test"})
        result = await table.select_one(where={"pk": "test-1"})
        assert result["name"] == "Test"

    async def test_add_column_if_missing(self, pg_db):
        """add_column_if_missing adds column that doesn't exist."""
        table = TestTable(pg_db)
        await table.create_schema()

        # Add a new column to the schema
        table.columns.column("new_col", "TEXT")

        # Should not raise
        await table.add_column_if_missing("new_col")

        # Should be able to use the column
        await table.insert({"pk": "test-2", "name": "Test", "new_col": "value"})

    async def test_add_column_if_missing_already_exists(self, pg_db):
        """add_column_if_missing doesn't fail if column exists."""
        table = TestTable(pg_db)
        await table.create_schema()

        # Should not raise even if column exists
        await table.add_column_if_missing("name")

    async def test_add_column_if_missing_undefined_raises(self, pg_db):
        """add_column_if_missing raises for undefined column."""
        table = TestTable(pg_db)
        await table.create_schema()

        with pytest.raises(ValueError, match="not defined"):
            await table.add_column_if_missing("undefined_col")

    async def test_sync_schema(self, pg_db):
        """sync_schema adds missing columns."""
        table = TestTable(pg_db)
        await table.create_schema()

        # Add new column to definition
        table.columns.column("synced_col", "TEXT")

        # Sync should add it
        await table.sync_schema()

        # Should be usable
        await table.insert({"pk": "test-3", "name": "Test", "synced_col": "synced"})
        result = await table.select_one(where={"pk": "test-3"})
        assert result["synced_col"] == "synced"


# =============================================================================
# CRUD operations tests
# =============================================================================


class TestTableCRUD:
    """Tests for basic CRUD operations."""

    async def test_insert_and_select(self, pg_db):
        """Insert and select a record."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "crud-1", "name": "CRUD Test", "value": 42})

        result = await table.select_one(where={"pk": "crud-1"})
        assert result["name"] == "CRUD Test"
        assert result["value"] == 42

    async def test_insert_generates_pk(self, pg_db):
        """Insert generates pk if not provided."""
        table = TestTable(pg_db)
        await table.create_schema()

        data = {"name": "Auto PK"}
        await table.insert(data)

        # pk should be populated in data
        assert "pk" in data
        assert data["pk"] is not None

    async def test_select_multiple(self, pg_db):
        """Select returns multiple rows."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "multi-1", "name": "First", "value": 1})
        await table.insert({"pk": "multi-2", "name": "Second", "value": 2})
        await table.insert({"pk": "multi-3", "name": "Third", "value": 3})

        results = await table.select(order_by="value")
        assert len(results) >= 3

    async def test_select_with_limit(self, pg_db):
        """Select respects limit."""
        table = TestTable(pg_db)
        await table.create_schema()

        for i in range(5):
            await table.insert({"pk": f"limit-{i}", "name": f"Item {i}"})

        results = await table.select(limit=2)
        assert len(results) == 2

    async def test_update(self, pg_db):
        """Update modifies record."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "update-1", "name": "Original", "value": 10})
        await table.update({"name": "Updated", "value": 20}, {"pk": "update-1"})

        result = await table.select_one(where={"pk": "update-1"})
        assert result["name"] == "Updated"
        assert result["value"] == 20

    async def test_delete(self, pg_db):
        """Delete removes record."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "delete-1", "name": "To Delete"})
        count = await table.delete({"pk": "delete-1"})

        assert count == 1
        result = await table.select_one(where={"pk": "delete-1"})
        assert result is None

    async def test_exists(self, pg_db):
        """exists() checks for record."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "exists-1", "name": "Exists"})

        assert await table.exists({"pk": "exists-1"}) is True
        assert await table.exists({"pk": "not-exists"}) is False

    async def test_count(self, pg_db):
        """count() returns row count."""
        table = TestTable(pg_db)
        await table.create_schema()

        initial = await table.count()
        await table.insert({"pk": "count-1", "name": "One"})
        await table.insert({"pk": "count-2", "name": "Two"})

        assert await table.count() == initial + 2
        assert await table.count({"name": "One"}) == 1


# =============================================================================
# JSON fields tests
# =============================================================================


class TestTableJSON:
    """Tests for JSON field encoding/decoding."""

    async def test_json_field_roundtrip(self, pg_db):
        """JSON fields are encoded on insert and decoded on select."""
        table = TestTable(pg_db)
        await table.create_schema()

        metadata = {"key": "value", "nested": {"a": 1, "b": [1, 2, 3]}}
        await table.insert({"pk": "json-1", "name": "JSON Test", "metadata": metadata})

        result = await table.select_one(where={"pk": "json-1"})
        assert result["metadata"] == metadata
        assert result["metadata"]["nested"]["b"] == [1, 2, 3]

    async def test_json_field_null(self, pg_db):
        """NULL JSON fields stay NULL."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "json-null", "name": "No JSON", "metadata": None})

        result = await table.select_one(where={"pk": "json-null"})
        assert result["metadata"] is None


# =============================================================================
# Encryption tests
# =============================================================================


class TestTableEncryption:
    """Tests for encrypted field handling."""

    async def test_encrypted_field_with_key(self, pg_db):
        """Encrypted fields are encrypted/decrypted with key."""
        # Set encryption key on proxy
        pg_db.parent.set_encryption_key(b"0" * 32)

        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "enc-1", "name": "Encrypted", "secret": "my-secret"})

        # Raw read should show encrypted value
        raw = await pg_db.adapter.select_one("test_items", where={"pk": "enc-1"})
        assert raw["secret"].startswith("ENC:")

        # Table read should decrypt
        result = await table.select_one(where={"pk": "enc-1"})
        assert result["secret"] == "my-secret"

    async def test_encrypted_field_without_key(self, pg_db):
        """Without encryption key, values stored as plaintext."""
        # Ensure no encryption key
        pg_db.parent._encryption_key = None

        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "noenc-1", "name": "No Encryption", "secret": "plain"})

        result = await table.select_one(where={"pk": "noenc-1"})
        assert result["secret"] == "plain"

    async def test_encrypted_field_decryption_failure(self, pg_db):
        """Decryption failure returns encrypted value."""
        # Set key, insert, then change key
        pg_db.parent.set_encryption_key(b"1" * 32)

        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "enc-fail", "name": "Will Fail", "secret": "secret"})

        # Change key
        pg_db.parent.set_encryption_key(b"2" * 32)

        # Should return encrypted value (not raise)
        result = await table.select_one(where={"pk": "enc-fail"})
        assert result["secret"].startswith("ENC:")


# =============================================================================
# RecordUpdater tests
# =============================================================================


class TestRecordUpdater:
    """Tests for RecordUpdater context manager."""

    async def test_record_update_existing(self, pg_db):
        """Update existing record via context manager."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "rec-1", "name": "Original", "value": 10})

        async with table.record("rec-1") as rec:
            rec["name"] = "Updated"
            rec["value"] = 20

        result = await table.select_one(where={"pk": "rec-1"})
        assert result["name"] == "Updated"
        assert result["value"] == 20

    async def test_record_insert_missing(self, pg_db):
        """Insert new record via context manager with insert_missing."""
        table = TestTable(pg_db)
        await table.create_schema()

        async with table.record("new-rec", insert_missing=True) as rec:
            rec["name"] = "New Record"
            rec["value"] = 100

        result = await table.select_one(where={"pk": "new-rec"})
        assert result is not None
        assert result["name"] == "New Record"

    async def test_record_not_found_empty_dict(self, pg_db):
        """Non-existent record returns empty dict without insert_missing."""
        table = TestTable(pg_db)
        await table.create_schema()

        async with table.record("not-found") as rec:
            # rec is empty dict
            assert rec == {}

    async def test_record_composite_key(self, pg_db):
        """Update with composite key (dict)."""
        # Use accounts table which has composite key
        accounts = pg_db.table("accounts")

        await pg_db.table("tenants").add({"id": "t1", "name": "Test"})
        await accounts.add({
            "id": "acc1",
            "tenant_id": "t1",
            "host": "smtp.test.com",
            "port": 587,
        })

        async with accounts.record({"tenant_id": "t1", "id": "acc1"}) as rec:
            rec["host"] = "smtp.updated.com"

        result = await accounts.get("t1", "acc1")
        assert result["host"] == "smtp.updated.com"

    async def test_record_without_for_update(self, pg_db):
        """Record without FOR UPDATE uses regular select."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "no-lock", "name": "No Lock", "value": 5})

        async with table.record("no-lock", for_update=False) as rec:
            rec["name"] = "Updated No Lock"

        result = await table.select_one(where={"pk": "no-lock"})
        assert result["name"] == "Updated No Lock"

    async def test_record_no_pkey_raises(self, pg_db):
        """record() with scalar pk on table without pkey raises."""
        await pg_db.adapter.execute(
            "CREATE TABLE IF NOT EXISTS no_pk_items (name TEXT, value INTEGER)"
        )
        table = NoPkTable(pg_db)

        with pytest.raises(ValueError, match="has no primary key"):
            table.record("some-value")

    async def test_record_exception_no_save(self, pg_db):
        """Exception in context manager doesn't save changes."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "exc-1", "name": "Original"})

        with pytest.raises(ValueError):
            async with table.record("exc-1") as rec:
                rec["name"] = "Should Not Save"
                raise ValueError("Test error")

        result = await table.select_one(where={"pk": "exc-1"})
        assert result["name"] == "Original"


# =============================================================================
# Batch operations tests
# =============================================================================


class TestTableBatchOps:
    """Tests for batch update operations."""

    async def test_update_batch(self, pg_db):
        """update_batch updates multiple records with triggers."""
        table = TestTable(pg_db)
        await table.create_schema()

        # Track trigger calls
        trigger_calls = []

        async def track_updating(record, old_record):
            trigger_calls.append(("updating", record["pk"]))
            return record

        async def track_updated(record, old_record):
            trigger_calls.append(("updated", record["pk"]))

        table.trigger_on_updating = track_updating
        table.trigger_on_updated = track_updated

        # Insert records
        await table.insert({"pk": "batch-1", "name": "One", "value": 1})
        await table.insert({"pk": "batch-2", "name": "Two", "value": 2})
        await table.insert({"pk": "batch-3", "name": "Three", "value": 3})

        # Batch update
        count = await table.update_batch(
            ["batch-1", "batch-2"],
            {"value": 100}
        )

        assert count == 2
        # Triggers called for each
        assert len([c for c in trigger_calls if c[0] == "updating"]) == 2
        assert len([c for c in trigger_calls if c[0] == "updated"]) == 2

    async def test_update_batch_empty(self, pg_db):
        """update_batch with empty list returns 0."""
        table = TestTable(pg_db)
        await table.create_schema()

        count = await table.update_batch([], {"value": 100})
        assert count == 0

    async def test_update_batch_no_pkey_raises(self, pg_db):
        """update_batch on table without pkey raises."""
        await pg_db.adapter.execute(
            "CREATE TABLE IF NOT EXISTS no_pk_items (name TEXT, value INTEGER)"
        )
        table = NoPkTable(pg_db)

        with pytest.raises(ValueError, match="has no primary key"):
            await table.update_batch(["a", "b"], {"value": 1})

    async def test_update_batch_raw(self, pg_db):
        """update_batch_raw updates with single query, no triggers."""
        table = TestTable(pg_db)
        await table.create_schema()

        trigger_called = []
        table.trigger_on_updated = lambda r, o: trigger_called.append(r)

        await table.insert({"pk": "raw-1", "name": "One", "value": 1})
        await table.insert({"pk": "raw-2", "name": "Two", "value": 2})

        count = await table.update_batch_raw(
            ["raw-1", "raw-2"],
            {"value": 999}
        )

        assert count == 2
        # No triggers called
        assert len(trigger_called) == 0

        # Values updated
        r1 = await table.select_one(where={"pk": "raw-1"})
        r2 = await table.select_one(where={"pk": "raw-2"})
        assert r1["value"] == 999
        assert r2["value"] == 999

    async def test_update_batch_raw_empty(self, pg_db):
        """update_batch_raw with empty list returns 0."""
        table = TestTable(pg_db)
        await table.create_schema()

        count = await table.update_batch_raw([], {"value": 100})
        assert count == 0

    async def test_update_batch_raw_no_updater(self, pg_db):
        """update_batch_raw with empty updater returns 0."""
        table = TestTable(pg_db)
        await table.create_schema()

        count = await table.update_batch_raw(["a", "b"], {})
        assert count == 0

    async def test_update_batch_raw_no_pkey_raises(self, pg_db):
        """update_batch_raw on table without pkey raises."""
        await pg_db.adapter.execute(
            "CREATE TABLE IF NOT EXISTS no_pk_items (name TEXT, value INTEGER)"
        )
        table = NoPkTable(pg_db)

        with pytest.raises(ValueError, match="has no primary key"):
            await table.update_batch_raw(["a"], {"value": 1})


# =============================================================================
# Trigger tests
# =============================================================================


class TestTableTriggers:
    """Tests for trigger hooks."""

    async def test_trigger_on_inserting(self, pg_db):
        """trigger_on_inserting can modify record before insert."""
        table = TestTable(pg_db)
        await table.create_schema()

        async def add_default(record):
            record["value"] = 42
            return record

        table.trigger_on_inserting = add_default

        await table.insert({"pk": "trig-ins", "name": "Trigger Test"})

        result = await table.select_one(where={"pk": "trig-ins"})
        assert result["value"] == 42

    async def test_trigger_on_inserted(self, pg_db):
        """trigger_on_inserted called after insert."""
        table = TestTable(pg_db)
        await table.create_schema()

        inserted_records = []

        async def track_inserted(record):
            inserted_records.append(record)

        table.trigger_on_inserted = track_inserted

        await table.insert({"pk": "trig-after", "name": "After Insert"})

        assert len(inserted_records) == 1
        assert inserted_records[0]["pk"] == "trig-after"

    async def test_trigger_on_updating(self, pg_db):
        """trigger_on_updating can modify record before update."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "trig-upd", "name": "Original", "value": 10})

        async def double_value(record, old_record):
            record["value"] = old_record["value"] * 2
            return record

        table.trigger_on_updating = double_value

        await table.update({"name": "Updated"}, {"pk": "trig-upd"})

        result = await table.select_one(where={"pk": "trig-upd"})
        assert result["value"] == 20  # 10 * 2

    async def test_trigger_on_deleting(self, pg_db):
        """trigger_on_deleting called before delete."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "trig-del", "name": "To Delete"})

        deleting_records = []

        async def track_deleting(record):
            deleting_records.append(record)

        table.trigger_on_deleting = track_deleting

        await table.delete({"pk": "trig-del"})

        assert len(deleting_records) == 1
        assert deleting_records[0]["pk"] == "trig-del"


# =============================================================================
# Raw query tests
# =============================================================================


class TestTableRawQuery:
    """Tests for raw query methods."""

    async def test_fetch_one(self, pg_db):
        """fetch_one executes raw query and returns single row."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "raw-q1", "name": "Raw Query"})

        result = await table.fetch_one(
            "SELECT * FROM test_items WHERE pk = :pk",
            {"pk": "raw-q1"}
        )
        assert result["name"] == "Raw Query"

    async def test_fetch_all(self, pg_db):
        """fetch_all executes raw query and returns all rows."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "raw-all-1", "name": "First"})
        await table.insert({"pk": "raw-all-2", "name": "Second"})

        results = await table.fetch_all(
            "SELECT * FROM test_items WHERE pk LIKE :pattern",
            {"pattern": "raw-all-%"}
        )
        assert len(results) >= 2

    async def test_execute(self, pg_db):
        """execute runs raw query and returns affected count."""
        table = TestTable(pg_db)
        await table.create_schema()

        await table.insert({"pk": "exec-1", "name": "Execute", "value": 1})
        await table.insert({"pk": "exec-2", "name": "Execute", "value": 2})

        count = await table.execute(
            "UPDATE test_items SET value = 999 WHERE name = :name",
            {"name": "Execute"}
        )
        assert count == 2


# =============================================================================
# Autoincrement pk tests
# =============================================================================


class TestAutoIncrementTable:
    """Tests for tables with autoincrement primary key."""

    async def test_autoincrement_insert(self, pg_db):
        """Insert generates autoincrement pk."""
        # Create the table manually since it's not in proxy
        await pg_db.adapter.execute("""
            CREATE TABLE IF NOT EXISTS auto_items (
                id SERIAL PRIMARY KEY,
                name TEXT
            )
        """)

        table = AutoIncrementTable(pg_db)

        data = {"name": "Auto 1"}
        await table.insert(data)

        # pk should be populated
        assert "id" in data
        assert data["id"] is not None
        assert isinstance(data["id"], int)

        # Insert another
        data2 = {"name": "Auto 2"}
        await table.insert(data2)

        assert data2["id"] > data["id"]
