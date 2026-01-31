# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Base class for MailProxy: database, tables, endpoints, and interface factories.

MailProxyBase is the foundation layer of genro-mail-proxy, providing:

1. Configuration: ProxyConfig instance at self.config
2. Database: SqlDb at self.db with autodiscovered Table classes
3. Endpoints: Registry at self.endpoints with autodiscovered Endpoint classes
4. Interfaces: Lazy `api` (FastAPI) and `cli` (Click) properties

Class Hierarchy:
    MailProxyBase (this class)
        └── MailProxy (proxy.py): adds SMTP sender, background loops, metrics
            └── MailProxy with MailProxy_EE mixin: adds bounce detection (EE)

Discovery Mechanism:
    Tables from `core.mail_proxy.entities.*/table.py` are composed with
    optional EE mixins from `enterprise.mail_proxy.entities.*/table_ee.py`.
    Endpoints follow the same pattern with `endpoint.py` and `endpoint_ee.py`.

    Discovered entities (CE):
        - tenants: Multi-tenant isolation
        - accounts: SMTP account configuration
        - messages: Message queue with priority
        - message_events: Delivery event history
        - command_log: API audit trail
        - instance: Service-level configuration

    EE extensions (when enterprise package installed):
        - accounts: +PEC (certified email) fields
        - tenants: +API key management
        - instance: +Bounce detection config

Usage (testing without runtime):
    proxy = MailProxyBase(db_path=":memory:")
    await proxy.init()
    await proxy.db.table("tenants").add({"id": "t1", "name": "Test"})

Usage (production via proxy.api):
    proxy = MailProxy(config=ProxyConfig(db_path="/data/mail.db"))
    app = proxy.api  # FastAPI app with auto-start/stop lifespan
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import TYPE_CHECKING, Any

from sql import SqlDb

from .interface import BaseEndpoint
from .proxy_config import ProxyConfig

if TYPE_CHECKING:
    import click
    from fastapi import FastAPI

# Packages to scan for entities
_CE_ENTITIES_PACKAGE = "core.mail_proxy.entities"
_EE_ENTITIES_PACKAGE = "enterprise.mail_proxy.entities"


