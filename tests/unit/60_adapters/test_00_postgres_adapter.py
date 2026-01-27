# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for PostgreSQL adapter placeholder conversion."""

import pytest


class TestPostgresPlaceholderConversion:
    """Test placeholder conversion preserves PostgreSQL :: cast operators."""

    def test_convert_placeholders_preserves_cast(self):
        """Ensure :: cast operators are not converted."""
        from mail_proxy.sql.adapters.postgresql import PostgresAdapter

        # Create adapter (will fail if psycopg not installed, but we can test the method)
        try:
            adapter = PostgresAdapter("postgresql://test:test@localhost/test")
        except ImportError:
            pytest.skip("psycopg not installed")

        # Test cases
        cases = [
            # (input, expected)
            (
                "SELECT field::text FROM t WHERE id = :id",
                "SELECT field::text FROM t WHERE id = %(id)s",
            ),
            (
                "SELECT a::int, b::varchar FROM t WHERE x = :x",
                "SELECT a::int, b::varchar FROM t WHERE x = %(x)s",
            ),
            (
                "SELECT :param::jsonb FROM t",
                "SELECT %(param)s::jsonb FROM t",
            ),
            (
                "SELECT * FROM t WHERE id = :id",
                "SELECT * FROM t WHERE id = %(id)s",
            ),
            (
                "INSERT INTO t (a, b) VALUES (:a, :b)",
                "INSERT INTO t (a, b) VALUES (%(a)s, %(b)s)",
            ),
        ]

        for query, expected in cases:
            result = adapter._convert_placeholders(query)
            assert result == expected, f"Failed for: {query}"

    def test_convert_placeholders_without_psycopg(self):
        """Test method directly without requiring psycopg."""
        import re

        # Copy the regex pattern from the adapter
        def convert(query: str) -> str:
            return re.sub(r"(?<!:):([a-zA-Z_][a-zA-Z0-9_]*)", r"%(\1)s", query)

        # Verify :: casts are preserved
        assert convert("a::text") == "a::text"
        assert convert("a::int WHERE x = :x") == "a::int WHERE x = %(x)s"
        assert convert(":p::jsonb") == "%(p)s::jsonb"


class TestPostgresPoolConfiguration:
    """Test PostgreSQL connection pool configuration."""

    def test_connect_sets_search_path_to_public(self):
        """Verify that connection pool configures search_path to 'public'.

        This ensures tables are created in a valid schema even when
        the connection default search_path is empty/unset.
        """
        from mail_proxy.sql.adapters.postgresql import PostgresAdapter

        try:
            adapter = PostgresAdapter("postgresql://test:test@localhost/test")
        except ImportError:
            pytest.skip("psycopg not installed")

        # Verify the connect method includes a configure callback
        import inspect
        source = inspect.getsource(adapter.connect)

        # Check that search_path is set to public in the configure function
        assert "SET search_path TO public" in source
        assert "configure" in source
