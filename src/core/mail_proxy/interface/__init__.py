# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Interface layer for API, CLI, and interactive forms.

This package provides infrastructure for exposing MailProxy functionality
through multiple interfaces using introspection-based route/command generation.

Components:
    BaseEndpoint: Base class for all endpoint definitions.
    EndpointDispatcher: Routes commands to endpoint methods.
    create_app: FastAPI application factory.
    register_api_endpoint: Register endpoint as FastAPI routes.
    register_cli_endpoint: Register endpoint as Click commands.
    DynamicForm: Interactive terminal forms with validation.

Example:
    Create a FastAPI application::

        from core.mail_proxy.interface import create_app
        from core.mail_proxy.proxy import MailProxy

        proxy = MailProxy(db_path="/data/mail.db")
        app = create_app(proxy, api_token="secret")

    Register CLI commands::

        import click
        from core.mail_proxy.interface import register_cli_endpoint

        @click.group()
        def cli():
            pass

        endpoint = AccountEndpoint(table)
        register_cli_endpoint(cli, endpoint)

    Use interactive forms in REPL::

        from core.mail_proxy.interface import create_form, set_proxy
        set_proxy(proxy, dispatcher)
        data = new_tenant()  # Interactive tenant creation

Note:
    All interfaces are generated dynamically from endpoint class
    method signatures via introspection. No hardcoded routes or
    commands required.
"""

from .api_base import create_app
from .api_base import register_endpoint as register_api_endpoint
from .cli_base import register_endpoint as register_cli_endpoint
from .cli_commands import (
    add_connect_command,
    add_run_now_command,
    add_send_command,
    add_stats_command,
    add_token_command,
)
from .endpoint_base import BaseEndpoint, EndpointDispatcher
from .forms import (
    DynamicForm,
    create_form,
    new_account,
    new_message,
    new_tenant,
    set_proxy,
)

__all__ = [
    "BaseEndpoint",
    "EndpointDispatcher",
    "create_app",
    "register_api_endpoint",
    "register_cli_endpoint",
    "add_connect_command",
    "add_run_now_command",
    "add_send_command",
    "add_stats_command",
    "add_token_command",
    "DynamicForm",
    "create_form",
    "set_proxy",
    "new_tenant",
    "new_account",
    "new_message",
]
