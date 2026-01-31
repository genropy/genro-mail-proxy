# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Column and Columns definitions for table schema."""

from __future__ import annotations

from typing import Any

# SQL Types
Integer = "INTEGER"
String = "TEXT"
Float = "REAL"
Boolean = "INTEGER"
Timestamp = "TIMESTAMP"
Blob = "BLOB"


class Column:
    """Column definition with SQL type, constraints, and optional relation."""

    def __init__(
        self,
        name: str,
        type_: str,
        *,
        unique: bool = False,
        nullable: bool = True,
        default: Any = None,
        json_encoded: bool = False,
        encrypted: bool = False,
    ):
        self.name = name
        self.type_ = type_
        self.unique = unique
        self.nullable = nullable
        self.default = default
        self.json_encoded = json_encoded
        self.encrypted = encrypted
        # Relation info (set via relation() method)
        self.relation_table: str | None = None
        self.relation_pk: str | None = None
        self.relation_sql: bool = False

    def relation(self, table: str, pk: str = "id", sql: bool = False) -> Column:
        """Define a foreign key relation to another table.

        Args:
            table: Target table name
            pk: Primary key column in target table (default: "id")
            sql: If True, generate SQL FOREIGN KEY constraint
        """
        self.relation_table = table
        self.relation_pk = pk
        self.relation_sql = sql
        return self

    def to_sql(self, *, primary_key: bool = False) -> str:
        """Generate SQL column definition.

        Args:
            primary_key: If True, add PRIMARY KEY constraint.

        Column names are quoted with double quotes to handle reserved words
        like 'user' which is reserved in PostgreSQL.
        """
        # Quote column name to handle reserved words (works for SQLite and PostgreSQL)
        quoted_name = f'"{self.name}"'
        parts = [quoted_name, self.type_]

        if primary_key:
            parts.append("PRIMARY KEY")
        elif self.unique:
            parts.append("UNIQUE")

        if not self.nullable and not primary_key:
            parts.append("NOT NULL")

        if self.default is not None:
            if isinstance(self.default, str):
                if self.default.upper() in ("CURRENT_TIMESTAMP", "NULL"):
                    parts.append(f"DEFAULT {self.default}")
                else:
                    parts.append(f"DEFAULT '{self.default}'")
            elif isinstance(self.default, bool):
                parts.append(f"DEFAULT {1 if self.default else 0}")
            else:
                parts.append(f"DEFAULT {self.default}")

        return " ".join(parts)


class Columns:
    """Container for table columns."""

    def __init__(self):
        self._columns: dict[str, Column] = {}

    def column(
        self,
        name: str,
        type_: str,
        *,
        unique: bool = False,
        nullable: bool = True,
        default: Any = None,
        json_encoded: bool = False,
        encrypted: bool = False,
    ) -> Column:
        """Add a column definition. Returns the Column for fluent relation()."""
        col = Column(
            name=name,
            type_=type_,
            unique=unique,
            nullable=nullable,
            default=default,
            json_encoded=json_encoded,
            encrypted=encrypted,
        )
        self._columns[name] = col
        return col

    def items(self):
        """Return column name-definition pairs."""
        return self._columns.items()

    def keys(self):
        """Return column names."""
        return self._columns.keys()

    def values(self):
        """Return column definitions."""
        return self._columns.values()

    def get(self, name: str) -> Column | None:
        """Get column by name."""
        return self._columns.get(name)

    def json_columns(self) -> list[str]:
        """Return names of JSON-encoded columns."""
        return [name for name, col in self._columns.items() if col.json_encoded]

    def encrypted_columns(self) -> list[str]:
        """Return names of encrypted columns."""
        return [name for name, col in self._columns.items() if col.encrypted]

    def __iter__(self):
        return iter(self._columns)

    def __len__(self):
        return len(self._columns)

    def __contains__(self, name: str) -> bool:
        return name in self._columns


__all__ = [
    "Column",
    "Columns",
    "Integer",
    "String",
    "Float",
    "Boolean",
    "Timestamp",
    "Blob",
]
