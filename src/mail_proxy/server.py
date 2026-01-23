# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""ASGI application entry point for uvicorn.

This module provides a pre-configured FastAPI application that reads
configuration from the database and initializes the MailProxy
service automatically.

Usage:
    uvicorn mail_proxy.server:app --host 0.0.0.0 --port 8000

Environment variables:
    GMP_DB_PATH: Database connection string. Formats:
        - /path/to/db.sqlite (SQLite file)
        - postgresql://user:pass@host/db (PostgreSQL)
        Default: /data/mail_service.db
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import create_app
from .core import MailProxy


def _get_config_from_db(connection_string: str) -> dict[str, str]:
    """Read configuration from database using sync connection.

    Uses synchronous connection to avoid issues with asyncio.run() when
    uvicorn already has an event loop running at module load time.
    """
    if connection_string.startswith("postgresql://"):
        return _get_config_from_postgres(connection_string)
    return _get_config_from_sqlite(connection_string)


def _get_config_from_sqlite(db_path: str) -> dict[str, str]:
    """Read configuration from SQLite database."""
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.execute("SELECT key, value FROM instance_config")
        return {row[0]: row[1] for row in cursor.fetchall()}
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return {}
    finally:
        conn.close()


def _get_config_from_postgres(dsn: str) -> dict[str, str]:
    """Read configuration from PostgreSQL database."""
    try:
        import psycopg
        import psycopg.errors
    except ImportError:
        return {}
    try:
        with psycopg.connect(dsn) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT key, value FROM instance_config")
                return {row[0]: row[1] for row in cur.fetchall()}
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable):
        # Database or table doesn't exist yet
        return {}


# Initialize from database and environment
_db_path = os.environ.get("GMP_DB_PATH", "/data/mail_service.db")
_config = _get_config_from_db(_db_path)
# API token from env takes precedence over database config
_api_token = os.environ.get("GMP_API_TOKEN") or _config.get("api_token")
_instance_name = _config.get("name", "mail-proxy")

# Create the core service
_core = MailProxy(
    db_path=_db_path,
    start_active=True,
)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler - starts and stops the core service."""
    await _core.start()
    yield
    await _core.stop()


# Create the configured application
app = create_app(_core, api_token=_api_token, lifespan=lifespan)
