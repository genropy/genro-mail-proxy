# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for storage module initialization and EE composition."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock


class TestStorageModuleImport:
    """Tests for storage module import behavior."""

    def test_storage_manager_exported(self):
        """StorageManager is exported from storage module."""
        from storage import StorageManager

        assert StorageManager is not None

    def test_storage_node_exported(self):
        """StorageNode is exported from storage module."""
        from storage import StorageNode

        assert StorageNode is not None

    def test_storage_node_has_ee_mixin_when_available(self):
        """StorageNode includes EE mixin when enterprise package is installed."""
        from storage import StorageNode

        # Check if EE is available
        try:
            from enterprise.mail_proxy.storage.node_ee import StorageNode_EE

            # If EE is available, StorageNode should have EE in its bases
            assert StorageNode_EE in StorageNode.__mro__
        except ImportError:
            # EE not available, StorageNode is just CE
            pass


class TestStorageNodeWithoutEE:
    """Tests for StorageNode when EE is not available."""

    def test_storage_node_fallback_without_ee(self, monkeypatch):
        """StorageNode falls back to CE-only when EE not available."""
        # Remove the storage module from cache to force reimport
        modules_to_remove = [k for k in sys.modules if k.startswith("storage")]
        for mod in modules_to_remove:
            monkeypatch.delitem(sys.modules, mod, raising=False)

        # Also remove enterprise modules if cached
        ee_modules = [k for k in sys.modules if k.startswith("enterprise")]
        for mod in ee_modules:
            monkeypatch.delitem(sys.modules, mod, raising=False)

        # Block the enterprise import
        original_import = __builtins__["__import__"]

        def blocked_import(name, *args, **kwargs):
            if name.startswith("enterprise"):
                raise ImportError(f"Blocked for test: {name}")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", blocked_import)

        # Now import storage - should fall back to CE only
        import importlib

        import storage.node

        importlib.reload(storage.node)

        # Re-import the module
        import storage

        importlib.reload(storage)

        # StorageNode should still be usable
        assert storage.StorageNode is not None

        # Restore modules for other tests
        modules_to_remove = [k for k in sys.modules if k.startswith("storage")]
        for mod in modules_to_remove:
            monkeypatch.delitem(sys.modules, mod, raising=False)
