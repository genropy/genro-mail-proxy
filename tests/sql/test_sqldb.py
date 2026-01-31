# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for sql.sqldb module - SqlDb database manager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from sql import SqlDb
from sql.table import Table


class DummyTable(Table):
    """Minimal table for testing."""

    name = "dummy"
    columns = []


class TableWithoutName(Table):
    """Table without name attribute for error testing."""

    columns = []


class TestSqlDbInit:
    """Tests for SqlDb initialization."""

    def test_init_creates_adapter(self):
        """SqlDb creates adapter from connection string."""
        db = SqlDb(":memory:")
        assert db.adapter is not None
        assert db.tables == {}

    def test_init_with_parent(self):
        """SqlDb stores parent reference."""
        parent = MagicMock()
        db = SqlDb(":memory:", parent=parent)
        assert db.parent is parent


class TestSqlDbEncryptionKey:
    """Tests for encryption_key property."""

    def test_encryption_key_none_without_parent(self):
        """encryption_key returns None when no parent."""
        db = SqlDb(":memory:")
        assert db.encryption_key is None

    def test_encryption_key_from_parent(self):
        """encryption_key is fetched from parent."""
        parent = MagicMock()
        parent.encryption_key = b"secret_key_32_bytes_long_12345"
        db = SqlDb(":memory:", parent=parent)
        assert db.encryption_key == b"secret_key_32_bytes_long_12345"

    def test_encryption_key_none_if_parent_has_no_attr(self):
        """encryption_key returns None if parent lacks attribute."""
        parent = object()  # No encryption_key attribute
        db = SqlDb(":memory:", parent=parent)
        assert db.encryption_key is None


class TestSqlDbTableManagement:
    """Tests for add_table and table methods."""

    def test_add_table_registers_table(self):
        """add_table registers and instantiates table."""
        db = SqlDb(":memory:")
        table = db.add_table(DummyTable)
        assert "dummy" in db.tables
        assert isinstance(table, DummyTable)

    def test_add_table_without_name_raises(self):
        """add_table raises if table has no name."""
        db = SqlDb(":memory:")
        # Remove name attribute for test
        TableWithoutName.name = ""
        with pytest.raises(ValueError, match="must define 'name'"):
            db.add_table(TableWithoutName)

    def test_table_returns_registered_table(self):
        """table() returns registered table instance."""
        db = SqlDb(":memory:")
        db.add_table(DummyTable)
        table = db.table("dummy")
        assert isinstance(table, DummyTable)

    def test_table_raises_for_unknown(self):
        """table() raises ValueError for unregistered table."""
        db = SqlDb(":memory:")
        with pytest.raises(ValueError, match="not registered"):
            db.table("nonexistent")


class TestSqlDbAsyncMethods:
    """Tests for async database methods."""

    async def test_connect_calls_adapter(self):
        """connect() delegates to adapter."""
        db = SqlDb(":memory:")
        db.adapter.connect = AsyncMock()
        await db.connect()
        db.adapter.connect.assert_called_once()

    async def test_close_calls_adapter(self):
        """close() delegates to adapter."""
        db = SqlDb(":memory:")
        db.adapter.close = AsyncMock()
        await db.close()
        db.adapter.close.assert_called_once()

    async def test_execute_delegates_to_adapter(self):
        """execute() delegates to adapter."""
        db = SqlDb(":memory:")
        db.adapter.execute = AsyncMock(return_value=5)
        result = await db.execute("UPDATE test SET x=1", {"x": 1})
        assert result == 5
        db.adapter.execute.assert_called_once()

    async def test_fetch_one_delegates_to_adapter(self):
        """fetch_one() delegates to adapter."""
        db = SqlDb(":memory:")
        db.adapter.fetch_one = AsyncMock(return_value={"id": 1})
        result = await db.fetch_one("SELECT * FROM test WHERE id=:id", {"id": 1})
        assert result == {"id": 1}
        db.adapter.fetch_one.assert_called_once()

    async def test_fetch_all_delegates_to_adapter(self):
        """fetch_all() delegates to adapter."""
        db = SqlDb(":memory:")
        db.adapter.fetch_all = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
        result = await db.fetch_all("SELECT * FROM test")
        assert result == [{"id": 1}, {"id": 2}]
        db.adapter.fetch_all.assert_called_once()

    async def test_commit_delegates_to_adapter(self):
        """commit() delegates to adapter."""
        db = SqlDb(":memory:")
        db.adapter.commit = AsyncMock()
        await db.commit()
        db.adapter.commit.assert_called_once()

    async def test_rollback_delegates_to_adapter(self):
        """rollback() delegates to adapter."""
        db = SqlDb(":memory:")
        db.adapter.rollback = AsyncMock()
        await db.rollback()
        db.adapter.rollback.assert_called_once()


class TestSqlDbCheckStructure:
    """Tests for check_structure method."""

    async def test_check_structure_creates_all_tables(self):
        """check_structure() calls create_schema on all tables."""
        db = SqlDb(":memory:")
        db.add_table(DummyTable)

        # Mock the table's create_schema
        db.tables["dummy"].create_schema = AsyncMock()

        await db.check_structure()

        db.tables["dummy"].create_schema.assert_called_once()
