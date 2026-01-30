# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""CLI base: generates Click commands from endpoint classes via introspection.

Usage:
    import click
    from core.mail_proxy.interface import register_cli_endpoint
    from core.mail_proxy.entities.account import AccountEndpoint

    @click.group()
    def cli():
        pass

    endpoint = AccountEndpoint(table)
    register_endpoint(cli, endpoint)
    # Creates: cli accounts add, cli accounts get, cli accounts list, cli accounts delete
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable, Literal, get_args, get_origin

import click


def _annotation_to_click_type(annotation: Any) -> type | click.Choice:
    """Convert Python annotation to Click type."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return str

    # Handle Optional (Union with None)
    origin = get_origin(annotation)
    if origin is type(None):
        return str

    # Unwrap Optional[X] → X
    args = get_args(annotation)
    if origin is type(int | str):  # UnionType
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            annotation = non_none[0]

    # Handle Literal["a", "b"] → click.Choice
    if get_origin(annotation) is Literal:
        choices = get_args(annotation)
        return click.Choice(choices)

    # Basic types
    if annotation is int:
        return int
    if annotation is bool:
        return bool
    if annotation is float:
        return float

    return str


def _create_click_command(method: Callable, run_async: Callable) -> click.Command:
    """Create a Click command from an async method."""
    sig = inspect.signature(method)
    doc = method.__doc__ or f"{method.__name__} operation"

    # Collect parameters
    options = []
    arguments = []

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        click_type = _annotation_to_click_type(param.annotation)
        has_default = param.default is not inspect.Parameter.empty
        is_bool = param.annotation is bool

        # Convert param_name to CLI-friendly format
        cli_name = param_name.replace("_", "-")

        if is_bool:
            # Boolean → flag
            options.append(
                click.option(
                    f"--{cli_name}/--no-{cli_name}",
                    default=param.default if has_default else False,
                    help=f"Enable/disable {param_name}",
                )
            )
        elif has_default:
            # Has default → option
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
            # Required → argument
            arguments.append(
                click.argument(param_name, type=click_type)
            )

    # Create command function
    def cmd_func(**kwargs: Any) -> None:
        # Convert CLI names back to Python names
        py_kwargs = {k.replace("-", "_"): v for k, v in kwargs.items()}
        result = run_async(method(**py_kwargs))
        if result is not None:
            if isinstance(result, (dict, list)):
                click.echo(json.dumps(result, indent=2, default=str))
            else:
                click.echo(result)

    # Apply decorators (in reverse order)
    cmd_func = click.command(help=doc)(cmd_func)
    for opt in reversed(options):
        cmd_func = opt(cmd_func)
    for arg in reversed(arguments):
        cmd_func = arg(cmd_func)

    return cmd_func


def register_endpoint(
    group: click.Group,
    endpoint: Any,
    run_async: Callable | None = None
) -> click.Group:
    """Register all methods of an endpoint as Click commands.

    Args:
        group: Click group to add commands to.
        endpoint: Endpoint instance with async methods.
        run_async: Function to run async code (default: asyncio.run).

    Returns:
        A new Click group with all endpoint commands.
    """
    if run_async is None:
        run_async = asyncio.run

    name = getattr(endpoint, "name", endpoint.__class__.__name__.lower())

    # Create subgroup for this endpoint
    @group.group(name=name)
    def endpoint_group() -> None:
        """Endpoint commands."""
        pass

    # Update docstring
    endpoint_group.__doc__ = f"Manage {name}."

    # Find all public async methods
    for method_name in dir(endpoint):
        if method_name.startswith("_"):
            continue

        method = getattr(endpoint, method_name)
        if not callable(method) or not inspect.iscoroutinefunction(method):
            continue

        # Create and add command
        cmd = _create_click_command(method, run_async)
        cmd.name = method_name.replace("_", "-")  # add_pec → add-pec
        endpoint_group.add_command(cmd)

    return endpoint_group


__all__ = ["register_endpoint"]
