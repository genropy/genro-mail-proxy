# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Async SQL layer with adapter pattern and table registration.

Usage:
    # Using SqlDb (recommended for table-based access):
    db = SqlDb("/data/mail.db")
    await db.connect()
    db.add_table(TenantsTable)
    await db.check_structure()
    tenant = await db.table("tenants").select_one(where={"id": "acme"})
    await db.close()

    # Direct adapter usage:
    adapter = get_adapter("/data/mail.db")  # SQLite
    adapter = get_adapter("postgresql://user:pass@host/db")  # PostgreSQL
    await adapter.connect()
    rows = await adapter.fetch_all("SELECT * FROM users WHERE active = :active", {"active": 1})
    await adapter.close()
"""

from .adapters import DbAdapter, get_adapter
from .column import Boolean, Column, Columns, Integer, String, Timestamp
from .sqldb import SqlDb
from .table import Table

__all__ = [
    # Main classes
    "SqlDb",
    "Table",
    # Column definitions
    "Column",
    "Columns",
    "Integer",
    "String",
    "Boolean",
    "Timestamp",
    # Adapters
    "DbAdapter",
    "get_adapter",
    # Backward compatibility
    "create_adapter",
]

# Backward compatibility alias
create_adapter = get_adapter
