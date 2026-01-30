# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""ASGI application entry point for uvicorn.

Usage:
    uvicorn core.mail_proxy.server:app --host 0.0.0.0 --port 8000

The MailProxy instance creates and configures the FastAPI application
with automatic lifecycle management (start/stop on server startup/shutdown).
"""

from .proxy import MailProxy

# Create proxy and expose its API (includes lifespan management)
_proxy = MailProxy()
app = _proxy.api
