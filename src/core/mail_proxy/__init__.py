# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Email dispatcher microservice with multi-tenant support.

This package provides an asynchronous email dispatch service with
queueing, rate limiting, retry logic, and multi-backend attachments.

Components:
    MailProxy: Main service class with SMTP sender and background loops.
    MailProxyBase: Foundation layer with database and endpoint discovery.
    ProxyConfig: Hierarchical configuration dataclasses.

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

Example:
    Create and run the mail service::

        from core.mail_proxy.proxy import MailProxy
        from core.mail_proxy.interface import create_app

        proxy = MailProxy(db_path="/data/mail.db")
        app = create_app(proxy, api_token="secret")

        # Or via CLI
        # mail-proxy serve --port 8000

Note:
    Enterprise Edition (EE) extends this package with bounce detection,
    PEC (certified email), and per-tenant API keys when the enterprise
    package is installed.
"""

# Import submodules to ensure they are accessible via core.mail_proxy.module
# This is required for patch() in tests to work correctly
from . import proxy as proxy  # noqa: PLC0414
from . import proxy_base as proxy_base  # noqa: PLC0414
from . import proxy_config as proxy_config  # noqa: PLC0414

# Enterprise Edition detection
# When EE modules are installed, MailProxy includes enterprise features
# (multi-tenant API, PEC, bounce detection).
try:
    from enterprise.mail_proxy import MailProxy_EE

    HAS_ENTERPRISE = True
except ImportError:
    MailProxy_EE = None  # type: ignore[misc, assignment]
    HAS_ENTERPRISE = False


def main() -> None:
    """CLI entry point. Creates a MailProxy and runs the CLI.

    Resolves the current instance from context (via `mail-proxy use`)
    and configures the MailProxy with the correct database path.
    """
    from .interface.cli_commands import _get_instance_config, resolve_context
    from .proxy import MailProxy
    from .proxy_config import ProxyConfig

    instance, _ = resolve_context()
    config = ProxyConfig()

    if instance:
        instance_config = _get_instance_config(instance)
        if instance_config:
            config = ProxyConfig(
                db_path=instance_config["db_path"],
                api_token=instance_config.get("api_token") or None,
                instance_name=instance,
            )

    mail_proxy = MailProxy(config=config)
    mail_proxy.cli()()
