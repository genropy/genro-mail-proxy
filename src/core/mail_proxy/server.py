# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""ASGI application entry point for uvicorn.

This module provides a pre-configured FastAPI application that reads
configuration from the database and initializes the MailProxy
service automatically.

Usage:
    uvicorn mail_proxy.server:app --host 0.0.0.0 --port 8000

Environment variables (permanent - always read):
    GMP_DB_PATH: Database connection string. Formats:
        - /path/to/db.sqlite (SQLite file)
        - postgresql://user:pass@host/db (PostgreSQL)
        Default: ./mail_service.db (local dev); Docker sets /data/mail_service.db

    GMP_API_TOKEN: API authentication token.

Environment variables (initialization - only used if instance table is empty):
    GMP_BOUNCE_ENABLED: Set to "1" or "true" to enable bounce detection.
    GMP_BOUNCE_IMAP_HOST: IMAP server hostname for bounce mailbox.
    GMP_BOUNCE_IMAP_PORT: IMAP server port (default: 143 for non-SSL, 993 for SSL).
    GMP_BOUNCE_IMAP_USER: IMAP username for bounce mailbox.
    GMP_BOUNCE_IMAP_PASSWORD: IMAP password for bounce mailbox.
    GMP_BOUNCE_IMAP_SSL: Set to "1" or "true" for SSL connection (default: false).
    GMP_BOUNCE_POLL_INTERVAL: Polling interval in seconds (default: 60).

    These env vars are used ONLY at first startup to populate the instance
    table in the database. After that, configuration is read from the DB.
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
    """Read configuration from SQLite database.

    Reads from the 'instance' table (singleton id=1) which has:
    - Typed columns: name, api_token, edition
    - JSON column: config (for host, port, start_active, etc.)
    """
    if not os.path.exists(db_path):
        return {}
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute("SELECT name, api_token, edition, config FROM instance WHERE id = 1")
        row = cursor.fetchone()
        if not row:
            return {}
        result: dict[str, str] = {}
        # Add typed columns
        if row["name"]:
            result["name"] = row["name"]
        if row["api_token"]:
            result["api_token"] = row["api_token"]
        if row["edition"]:
            result["edition"] = row["edition"]
        # Merge JSON config
        if row["config"]:
            import json
            try:
                config_json = json.loads(row["config"])
                for k, v in config_json.items():
                    result[k] = str(v) if v is not None else ""
            except json.JSONDecodeError:
                pass
        return result
    except sqlite3.OperationalError:
        # Table doesn't exist yet
        return {}
    finally:
        conn.close()


def _get_config_from_postgres(dsn: str) -> dict[str, str]:
    """Read configuration from PostgreSQL database.

    Reads from the 'instance' table (singleton id=1) which has:
    - Typed columns: name, api_token, edition
    - JSON column: config (for host, port, start_active, etc.)
    """
    try:
        import psycopg
        import psycopg.errors
    except ImportError:
        return {}
    try:
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT name, api_token, edition, config FROM instance WHERE id = 1")
            row = cur.fetchone()
            if not row:
                return {}
            result: dict[str, str] = {}
            # Add typed columns (row is a tuple: name, api_token, edition, config)
            if row[0]:
                result["name"] = row[0]
            if row[1]:
                result["api_token"] = row[1]
            if row[2]:
                result["edition"] = row[2]
            # Merge JSON config
            if row[3]:
                import json
                try:
                    config_json = json.loads(row[3]) if isinstance(row[3], str) else row[3]
                    for k, v in config_json.items():
                        result[k] = str(v) if v is not None else ""
                except (json.JSONDecodeError, TypeError):
                    pass
            return result
    except (psycopg.OperationalError, psycopg.errors.UndefinedTable):
        # Database or table doesn't exist yet
        return {}


# Initialize from database and environment
# Default to current directory for local development; Docker sets GMP_DB_PATH=/data/mail_service.db
_db_path = os.environ.get("GMP_DB_PATH", "./mail_service.db")
_config = _get_config_from_db(_db_path)
# API token from env (permanent config, not in DB)
_api_token = os.environ.get("GMP_API_TOKEN") or _config.get("api_token")


