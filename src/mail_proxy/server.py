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
        Default: ./mail_service.db (local dev); Docker sets /data/mail_service.db

    GMP_API_TOKEN: API authentication token.

    GMP_BOUNCE_ENABLED: Set to "1" or "true" to enable bounce detection.
    GMP_BOUNCE_IMAP_HOST: IMAP server hostname for bounce mailbox.
    GMP_BOUNCE_IMAP_PORT: IMAP server port (default: 143 for non-SSL, 993 for SSL).
    GMP_BOUNCE_IMAP_USER: IMAP username for bounce mailbox.
    GMP_BOUNCE_IMAP_PASSWORD: IMAP password for bounce mailbox.
    GMP_BOUNCE_IMAP_SSL: Set to "1" or "true" for SSL connection (default: false).
    GMP_BOUNCE_POLL_INTERVAL: Polling interval in seconds (default: 60).
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import create_app
from .core import MailProxy

_logger = logging.getLogger(__name__)


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
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT key, value FROM instance_config")
            return {row[0]: row[1] for row in cur.fetchall()}
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable):
        # Database or table doesn't exist yet
        return {}


# Initialize from database and environment
# Default to current directory for local development; Docker sets GMP_DB_PATH=/data/mail_service.db
_db_path = os.environ.get("GMP_DB_PATH", "./mail_service.db")
_config = _get_config_from_db(_db_path)
# API token from env takes precedence over database config
_api_token = os.environ.get("GMP_API_TOKEN") or _config.get("api_token")
_instance_name = _config.get("name", "mail-proxy")


def _is_truthy(value: str | None) -> bool:
    """Check if environment variable value is truthy."""
    return value is not None and value.lower() in ("1", "true", "yes", "on")


def _get_bounce_config():
    """Get BounceConfig from environment variables if enabled."""
    if not _is_truthy(os.environ.get("GMP_BOUNCE_ENABLED")):
        return None

    host = os.environ.get("GMP_BOUNCE_IMAP_HOST")
    if not host:
        _logger.warning("GMP_BOUNCE_ENABLED=1 but GMP_BOUNCE_IMAP_HOST not set")
        return None

    from .bounce import BounceConfig

    return BounceConfig(
        host=host,
        port=int(os.environ.get("GMP_BOUNCE_IMAP_PORT", "143")),
        user=os.environ.get("GMP_BOUNCE_IMAP_USER", ""),
        password=os.environ.get("GMP_BOUNCE_IMAP_PASSWORD", ""),
        use_ssl=_is_truthy(os.environ.get("GMP_BOUNCE_IMAP_SSL")),
        poll_interval=int(os.environ.get("GMP_BOUNCE_POLL_INTERVAL", "60")),
    )


# Create the core service
_core = MailProxy(
    db_path=_db_path,
    start_active=True,
)

# Configure bounce detection if enabled via environment
_bounce_config = _get_bounce_config()
if _bounce_config:
    _logger.info(f"Configuring bounce detection: {_bounce_config.host}:{_bounce_config.port}")
    _core.configure_bounce_receiver(_bounce_config)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler - starts and stops the core service.

    Signal handling is delegated to tini (Docker init) and uvicorn.
    When uvicorn receives SIGTERM/SIGINT, it triggers the lifespan shutdown
    which calls _core.stop() to gracefully terminate background tasks.
    """
    _logger.info("Starting mail-proxy service...")
    await _core.start()
    _logger.info("Mail-proxy service started")

    try:
        yield
    finally:
        _logger.info("Stopping mail-proxy service...")
        await _core.stop()
        _logger.info("Mail-proxy service stopped")


# Create the configured application
app = create_app(
    _core,
    api_token=_api_token,
    lifespan=lifespan,
)
