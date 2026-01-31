# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""ASGI application entry point for uvicorn.

This module provides the FastAPI application instance for deployment
with ASGI servers like uvicorn, hypercorn, or gunicorn+uvicorn.

Components:
    app: FastAPI application with full MailProxy lifecycle management.
    _proxy: Internal MailProxy instance (use app instead).

Example:
    Run with uvicorn::

        uvicorn core.mail_proxy.server:app --host 0.0.0.0 --port 8000

    Run with reload for development::

        uvicorn core.mail_proxy.server:app --reload

    Or via CLI::

        mail-proxy serve --port 8000

Note:
    The application includes a lifespan context manager that calls
    proxy.start() on startup and proxy.stop() on shutdown, ensuring
    proper initialization of background tasks and graceful cleanup.
"""

from .proxy import MailProxy

# Create proxy and expose its API (includes lifespan management)
_proxy = MailProxy()
app = _proxy.api
