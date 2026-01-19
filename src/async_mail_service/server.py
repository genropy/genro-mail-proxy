"""ASGI application entry point for uvicorn.

This module provides a pre-configured FastAPI application that reads
configuration from environment variables and initializes the AsyncMailCore
service automatically.

Usage:
    uvicorn async_mail_service.server:app --host 0.0.0.0 --port 8000

Environment variables:
    GMP_DB_PATH: Path to SQLite database (default: /data/mail_service.db)
    GMP_CONFIG_FILE: Path to config.ini file (optional)
    GMP_INSTANCE_NAME: Instance name for identification (optional)
    GMP_API_TOKEN: API token for authentication (optional)
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI

from .api import create_app
from .core import AsyncMailCore


def _get_config_value(key: str, default: str | None = None) -> str | None:
    """Get configuration value from environment or config file."""
    import configparser

    # First check environment
    env_value = os.environ.get(key)
    if env_value:
        return env_value

    # Then check config file
    config_file = os.environ.get("GMP_CONFIG_FILE")
    if config_file and os.path.exists(config_file):
        config = configparser.ConfigParser()
        config.read(config_file)

        # Map env var names to config file sections/keys
        config_map = {
            "GMP_DB_PATH": ("server", "db_path"),
            "GMP_API_TOKEN": ("server", "api_token"),
            "GMP_INSTANCE_NAME": ("server", "name"),
        }

        if key in config_map:
            section, option = config_map[key]
            try:
                value = config.get(section, option, fallback=None)
                if value:
                    return value
            except (configparser.NoSectionError, configparser.NoOptionError):
                pass

    return default


# Initialize core service
_db_path = _get_config_value("GMP_DB_PATH", "/data/mail_service.db")
_api_token = _get_config_value("GMP_API_TOKEN")
_instance_name = _get_config_value("GMP_INSTANCE_NAME", "mail-proxy")

# Create the core service
_core = AsyncMailCore(
    db_path=_db_path,
    config_path=os.environ.get("GMP_CONFIG_FILE"),
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
