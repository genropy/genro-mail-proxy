# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Base class for endpoint introspection and command dispatch.

Provides common functionality for all endpoints:
- Method introspection for API/CLI generation
- HTTP method inference from method names
- Pydantic model generation from signatures
- Command dispatch to appropriate endpoint methods
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import TYPE_CHECKING, Any, Callable, get_origin, get_type_hints

from pydantic import create_model

if TYPE_CHECKING:
    from sql import SqlDb

# Packages to scan for entity endpoints
_CE_ENTITIES_PACKAGE = "core.mail_proxy.entities"
_EE_ENTITIES_PACKAGE = "enterprise.mail_proxy.entities"


def POST(method: Callable) -> Callable:
    """Decorator to mark an endpoint method as POST (uses JSON body)."""
    method._http_post = True  # type: ignore[attr-defined]
    return method


class BaseEndpoint:
    """Base class for all endpoints. Provides introspection capabilities."""

    name: str = ""

    def __init__(self, table: Any):
        self.table = table

    def get_methods(self) -> list[tuple[str, Callable]]:
        """Return all public async methods for API/CLI generation."""
        methods = []
        for method_name in dir(self):
            if method_name.startswith("_"):
                continue
            method = getattr(self, method_name)
            if callable(method) and inspect.iscoroutinefunction(method):
                methods.append((method_name, method))
        return methods

    def get_http_method(self, method_name: str) -> str:
        """Determine HTTP method: POST if decorated with @POST, else GET."""
        method = getattr(self, method_name)
        if getattr(method, "_http_post", False):
            return "POST"
        return "GET"

    def create_request_model(self, method_name: str) -> type:
        """Create Pydantic model from method signature for request body."""
        method = getattr(self, method_name)
        sig = inspect.signature(method)

        # Resolve string annotations to actual types
        try:
            hints = get_type_hints(method)
        except Exception:
            hints = {}

        fields = {}
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue

            # Prefer resolved hint, fallback to signature annotation
            annotation = hints.get(param_name, param.annotation)
            if annotation is inspect.Parameter.empty:
                annotation = Any

            fields[param_name] = self._annotation_to_field(annotation, param.default)

        model_name = f"{method_name.title().replace('_', '')}Request"
        return create_model(model_name, **fields)

    def is_simple_params(self, method_name: str) -> bool:
        """Check if method has only simple params (suitable for query string).

        Returns False if any parameter is a list or dict (including Optional[list]).
        """
        method = getattr(self, method_name)

        # Resolve string annotations to actual types
        try:
            hints = get_type_hints(method)
        except Exception:
            hints = {}

        sig = inspect.signature(method)
        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            # Prefer resolved hint, fallback to raw annotation
            ann = hints.get(param_name, param.annotation)
            if self._is_complex_type(ann):
                return False
        return True

    def _is_complex_type(self, ann: Any) -> bool:
        """Check if annotation is a complex type (list, dict, or contains them)."""
        import types
        from typing import Union, get_args

        if ann in (list, dict):
            return True

        origin = get_origin(ann)
        if origin in (list, dict):
            return True

        # Check Union types: both typing.Union and Python 3.10+ X | Y syntax
        if origin is Union or isinstance(origin, type) and origin is types.UnionType:
            for arg in get_args(ann):
                if arg is type(None):
                    continue
                if self._is_complex_type(arg):
                    return True

        # Also check for types.UnionType directly (Python 3.10+ X | Y)
        if type(ann).__name__ == "UnionType":
            for arg in get_args(ann):
                if arg is type(None):
                    continue
                if self._is_complex_type(arg):
                    return True

        return False

    def count_params(self, method_name: str) -> int:
        """Count non-self parameters."""
        method = getattr(self, method_name)
        sig = inspect.signature(method)
        return sum(1 for p in sig.parameters if p != "self")

    def _annotation_to_field(self, annotation: Any, default: Any) -> tuple[Any, Any]:
        """Convert Python annotation to Pydantic field tuple (type, default)."""
        if default is inspect.Parameter.empty:
            return (annotation, ...)  # Required field
        return (annotation, default)

    @classmethod
    def discover(cls) -> list[type["BaseEndpoint"]]:
        """Autodiscover all endpoint classes from entities/ directories.

        Scans CE and EE entities packages for endpoint.py and endpoint_ee.py modules.
        When both exist for an entity, composes them (EE mixin first for override).

        Returns:
            List of endpoint classes ready for instantiation.
        """
        ce_modules = cls._find_entity_modules(_CE_ENTITIES_PACKAGE, "endpoint")
        ee_modules = cls._find_entity_modules(_EE_ENTITIES_PACKAGE, "endpoint_ee")

        endpoints: list[type[BaseEndpoint]] = []
        for entity_name, ce_module in ce_modules.items():
            ce_class = cls._get_class_from_module(ce_module, "Endpoint")
            if not ce_class:
                continue

            # Check for EE mixin
            ee_module = ee_modules.get(entity_name)
            if ee_module:
                ee_mixin = cls._get_ee_mixin_from_module(ee_module, "_EE")
                if ee_mixin:
                    # Compose: EE mixin first for method override
                    composed_class = type(
                        ce_class.__name__,
                        (ee_mixin, ce_class),
                        {"__module__": ce_class.__module__}
                    )
                    endpoints.append(composed_class)
                    continue

            # No EE mixin, use CE class as-is
            endpoints.append(ce_class)

        return endpoints

    @classmethod
    def _find_entity_modules(cls, base_package: str, module_name: str) -> dict[str, Any]:
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

    @classmethod
    def _get_class_from_module(cls, module: Any, class_suffix: str) -> type | None:
        """Extract a class from module by suffix pattern."""
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            obj = getattr(module, attr_name)
            if isinstance(obj, type) and attr_name.endswith(class_suffix):
                if "_EE" in attr_name or "Mixin" in attr_name:
                    continue
                if attr_name in ("BaseEndpoint", "Endpoint"):
                    continue
                if not hasattr(obj, "name"):
                    continue
                return obj
        return None

    @classmethod
    def _get_ee_mixin_from_module(cls, module: Any, class_suffix: str) -> type | None:
        """Extract an EE mixin class from module."""
        for name in dir(module):
            if name.startswith("_"):
                continue
            obj = getattr(module, name)
            if isinstance(obj, type) and name.endswith(class_suffix):
                return obj
        return None


