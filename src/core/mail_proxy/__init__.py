# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Email dispatcher microservice with multi-tenant support.

Features:
    - Multi-tenant isolation with per-tenant configuration
    - Priority-based message queuing (immediate/high/medium/low)
    - Per-account rate limiting (minute/hour/day)
    - Automatic retry with exponential backoff
    - Attachment fetching (HTTP endpoint, URL, base64, filesystem)
    - Delivery report callbacks to client applications
    - Prometheus metrics for monitoring
    - FastAPI REST API for control and message submission
    - SQLite/PostgreSQL persistence

Example::

    from core.mail_proxy.proxy import MailProxy
    from core.mail_proxy.interface import create_app

    proxy = MailProxy(db_path="/data/mail.db")
    app = create_app(proxy, api_token="secret")
"""

# Enterprise Edition detection
# When EE modules are installed, MailProxy includes enterprise features
# (multi-tenant API, PEC, bounce detection).
try:
    from enterprise.mail_proxy import MailProxy_EE

    HAS_ENTERPRISE = True
except ImportError:
    MailProxy_EE = None  # type: ignore[misc, assignment]
    HAS_ENTERPRISE = False

