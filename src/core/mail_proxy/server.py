# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""ASGI application entry point for uvicorn.

Usage:
    uvicorn core.mail_proxy.server:app --host 0.0.0.0 --port 8000

Environment variables:
    GMP_DB_PATH: Database path (default: ./mail_service.db)
    GMP_API_TOKEN: API authentication token
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .interface import create_app
from .proxy import MailProxy

_logger = logging.getLogger(__name__)

# Configuration from environment
_db_path = os.environ.get("GMP_DB_PATH", "./mail_service.db")
_api_token = os.environ.get("GMP_API_TOKEN")

# Create the core service
_core = MailProxy(db_path=_db_path, start_active=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Start and stop the MailProxy service."""
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
app = create_app(_core, api_token=_api_token, lifespan=lifespan)