class EndpointDispatcher:
    """Dispatches commands to appropriate endpoint methods.

    Centralizes command routing, replacing the large match/case block in proxy.py.
    Commands are mapped to endpoint.method pairs via a registration system.

    Example:
        dispatcher = EndpointDispatcher(db)
        result = await dispatcher.dispatch("addMessages", {"messages": [...]})
    """

    # Command name → (endpoint_name, method_name)
    # This maps legacy camelCase commands to endpoint methods
    COMMAND_MAP: dict[str, tuple[str, str]] = {
        # Messages
        "addMessages": ("messages", "add_batch"),
        "deleteMessages": ("messages", "delete_batch"),
        "listMessages": ("messages", "list"),
        "cleanupMessages": ("messages", "cleanup"),
        # Accounts
        "addAccount": ("accounts", "add"),
        "listAccounts": ("accounts", "list"),
        "deleteAccount": ("accounts", "delete"),
        # Tenants
        "addTenant": ("tenants", "add"),
        "getTenant": ("tenants", "get"),
        "listTenants": ("tenants", "list"),
        "updateTenant": ("tenants", "update"),
        "deleteTenant": ("tenants", "delete"),
        "suspend": ("tenants", "suspend_batch"),
        "activate": ("tenants", "activate_batch"),
        # Instance
        "getInstance": ("instance", "get"),
        "updateInstance": ("instance", "update"),
        "listTenantsSyncStatus": ("instance", "get_sync_status"),
    }

    def __init__(self, db: "SqlDb", proxy: Any = None):
        """Initialize dispatcher with database and optional proxy reference.

        Args:
            db: MailProxyDb instance for accessing tables.
            proxy: Optional MailProxy instance for operations needing runtime state.
        """
        self.db = db
        self.proxy = proxy
        self._endpoints: dict[str, BaseEndpoint] = {}

    def _get_endpoint(self, endpoint_name: str) -> BaseEndpoint:
        """Get or create endpoint instance by name."""
        if endpoint_name not in self._endpoints:
            self._endpoints[endpoint_name] = self._create_endpoint(endpoint_name)
        return self._endpoints[endpoint_name]

    def _create_endpoint(self, endpoint_name: str) -> BaseEndpoint:
        """Create endpoint instance for the given name."""
        from ..entities.account import AccountEndpoint
        from ..entities.instance import InstanceEndpoint
        from ..entities.message import MessageEndpoint
        from ..entities.tenant import TenantEndpoint

        table = self.db.table(endpoint_name)

        match endpoint_name:
            case "messages":
                return MessageEndpoint(table)
            case "accounts":
                return AccountEndpoint(table)
            case "tenants":
                return TenantEndpoint(table)
            case "instance":
                return InstanceEndpoint(table, proxy=self.proxy)
            case _:
                raise ValueError(f"Unknown endpoint: {endpoint_name}")

    # Result wrapping rules for legacy API compatibility
    # Maps command name to the key to use when wrapping non-dict results
    _RESULT_WRAP_KEYS: dict[str, str] = {
        "listTenants": "tenants",
        "listAccounts": "accounts",
        "listMessages": "messages",
    }

    async def dispatch(self, cmd: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a command to the appropriate endpoint method.

        Args:
            cmd: Command name (e.g., "addMessages", "listTenants").
            payload: Command parameters as dict.

        Returns:
            Result dict in legacy format {"ok": True/False, ...}.
        """
        if cmd not in self.COMMAND_MAP:
            return {"ok": False, "error": f"unknown command: {cmd}"}

        # Pre-dispatch validation for commands requiring specific fields
        validation_error = self._validate_payload(cmd, payload)
        if validation_error:
            return {"ok": False, "error": validation_error}

        endpoint_name, method_name = self.COMMAND_MAP[cmd]
        endpoint = self._get_endpoint(endpoint_name)
        method = getattr(endpoint, method_name)
        mapped_payload = self._map_payload(cmd, payload)

        try:
            result = await method(**mapped_payload)
            return self._wrap_result(cmd, result)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"Internal error: {e}"}

    def _validate_payload(self, cmd: str, payload: dict[str, Any]) -> str | None:
        """Validate payload before dispatch. Returns error message or None."""
        if cmd == "updateTenant":
            if "id" not in payload:
                return "tenant id required"
        return None

    def _wrap_result(self, cmd: str, result: Any) -> dict[str, Any]:
        """Wrap endpoint result in legacy API format.

        Handles conversion from endpoint return types to {"ok": True, ...} format.
        """
        # List results → wrap with appropriate key
        if isinstance(result, list):
            key = self._RESULT_WRAP_KEYS.get(cmd, "items")
            return {"ok": True, key: result}

        # Boolean results (e.g., delete operations)
        if isinstance(result, bool):
            if result:
                return {"ok": True}
            return {"ok": False, "error": "not found"}

        # None results
        if result is None:
            return {"ok": False, "error": "not found"}

        # Dict results - ensure "ok" is present
        if isinstance(result, dict):
            if "ok" not in result:
                result["ok"] = True
            return result

        # Other types - wrap as value
        return {"ok": True, "value": result}

    def _map_payload(self, cmd: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Map legacy payload keys to endpoint method parameters.

        Handles differences between old API (camelCase, "id") and new endpoints.
        """
        result = dict(payload)

        # Rename "id" to specific field names based on command
        if cmd in ("getTenant", "deleteTenant", "updateTenant"):
            if "id" in result:
                result["tenant_id"] = result.pop("id")
        elif cmd == "deleteAccount":
            if "id" in result:
                result["account_id"] = result.pop("id")

        # Handle listMessages active_only default
        if cmd == "listMessages":
            result.setdefault("active_only", False)
            result.setdefault("include_history", False)

        return result

    def get_endpoint(self, name: str) -> BaseEndpoint:
        """Get endpoint by name for direct access."""
        return self._get_endpoint(name)


__all__ = ["BaseEndpoint", "EndpointDispatcher", "POST"]
