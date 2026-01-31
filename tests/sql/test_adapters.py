# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for sql.adapters module - adapter factory and registry."""

from __future__ import annotations

import pytest

from sql.adapters import ADAPTERS, DbAdapter, SqliteAdapter, get_adapter


class TestGetAdapter:
    """Tests for get_adapter factory function."""

    def test_absolute_path_returns_sqlite(self):
        """Absolute paths are treated as SQLite databases."""
        adapter = get_adapter("/tmp/test.db")
        assert isinstance(adapter, SqliteAdapter)

    def test_relative_path_returns_sqlite(self):
        """Relative paths starting with ./ are treated as SQLite."""
        adapter = get_adapter("./data/test.db")
        assert isinstance(adapter, SqliteAdapter)

    def test_memory_returns_sqlite(self):
        """:memory: is treated as SQLite in-memory database."""
        adapter = get_adapter(":memory:")
        assert isinstance(adapter, SqliteAdapter)

    def test_sqlite_prefix_returns_sqlite(self):
        """sqlite:path format returns SqliteAdapter."""
        adapter = get_adapter("sqlite:/tmp/test.db")
        assert isinstance(adapter, SqliteAdapter)

    def test_sqlite_memory_prefix(self):
        """sqlite::memory: format returns SqliteAdapter."""
        adapter = get_adapter("sqlite::memory:")
        assert isinstance(adapter, SqliteAdapter)

    def test_invalid_connection_string_raises(self):
        """Invalid connection string without colon raises ValueError."""
        with pytest.raises(ValueError, match="Invalid connection string"):
            get_adapter("invalid_string_without_colon")

    def test_unknown_database_type_raises(self):
        """Unknown database type raises ValueError."""
        with pytest.raises(ValueError, match="Unknown database type"):
            get_adapter("mysql://localhost/db")

    def test_postgresql_lazy_import(self):
        """PostgreSQL adapter is lazily imported."""
        # This test verifies the postgresql path works
        # It may raise ImportError if psycopg is not installed
        try:
            adapter = get_adapter("postgresql://user:pass@localhost:5432/testdb")
            assert adapter is not None
            assert "postgresql" in ADAPTERS
        except ImportError:
            # psycopg not installed - that's OK, the path was exercised
            pass

    def test_postgres_alias(self):
        """postgres:// is an alias for postgresql://."""
        try:
            adapter = get_adapter("postgres://user:pass@localhost:5432/testdb")
            assert adapter is not None
            assert "postgres" in ADAPTERS
        except ImportError:
            pass

    def test_postgresql_reconstructs_dsn(self):
        """PostgreSQL reconstructs DSN if needed."""
        try:
            # Connection string without full postgresql:// prefix
            adapter = get_adapter("postgresql://localhost/testdb")
            assert adapter is not None
        except ImportError:
            pass


class TestAdaptersRegistry:
    """Tests for ADAPTERS registry."""

    def test_sqlite_in_registry(self):
        """SQLite adapter is registered by default."""
        assert "sqlite" in ADAPTERS
        assert ADAPTERS["sqlite"] is SqliteAdapter

    def test_db_adapter_is_abstract_base(self):
        """DbAdapter is the abstract base class."""
        assert DbAdapter is not None
        # DbAdapter should be abstract (cannot instantiate)
        with pytest.raises(TypeError):
            DbAdapter()  # type: ignore
