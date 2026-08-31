# Copyright 2025 Softwell S.r.l.
# Licensed under the Apache License, Version 2.0

"""Unit tests for get_adapter function."""

from __future__ import annotations

import pytest

from src.mail_proxy.sql.adapters import get_adapter, ADAPTERS
from src.mail_proxy.sql.adapters.sqlite import SqliteAdapter


class TestGetAdapterSqlite:
    """Tests for SQLite adapter selection."""

    def test_absolute_path_returns_sqlite(self, tmp_path):
        """Absolute path should return SQLite adapter."""
        db_path = str(tmp_path / "test.db")
        adapter = get_adapter(db_path)

        assert isinstance(adapter, SqliteAdapter)

    def test_relative_path_returns_sqlite(self):
        """Relative path starting with ./ should return SQLite adapter."""
        adapter = get_adapter("./test.db")

        assert isinstance(adapter, SqliteAdapter)

    def test_memory_returns_sqlite(self):
        """':memory:' should return SQLite in-memory adapter."""
        adapter = get_adapter(":memory:")

        assert isinstance(adapter, SqliteAdapter)

    def test_sqlite_prefix_returns_sqlite(self):
        """'sqlite:' prefix should return SQLite adapter."""
        adapter = get_adapter("sqlite:/tmp/test.db")

        assert isinstance(adapter, SqliteAdapter)

    def test_sqlite_memory_prefix(self):
        """'sqlite::memory:' should return SQLite in-memory adapter."""
        adapter = get_adapter("sqlite::memory:")

        assert isinstance(adapter, SqliteAdapter)


class TestGetAdapterPostgres:
    """Tests for PostgreSQL adapter selection."""

    def test_postgresql_prefix_imports_adapter(self):
        """'postgresql:' prefix should try to import PostgreSQL adapter."""
        try:
            adapter = get_adapter("postgresql://user:pass@localhost:5432/db")
            # If psycopg is installed, we get a PostgresAdapter
            from src.mail_proxy.sql.adapters.postgresql import PostgresAdapter
            assert isinstance(adapter, PostgresAdapter)
        except ImportError:
            # psycopg not installed - expected in unit tests
            pytest.skip("psycopg not installed")

    def test_postgres_prefix_imports_adapter(self):
        """'postgres:' prefix should also work (alias)."""
        try:
            adapter = get_adapter("postgres://user:pass@localhost:5432/db")
            from src.mail_proxy.sql.adapters.postgresql import PostgresAdapter
            assert isinstance(adapter, PostgresAdapter)
        except ImportError:
            pytest.skip("psycopg not installed")

    def test_postgres_registers_in_adapters(self):
        """PostgreSQL adapter should be registered in ADAPTERS dict."""
        try:
            get_adapter("postgresql://user:pass@localhost:5432/db")
            assert "postgresql" in ADAPTERS
            assert "postgres" in ADAPTERS
        except ImportError:
            pytest.skip("psycopg not installed")


class TestGetAdapterErrors:
    """Tests for error handling."""

    def test_invalid_connection_string_no_colon(self):
        """Invalid string without colon should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            get_adapter("invalid_no_colon")

        assert "Invalid connection string" in str(exc_info.value)
        assert "invalid_no_colon" in str(exc_info.value)

    def test_unknown_database_type(self):
        """Unknown database type should raise ValueError."""
        with pytest.raises(ValueError) as exc_info:
            get_adapter("mysql://localhost/db")

        assert "Unknown database type" in str(exc_info.value)
        assert "mysql" in str(exc_info.value)
        assert "Supported: sqlite, postgresql" in str(exc_info.value)

    def test_unknown_type_oracle(self):
        """Oracle is not supported."""
        with pytest.raises(ValueError) as exc_info:
            get_adapter("oracle://localhost/db")

        assert "Unknown database type" in str(exc_info.value)
        assert "oracle" in str(exc_info.value)


class TestAdaptersRegistry:
    """Tests for ADAPTERS registry."""

    def test_sqlite_in_adapters(self):
        """SQLite adapter should be in registry by default."""
        assert "sqlite" in ADAPTERS
        assert ADAPTERS["sqlite"] is SqliteAdapter

    def test_adapters_is_dict(self):
        """ADAPTERS should be a dict."""
        assert isinstance(ADAPTERS, dict)
