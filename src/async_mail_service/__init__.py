"""Asynchronous email dispatcher microservice with scheduling and rate limiting.

This package provides a complete email dispatch solution with features including:

- Priority-based message queuing with automatic retry
- Per-account rate limiting (per minute, hour, day)
- Attachment handling via genro-storage integration
- Prometheus metrics for monitoring
- FastAPI REST API for control and message submission
- SQLite persistence for reliability

Example:
    Basic usage with the FastAPI application::

        from async_mail_service.core import AsyncMailCore
        from async_mail_service.api import create_app

        core = AsyncMailCore(db_path="/data/mail.db")
        app = create_app(core, api_token="secret")

Authors:
    Softwell S.r.l.
    Giovanni Porcari
"""

