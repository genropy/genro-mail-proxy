# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Click command generation from endpoint classes via introspection.

This module generates CLI commands automatically from endpoint classes
by introspecting method signatures and creating Click commands.

Components:
    register_endpoint: Register endpoint methods as Click commands.

Example:
    Register endpoint commands::

        import click
        from core.mail_proxy.interface import register_cli_endpoint
        from core.mail_proxy.entities.account import AccountEndpoint

        @click.group()
        def cli():
            pass

        endpoint = AccountEndpoint(table)
        register_cli_endpoint(cli, endpoint)
        # Creates: cli accounts add, cli accounts get, cli accounts list

    Generated commands::

        mail-proxy myinstance accounts list --active-only
        mail-proxy myinstance accounts add main --host smtp.example.com
        mail-proxy myinstance messages list --tenant-id acme

Note:
    - Required params become positional arguments
    - Optional params become --options
    - Boolean params become --flag/--no-flag toggles
    - Method underscores become dashes (add_batch → add-batch)
"""

from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Callable
from typing import Any, Literal, get_args, get_origin

import click


def _annotation_to_click_type(annotation: Any) -> type | click.Choice:
    """Convert Python type annotation to Click type.

    Args:
        annotation: Python type annotation.

    Returns:
        Click-compatible type (int, str, bool, float, or click.Choice).
    """
    if annotation is inspect.Parameter.empty or annotation is Any:
        return str

    origin = get_origin(annotation)
    if origin is type(None):
        return str

    args = get_args(annotation)
    if origin is type(int | str):  # UnionType
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            annotation = non_none[0]

    if get_origin(annotation) is Literal:
        choices = get_args(annotation)
        return click.Choice(choices)

    if annotation is int:
        return int
    if annotation is bool:
        return bool
    if annotation is float:
        return float

    return str


def _create_click_command(method: Callable, run_async: Callable) -> click.Command:
    """Create a Click command from an async method.

    Args:
        method: Async method to wrap.
        run_async: Function to run async code (e.g., asyncio.run).

    Returns:
        Click command ready to be added to a group.
    """
    sig = inspect.signature(method)
    doc = method.__doc__ or f"{method.__name__} operation"

    options = []
    arguments = []

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        click_type = _annotation_to_click_type(param.annotation)
        has_default = param.default is not inspect.Parameter.empty
        is_bool = param.annotation is bool

        cli_name = param_name.replace("_", "-")

        if is_bool:
            options.append(
                click.option(
                    f"--{cli_name}/--no-{cli_name}",
                    default=param.default if has_default else False,
                    help=f"Enable/disable {param_name}",
                )
            )
        elif has_default:
            options.append(
                click.option(
                    f"--{cli_name}",
                    type=click_type,
                    default=param.default,
                    show_default=True,
                    help=f"{param_name} parameter",
                )
            )
        else:
            arguments.append(click.argument(param_name, type=click_type))

    def cmd_func(**kwargs: Any) -> None:
        py_kwargs = {k.replace("-", "_"): v for k, v in kwargs.items()}
        result = run_async(method(**py_kwargs))
        if result is not None:
            if isinstance(result, (dict, list)):
                click.echo(json.dumps(result, indent=2, default=str))
            else:
                click.echo(result)

    cmd_func = click.command(help=doc)(cmd_func)
    for opt in reversed(options):
        cmd_func = opt(cmd_func)
    for arg in reversed(arguments):
        cmd_func = arg(cmd_func)

    return cmd_func


def register_endpoint(
    group: click.Group, endpoint: Any, run_async: Callable | None = None
) -> click.Group:
    """Register all methods of an endpoint as Click commands.

    Creates a subgroup named after the endpoint and adds commands
    for each public async method.

    Args:
        group: Click group to add commands to.
        endpoint: Endpoint instance with async methods.
        run_async: Function to run async code. Defaults to asyncio.run.

    Returns:
        The created Click subgroup with all endpoint commands.

    Example:
        ::

            @click.group()
            def cli():
                pass

            endpoint = AccountEndpoint(db.table("accounts"))
            register_endpoint(cli, endpoint)

            # Now available:
            # cli accounts list
            # cli accounts add <id> --host <host> --port <port>
            # cli accounts delete <id>
    """
    if run_async is None:
        run_async = asyncio.run

    name = getattr(endpoint, "name", endpoint.__class__.__name__.lower())

    @group.group(name=name)
    def endpoint_group() -> None:
        """Endpoint commands."""
        pass

    endpoint_group.__doc__ = f"Manage {name}."

    for method_name in dir(endpoint):
        if method_name.startswith("_"):
            continue

        method = getattr(endpoint, method_name)
        if not callable(method) or not inspect.iscoroutinefunction(method):
            continue

        cmd = _create_click_command(method, run_async)
        cmd.name = method_name.replace("_", "-")
        endpoint_group.add_command(cmd)

    return endpoint_group


__all__ = ["register_endpoint"]
