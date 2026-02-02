# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""ASGI application entry point for uvicorn.

This module provides the FastAPI application instance for deployment
with ASGI servers like uvicorn, hypercorn, or gunicorn+uvicorn.

Configuration via environment variables:
    GMP_DB_PATH: Database path (SQLite file or PostgreSQL URL)
    GMP_API_TOKEN: API authentication token

Components:
    app: FastAPI application with full MailProxy lifecycle management.
    _proxy: Internal MailProxy instance (use app instead).

Example:
    Run with uvicorn::

        GMP_DB_PATH=/data/mail.db GMP_API_TOKEN=secret \\
            uvicorn core.mail_proxy.server:app --host 0.0.0.0 --port 8000

    Run with Docker::

        docker run -e GMP_DB_PATH=postgresql://... -e GMP_API_TOKEN=secret ...

    Or via CLI (reads from ~/.mail-proxy/<name>/config.ini)::

        mail-proxy serve --port 8000

Note:
    The application includes a lifespan context manager that calls
    proxy.start() on startup and proxy.stop() on shutdown, ensuring
    proper initialization of background tasks and graceful cleanup.

Design Decision (2025-02):
    API tokens are stored in plaintext in config.ini for CLI instances.
    This is acceptable for development; production uses Docker with
    environment variables. Hashing like tenant tokens was considered
    but deferred since Docker passes tokens via env vars anyway.
"""

import os

from .proxy import MailProxy
from .proxy_config import ProxyConfig


def _config_from_env() -> ProxyConfig:
    """Build ProxyConfig from GMP_* environment variables."""
    db_path = os.environ.get("GMP_DB_PATH", "/data/mail_service.db")
    api_token = os.environ.get("GMP_API_TOKEN")
    return ProxyConfig(db_path=db_path, api_token=api_token)


# Create proxy and expose its API (includes lifespan management)
_proxy = MailProxy(config=_config_from_env())
app = _proxy.api
