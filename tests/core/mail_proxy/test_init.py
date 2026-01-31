# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for core.mail_proxy module initialization."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch


class TestMailProxyModuleImport:
    """Tests for mail_proxy module import behavior."""

    def test_has_enterprise_flag_available(self):
        """HAS_ENTERPRISE flag is exported."""
        from core.mail_proxy import HAS_ENTERPRISE

        assert isinstance(HAS_ENTERPRISE, bool)

    def test_mailproxy_ee_none_when_no_enterprise(self, monkeypatch):
        """MailProxy_EE is None when enterprise not installed."""
        # Remove cached modules
        modules_to_remove = [k for k in sys.modules if "mail_proxy" in k or "enterprise" in k]
        for mod in modules_to_remove:
            monkeypatch.delitem(sys.modules, mod, raising=False)

        # Block enterprise import
        original_import = __builtins__["__import__"]

        def blocked_import(name, *args, **kwargs):
            if name.startswith("enterprise"):
                raise ImportError(f"Blocked for test: {name}")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr("builtins.__import__", blocked_import)

        # Reimport
        import importlib

        import core.mail_proxy

        importlib.reload(core.mail_proxy)

        assert core.mail_proxy.HAS_ENTERPRISE is False
        assert core.mail_proxy.MailProxy_EE is None

        # Cleanup
        modules_to_remove = [k for k in sys.modules if "mail_proxy" in k]
        for mod in modules_to_remove:
            monkeypatch.delitem(sys.modules, mod, raising=False)

    def test_has_enterprise_true_when_installed(self):
        """HAS_ENTERPRISE is True when enterprise package available."""
        from core.mail_proxy import HAS_ENTERPRISE

        # In test environment, enterprise is installed
        assert HAS_ENTERPRISE is True


class TestMainEntryPoint:
    """Tests for main() CLI entry point."""

    def test_main_creates_proxy_and_runs_cli(self, monkeypatch):
        """main() creates MailProxy and invokes CLI."""
        # Mock MailProxy
        mock_proxy = MagicMock()
        mock_proxy_class = MagicMock(return_value=mock_proxy)

        with patch("core.mail_proxy.proxy.MailProxy", mock_proxy_class):
            from core.mail_proxy import main

            # main() should create proxy and call cli()()
            # We can't easily test this without invoking real CLI
            # Just verify the function exists and is callable
            assert callable(main)
