# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""REPL protection utilities.

Provides decorators and wrappers to protect sensitive methods/attributes
from being accessed in interactive REPL sessions.

Usage:
    from tools.repl import reserved, repl_wrap

    class MyService:
        @reserved
        def get_secret_key(self):
            return self._secret

        def public_method(self):
            return "hello"

    # In REPL setup:
    service = MyService()
    namespace = {"service": repl_wrap(service)}

    # Now in REPL:
    >>> service.public_method()  # Works
    'hello'
    >>> service.get_secret_key()  # Blocked
    AttributeError: 'get_secret_key' is reserved and not accessible in REPL
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

# Marker attribute name
RESERVED_ATTR = "_reserved"


def reserved(func: Callable[..., T]) -> Callable[..., T]:
    """Mark a method as reserved (not accessible from REPL).

    Usage:
        class MyClass:
            @reserved
            def sensitive_method(self):
                ...
    """
    setattr(func, RESERVED_ATTR, True)
    return func


def is_reserved(obj: Any) -> bool:
    """Check if an object (method/function) is marked as reserved."""
    return getattr(obj, RESERVED_ATTR, False)


class REPLWrapper:
    """Wrapper that blocks access to @reserved methods/attributes.

    This wrapper intercepts attribute access and raises AttributeError
    for any method marked with @reserved decorator.
    """

    def __init__(self, wrapped: Any):
        # Use object.__setattr__ to avoid triggering our __setattr__
        object.__setattr__(self, "_wrapped", wrapped)

    def __getattr__(self, name: str) -> Any:
        wrapped = object.__getattribute__(self, "_wrapped")
        attr = getattr(wrapped, name)

        # Check if it's a reserved method
        if callable(attr) and is_reserved(attr):
            raise AttributeError(f"'{name}' is reserved and not accessible in REPL")

        # If the attribute is an object with its own methods, wrap it too
        # (for nested access like proxy.db.get_secret())
        if hasattr(attr, "__dict__") and not callable(attr):
            return REPLWrapper(attr)

        return attr

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_wrapped":
            object.__setattr__(self, name, value)
        else:
            wrapped = object.__getattribute__(self, "_wrapped")
            setattr(wrapped, name, value)

    def __dir__(self) -> list[str]:
        """Return directory listing, excluding reserved methods."""
        wrapped = object.__getattribute__(self, "_wrapped")
        result = []
        for name in dir(wrapped):
            try:
                attr = getattr(wrapped, name)
                if not (callable(attr) and is_reserved(attr)):
                    result.append(name)
            except AttributeError:
                result.append(name)
        return result

    def __repr__(self) -> str:
        wrapped = object.__getattribute__(self, "_wrapped")
        return repr(wrapped)

    def __str__(self) -> str:
        wrapped = object.__getattribute__(self, "_wrapped")
        return str(wrapped)


def repl_wrap(obj: T) -> T:
    """Wrap an object for safe REPL access.

    Returns a wrapper that blocks access to @reserved methods.
    The wrapper is transparent for all other operations.

    Args:
        obj: The object to wrap.

    Returns:
        A REPLWrapper that behaves like the original object
        but blocks @reserved methods.

    Usage:
        # In REPL setup code:
        namespace = {
            "proxy": repl_wrap(proxy),
            "db": repl_wrap(db),
        }
        code.interact(local=namespace)
    """
    return REPLWrapper(obj)  # type: ignore[return-value]


__all__ = ["RESERVED_ATTR", "REPLWrapper", "is_reserved", "repl_wrap", "reserved"]
