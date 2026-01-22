# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Asynchronous email dispatcher microservice with scheduling and rate limiting.

This package provides a complete email dispatch solution with features including:

- Priority-based message queuing with automatic retry
- Per-account rate limiting (per minute, hour, day)
- Attachment handling via HTTP/base64
- Prometheus metrics for monitoring
- FastAPI REST API for control and message submission
- SQLite persistence for reliability

Example:
    Basic usage with the FastAPI application::

        from mail_proxy.core import MailProxy
        from mail_proxy.api import create_app

        core = MailProxy(db_path="/data/mail.db")
        app = create_app(core, api_token="secret")

Authors:
    Softwell S.r.l.
    Giovanni Porcari
"""

