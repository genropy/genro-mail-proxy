# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""ASGI application entry point for uvicorn.

This module provides a pre-configured FastAPI application that reads
configuration from the database and initializes the AsyncMailCore
service automatically.

Usage:
    uvicorn async_mail_service.server:app --host 0.0.0.0 --port 8000

Environment variables:
    GMP_DB_PATH: Path to SQLite database (default: /data/mail_service.db)
"""

from __future__ import annotations

import os
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import create_app
from .core import AsyncMailCore


def _get_config_from_db(db_path: str) -> dict[str, str]:
    """Read configuration from database using sync SQLite.

    Uses synchronous SQLite to avoid issues with asyncio.run() when
    uvicorn already has an event loop running at module load time.
    """
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


# Initialize from database
_db_path = os.environ.get("GMP_DB_PATH", "/data/mail_service.db")
_config = _get_config_from_db(_db_path)
_api_token = _config.get("api_token")
_instance_name = _config.get("name", "mail-proxy")

# Create the core service
_core = AsyncMailCore(
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