class MailProxyBase:
    """Foundation layer: config, database, tables, endpoints, interface factories.

    Attributes:
        config: ProxyConfig instance with all configuration
        db: SqlDb with autodiscovered Table classes (CE + EE mixins)
        endpoints: Dict of Endpoint instances keyed by name

    Properties:
        api: FastAPI app (lazy, created on first access)
        cli: Click CLI group (lazy, created on first access)

    Subclassed by MailProxy which adds SMTP sender, background loops, etc.
    """

    def __init__(self, config: ProxyConfig | None = None):
        """Initialize base proxy with config and database.

        Args:
            config: ProxyConfig instance. If None, creates default.
        """
        self.config = config or ProxyConfig()

        self._encryption_key: bytes | None = None
        self._load_encryption_key()

        self.db = SqlDb(self.config.db_path or ":memory:", parent=self)
        self._discover_tables()

        self.endpoints: dict[str, BaseEndpoint] = {}
        self._discover_endpoints()

    def _load_encryption_key(self) -> None:
        """Load encryption key from environment or secrets file.

        Sources (in priority order):
        1. MAIL_PROXY_ENCRYPTION_KEY env var (base64-encoded 32 bytes)
        2. /run/secrets/encryption_key file (Docker/K8s secrets)

        If no key is configured, encryption is disabled (fields stored as plaintext).
        """
        import base64
        import os
        from pathlib import Path

        # 1. Environment variable
        key_b64 = os.environ.get("MAIL_PROXY_ENCRYPTION_KEY")
        if key_b64:
            try:
                key = base64.b64decode(key_b64)
                if len(key) == 32:
                    self._encryption_key = key
                    return
            except Exception:
                pass

        # 2. Secrets file
        secrets_path = Path("/run/secrets/encryption_key")
        if secrets_path.exists():
            try:
                key = secrets_path.read_bytes().strip()
                if len(key) == 32:
                    self._encryption_key = key
                    return
            except Exception:
                pass

    @property
    def encryption_key(self) -> bytes | None:
        """Encryption key for database field encryption. None if not configured."""
        return self._encryption_key

    def set_encryption_key(self, key: bytes) -> None:
        """Set encryption key programmatically (for testing)."""
        if len(key) != 32:
            raise ValueError("Encryption key must be 32 bytes")
        self._encryption_key = key

    def _discover_tables(self) -> None:
        """Autodiscover Table classes from entities/ and compose with EE mixins."""
        ce_modules = self._find_entity_modules(_CE_ENTITIES_PACKAGE, "table")
        ee_modules = self._find_entity_modules(_EE_ENTITIES_PACKAGE, "table_ee")

        for entity_name, ce_module in ce_modules.items():
            ce_class = self._get_class_from_module(ce_module, "Table")
            if not ce_class:
                continue

            ee_module = ee_modules.get(entity_name)
            if ee_module:
                ee_mixin = self._get_ee_mixin_from_module(ee_module, "_EE")
                if ee_mixin:
                    composed_class = type(
                        ce_class.__name__, (ee_mixin, ce_class), {"__module__": ce_class.__module__}
                    )
                    self.db.add_table(composed_class)
                    continue

            self.db.add_table(ce_class)

    def _discover_endpoints(self) -> None:
        """Autodiscover Endpoint classes and compose with EE mixins."""
        for endpoint_class in BaseEndpoint.discover():
            table = self.db.table(endpoint_class.name)
            # InstanceEndpoint needs proxy reference, others just need table
            if endpoint_class.name == "instance":
                self.endpoints[endpoint_class.name] = endpoint_class(table, proxy=self)
            else:
                self.endpoints[endpoint_class.name] = endpoint_class(table)

    def endpoint(self, name: str) -> BaseEndpoint:
        """Get endpoint by name."""
        if name not in self.endpoints:
            raise ValueError(f"Endpoint '{name}' not found")
        return self.endpoints[name]

    async def init(self) -> None:
        """Initialize database: connect, create tables, run migrations, detect edition."""
        await self.db.connect()
        await self.db.check_structure()

        logger = logging.getLogger("mail_proxy")

        # Run legacy schema migrations
        accounts = self.db.table("accounts")
        if await accounts.migrate_from_legacy_schema():
            logger.info("Migrated accounts table from legacy schema")

        messages = self.db.table("messages")
        if await messages.migrate_from_legacy_schema():
            logger.info("Migrated messages table from legacy schema")

        # Sync schema for all tables
        await self.db.table("tenants").sync_schema()
        await self.db.table("accounts").sync_schema()
        await self.db.table("messages").sync_schema()
        await self.db.table("message_events").sync_schema()
        await self.db.table("command_log").sync_schema()
        await self.db.table("instance").sync_schema()

        # Populate account_pk for existing messages
        if await messages.migrate_account_pk():
            logger.info("Migrated messages table: populated account_pk")

        # Edition detection and default tenant creation
        await self._init_edition()

    async def _init_edition(self) -> None:
        """Detect CE/EE mode based on existing data and installed modules."""
        import core.mail_proxy

        tenants_table = self.db.table("tenants")
        instance_table = self.db.table("instance")

        tenants = await tenants_table.list_all()
        count = len(tenants)

        if count == 0:
            if core.mail_proxy.HAS_ENTERPRISE:
                await instance_table.set_edition("ee")
            else:
                await tenants_table.ensure_default()
                await instance_table.set_edition("ce")
        elif count > 1 or (count == 1 and tenants[0]["id"] != "default"):
            await instance_table.set_edition("ee")

    async def close(self) -> None:
        """Close database connection."""
        await self.db.close()

    # -------------------------------------------------------------------------
    # Discovery helpers (private)
    # -------------------------------------------------------------------------

    def _find_entity_modules(self, base_package: str, module_name: str) -> dict[str, Any]:
        """Scan package for entity subpackages containing module_name."""
        result: dict[str, Any] = {}
        try:
            package = importlib.import_module(base_package)
        except ImportError:
            return result

        package_path = getattr(package, "__path__", None)
        if not package_path:
            return result

        for _, name, is_pkg in pkgutil.iter_modules(package_path):
            if not is_pkg:
                continue
            full_module_name = f"{base_package}.{name}.{module_name}"
            try:
                module = importlib.import_module(full_module_name)
                result[name] = module
            except ImportError:
                pass
        return result

    def _get_class_from_module(self, module: Any, class_suffix: str) -> type | None:
        """Extract CE Table/Endpoint class by suffix (excludes _EE mixins)."""
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            obj = getattr(module, attr_name)
            if isinstance(obj, type) and attr_name.endswith(class_suffix):
                if "_EE" in attr_name or "Mixin" in attr_name:
                    continue
                if attr_name == "Table":
                    continue
                if not hasattr(obj, "name"):
                    continue
                return obj
        return None

    def _get_ee_mixin_from_module(self, module: Any, class_suffix: str) -> type | None:
        """Extract EE mixin class (suffix _EE) for composition with CE class."""
        for name in dir(module):
            if name.startswith("_"):
                continue
            obj = getattr(module, name)
            if isinstance(obj, type) and name.endswith(class_suffix):
                return obj
        return None

    # -------------------------------------------------------------------------
    # Interface factories (lazy properties)
    # -------------------------------------------------------------------------

    @property
    def api(self) -> FastAPI:
        """FastAPI app with all endpoints, auth middleware, and lifespan.

        Created on first access. Includes default lifespan that calls
        proxy.start() on startup and proxy.stop() on shutdown.

        Usage:
            uvicorn core.mail_proxy.server:app
        """
        if not hasattr(self, "_api") or self._api is None:
            from .interface import create_app

            self._api = create_app(self, api_token=self.config.api_token)
        return self._api

    @property
    def cli(self) -> click.Group:
        """Click CLI group with endpoint commands and service commands.

        Created on first access. Includes:
        - Endpoint commands: tenants, accounts, messages, instance, command_log
        - Service commands: serve, connect, stats, token, run-now

        Usage:
            mail-proxy --help
        """
        if not hasattr(self, "_cli") or self._cli is None:
            self._cli = self._create_cli()
        return self._cli

    def _create_cli(self) -> click.Group:
        """Build Click CLI: endpoint commands + service commands (serve, etc.)."""
        import click

        from .interface import (
            add_connect_command,
            add_run_now_command,
            add_stats_command,
            add_token_command,
            register_cli_endpoint,
        )

        @click.group()
        @click.version_option()
        def cli() -> None:
            """Mail-Proxy: Asynchronous email dispatch service."""
            pass

        # Register endpoint-based commands (tenants, accounts, messages, instance)
        for endpoint in self.endpoints.values():
            register_cli_endpoint(cli, endpoint)

        # Add special commands
        def get_url() -> str:
            return f"http://localhost:{self.config.port}"

        def get_token() -> str | None:
            return self.config.api_token

        add_connect_command(cli, get_url, get_token, self.config.instance_name)
        add_stats_command(cli, self.db)
        add_token_command(cli, self.db)
        add_run_now_command(cli, get_url, get_token)

        # Add serve command
        @cli.command("serve")
        @click.option("--host", default="0.0.0.0", help="Bind host")
        @click.option("--port", "-p", default=self.config.port, help="Bind port")
        @click.option("--reload", is_flag=True, help="Enable auto-reload")
        def serve_cmd(host: str, port: int, reload: bool) -> None:
            """Start the API server."""
            import uvicorn

            uvicorn.run(
                "core.mail_proxy.server:app",
                host=host,
                port=port,
                reload=reload,
            )

        return cli


__all__ = ["MailProxyBase"]