def _is_truthy(value: str | None) -> bool:
    """Check if environment variable value is truthy."""
    return value is not None and value.lower() in ("1", "true", "yes", "on")


def _get_bounce_env_vars() -> dict | None:
    """Get bounce configuration from environment variables.

    Returns dict with bounce config if GMP_BOUNCE_ENABLED is set, None otherwise.
    Used only for initial DB population.
    """
    if not _is_truthy(os.environ.get("GMP_BOUNCE_ENABLED")):
        return None

    host = os.environ.get("GMP_BOUNCE_IMAP_HOST")
    if not host:
        _logger.warning("GMP_BOUNCE_ENABLED=1 but GMP_BOUNCE_IMAP_HOST not set")
        return None

    return {
        "enabled": True,
        "imap_host": host,
        "imap_port": int(os.environ.get("GMP_BOUNCE_IMAP_PORT", "143")),
        "imap_user": os.environ.get("GMP_BOUNCE_IMAP_USER", ""),
        "imap_password": os.environ.get("GMP_BOUNCE_IMAP_PASSWORD", ""),
        "imap_ssl": _is_truthy(os.environ.get("GMP_BOUNCE_IMAP_SSL")),
        "poll_interval": int(os.environ.get("GMP_BOUNCE_POLL_INTERVAL", "60")),
    }


# Create the core service
_core = MailProxy(
    db_path=_db_path,
    start_active=True,
)


async def _initialize_instance_from_env() -> None:
    """Initialize instance table from environment variables if not yet configured.

    This is called once at startup. If the instance record doesn't exist or
    bounce is not configured, it populates from GMP_BOUNCE_* env vars.
    """
    instance_table = _core.db.table('instance')

    # Ensure instance record exists
    instance = await instance_table.ensure_instance()

    # Check if bounce is already configured in DB
    if instance.get("bounce_imap_host"):
        _logger.debug("Bounce config already in DB, skipping env var initialization")
        return

    # Try to initialize from env vars
    bounce_env = _get_bounce_env_vars()
    if bounce_env:
        _logger.info("Initializing bounce config from environment variables")
        await instance_table.set_bounce_config(
            enabled=bounce_env["enabled"],
            imap_host=bounce_env["imap_host"],
            imap_port=bounce_env["imap_port"],
            imap_user=bounce_env["imap_user"],
            imap_password=bounce_env["imap_password"],
        )


async def _configure_bounce_from_db() -> None:
    """Configure BounceReceiver from database if enabled."""
    instance_table = _core.db.table('instance')
    bounce_config = await instance_table.get_bounce_config()

    if not bounce_config.get("enabled"):
        _logger.debug("Bounce detection disabled")
        return

    host = bounce_config.get("imap_host")
    if not host:
        _logger.warning("Bounce enabled but imap_host not configured")
        return

    from .bounce import BounceConfig

    config = BounceConfig(
        host=host,
        port=bounce_config.get("imap_port") or 993,
        user=bounce_config.get("imap_user") or "",
        password=bounce_config.get("imap_password") or "",
        use_ssl=bounce_config.get("imap_ssl", True),
        poll_interval=bounce_config.get("poll_interval") or 60,
    )

    _logger.info(f"Configuring bounce detection: {config.host}:{config.port}")
    _core.configure_bounce_receiver(config)
    await _core._start_bounce_receiver()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler - starts and stops the core service.

    Signal handling is delegated to tini (Docker init) and uvicorn.
    When uvicorn receives SIGTERM/SIGINT, it triggers the lifespan shutdown
    which calls _core.stop() to gracefully terminate background tasks.
    """
    _logger.info("Starting mail-proxy service...")
    await _core.start()

    # Initialize instance config from env vars if needed
    await _initialize_instance_from_env()

    # Configure bounce from DB (after potential env var initialization)
    await _configure_bounce_from_db()

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
