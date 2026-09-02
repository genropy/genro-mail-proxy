# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""ASGI application entry point for uvicorn.

This module builds the genro-asgi application from the environment and the
database and hands it to an ``AsgiServer``, which is the ASGI callable uvicorn
serves. The v1 contract answers under ``/mailproxy/v1`` (ADR-011).

The auth middleware is off: ``MailProxyApplication`` resolves ``X-API-Token``
itself, before dispatch, against the tenants table.

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
from typing import Any

from genro_asgi import AsgiServer

from .bounce import BounceConfig
from .core import MailProxy
from .mail_proxy_application import MailProxyApplication

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
    instance_table = _core.db.instance

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
    instance_table = _core.db.instance
    bounce_config = await instance_table.get_bounce_config()

    if not bounce_config.get("enabled"):
        _logger.debug("Bounce detection disabled")
        return

    host = bounce_config.get("imap_host")
    if not host:
        _logger.warning("Bounce enabled but imap_host not configured")
        return

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


class ServerApplication(MailProxyApplication):
    """The application this module boots: v1 plus the environment seeding.

    ``MailProxyApplication.on_startup`` starts the engine. This subclass adds
    what only a deployment knows: seeding the instance row from ``GMP_BOUNCE_*``
    the first time, and starting the bounce receiver on whatever the database
    then holds. Signal handling is left to tini and uvicorn — a SIGTERM reaches
    the server's lifespan, which runs ``on_shutdown`` and stops the engine.
    """

    async def on_startup(self) -> None:
        _logger.info("Starting mail-proxy service...")
        await super().on_startup()
        await _initialize_instance_from_env()
        await _configure_bounce_from_db()
        _logger.info("Mail-proxy service started")

    async def on_shutdown(self) -> None:
        _logger.info("Stopping mail-proxy service...")
        await super().on_shutdown()
        _logger.info("Mail-proxy service stopped")


def build_server(**kwargs: Any) -> AsgiServer:
    """Build the ASGI callable uvicorn serves: the server owning the application."""
    application = ServerApplication(core=_core, api_token=_api_token)
    return AsgiServer(applications=[application], middleware={"auth": False}, **kwargs)


app = build_server()
