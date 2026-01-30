# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Interface layer: API, CLI, REPL and endpoint base classes.

This module provides the infrastructure for exposing MailProxy functionality
through different interfaces (REST API, CLI, REPL) using introspection-based
route/command generation from endpoint classes.
"""

from .api_base import create_app, register_endpoint as register_api_endpoint
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
