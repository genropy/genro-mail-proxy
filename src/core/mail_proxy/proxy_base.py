# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Base class for MailProxy with database, tables, and endpoints.

Provides the foundation for MailProxy:
- Configuration via ProxyConfig
- Database with autodiscovered tables
- Endpoint registry with autodiscovered endpoints
- Database initialization and migrations

This class can be used directly for testing without the full MailProxy
runtime (SMTP pool, background loops, etc.).

Example (testing):
    proxy = MailProxyBase(db_path=":memory:")
    await proxy.init()
    await proxy.db.table("tenants").add({"id": "t1", "name": "Test"})
"""

from __future__ import annotations

import importlib
import logging
import pkgutil
from typing import Any

from sql import SqlDb

from .interface import BaseEndpoint
from .proxy_config import ProxyConfig

# Packages to scan for entities
_CE_ENTITIES_PACKAGE = "core.mail_proxy.entities"
_EE_ENTITIES_PACKAGE = "enterprise.mail_proxy.entities"


class MailProxyBase:
    """Base class with database, tables, and endpoints.

    Provides:
    - config: ProxyConfig instance
    - db: SqlDb with autodiscovered tables
    - endpoints: dict of endpoint instances by name

    Subclassed by MailProxy which adds SMTP, loops, rate limiting, etc.
    """

    def __init__(
        self,
        *,
        config: ProxyConfig | None = None,
        db_path: str | None = None,
    ):
        """Initialize base proxy with config and database.

        Args:
            config: ProxyConfig instance. If None, creates default.
            db_path: Database path override. If provided, overrides config.db_path.
        """
        self.config = config or ProxyConfig()
        _db_path = db_path if db_path is not None else self.config.db_path

        self.db = SqlDb(_db_path or ":memory:")
        self._discover_tables()

        self.endpoints: dict[str, BaseEndpoint] = {}
        self._discover_endpoints()

    def _discover_tables(self) -> None:
        """Autodiscover and register table classes from entities/ directories."""
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
                        ce_class.__name__,
                        (ee_mixin, ce_class),
                        {"__module__": ce_class.__module__}
                    )
                    self.db.add_table(composed_class)
                    continue

            self.db.add_table(ce_class)

    def _discover_endpoints(self) -> None:
        """Autodiscover and instantiate endpoint classes from entities/ directories."""
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
        """Initialize database: connect, create schema, run migrations."""
        await self.db.connect()
        await self.db.check_structure()

        logger = logging.getLogger("mail_proxy")

        # Run legacy schema migrations
        accounts = self.db.table('accounts')
        if await accounts.migrate_from_legacy_schema():
            logger.info("Migrated accounts table from legacy schema")

        messages = self.db.table('messages')
        if await messages.migrate_from_legacy_schema():
            logger.info("Migrated messages table from legacy schema")

        # Sync schema for all tables
        await self.db.table('tenants').sync_schema()
        await self.db.table('accounts').sync_schema()
        await self.db.table('messages').sync_schema()
        await self.db.table('message_events').sync_schema()
        await self.db.table('command_log').sync_schema()
        await self.db.table('instance').sync_schema()

        # Populate account_pk for existing messages
        if await messages.migrate_account_pk():
            logger.info("Migrated messages table: populated account_pk")

        # Edition detection and default tenant creation
        await self._init_edition()

    async def _init_edition(self) -> None:
        """Initialize edition based on existing data and installed modules."""
        from . import HAS_ENTERPRISE

        tenants_table = self.db.table('tenants')
        instance_table = self.db.table('instance')

        tenants = await tenants_table.list_all()
        count = len(tenants)

        if count == 0:
            if HAS_ENTERPRISE:
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
    # Discovery helpers
    # -------------------------------------------------------------------------

    def _find_entity_modules(self, base_package: str, module_name: str) -> dict[str, Any]:
        """Find entity modules in a package."""
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
        """Extract a class from module by suffix pattern."""
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
        """Extract an EE mixin class from module."""
        for name in dir(module):
            if name.startswith("_"):
                continue
            obj = getattr(module, name)
            if isinstance(obj, type) and name.endswith(class_suffix):
                return obj
        return None


__all__ = ["MailProxyBase"]
