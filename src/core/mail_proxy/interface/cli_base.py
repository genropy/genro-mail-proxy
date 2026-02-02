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

        mail-proxy accounts list                    # uses context tenant
        mail-proxy accounts list acme               # explicit tenant
        mail-proxy accounts add main --host smtp.example.com

Note:
    - tenant_id is special: optional positional with context fallback
    - Other required params become positional arguments
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


def _format_list_as_table(data: list[dict], console: Any) -> None:
    """Format a list of dicts as a Rich table.

    Automatically selects columns based on the data:
    - id, name, active for tenants
    - id, tenant_id, host, port for accounts
    - id, tenant_id, status, subject for messages
    """
    from rich.table import Table

    if not data:
        console.print("[dim]No records found.[/dim]")
        return

    # Define column priorities for different entity types
    priority_columns = ["id", "tenant_id", "name", "active", "host", "port", "status", "subject"]
    all_keys = set()
    for row in data:
        all_keys.update(row.keys())

    # Select columns: priority columns first, then others (limited)
    columns = [c for c in priority_columns if c in all_keys]
    remaining = [k for k in all_keys if k not in columns]
    columns.extend(remaining[:3])  # Add up to 3 more columns

    table = Table(show_header=True, header_style="bold")
    for col in columns:
        table.add_column(col.replace("_", " ").title())

    for row in data:
        values = []
        for col in columns:
            val = row.get(col)
            if val is None:
                values.append("[dim]-[/dim]")
            elif isinstance(val, bool):
                values.append("[green]✓[/green]" if val else "[dim]✗[/dim]")
            elif col == "active":
                values.append("[green]✓[/green]" if val else "[dim]✗[/dim]")
            elif isinstance(val, dict):
                values.append("[dim]{...}[/dim]")
            elif isinstance(val, list):
                values.append(f"[dim][{len(val)} items][/dim]")
            else:
                str_val = str(val)
                if len(str_val) > 40:
                    str_val = str_val[:37] + "..."
                values.append(str_val)
        table.add_row(*values)

    console.print(table)


def _create_click_command(
    method: Callable, run_async: Callable, endpoint_name: str = ""
) -> click.Command:
    """Create a Click command from an async method.

    Args:
        method: Async method to wrap.
        run_async: Function to run async code (e.g., asyncio.run).
        endpoint_name: Name of the endpoint (for formatting delete messages).

    Returns:
        Click command ready to be added to a group.

    Note:
        tenant_id is treated specially: it becomes an optional positional
        argument with fallback to the current context (via resolve_context).

        Output formatting:
        - delete methods: show success message instead of True/False
        - list methods: show Rich table (use --json for JSON output)
        - other methods: show JSON for dicts/lists, plain text otherwise
    """
    from .cli_commands import require_context, resolve_context

    sig = inspect.signature(method)
    doc = method.__doc__ or f"{method.__name__} operation"
    method_name = method.__name__

    # Determine if this is a list command (needs --json flag)
    is_list_command = method_name in ("list", "list_all")
    is_delete_command = method_name in ("delete", "remove")

    options = []
    arguments = []
    has_tenant_id = False

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        click_type = _annotation_to_click_type(param.annotation)
        has_default = param.default is not inspect.Parameter.empty
        is_bool = param.annotation is bool

        cli_name = param_name.replace("_", "-")

        # Special case: required tenant_id becomes optional positional with context fallback
        # (if tenant_id already has a default, it remains an option as before)
        if param_name == "tenant_id" and not has_default:
            has_tenant_id = True
            arguments.append(
                click.argument("tenant_id", type=click_type, required=False, default=None)
            )
        elif is_bool:
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

    # Add --json flag for list commands
    if is_list_command:
        options.append(
            click.option("--json", "output_json", is_flag=True, help="Output as JSON")
        )

    def cmd_func(**kwargs: Any) -> None:
        from rich.console import Console

        console = Console(stderr=True)
        output_console = Console()  # For table output to stdout
        output_json = kwargs.pop("output_json", False)
        py_kwargs = {k.replace("-", "_"): v for k, v in kwargs.items()}

        # Resolve tenant_id from context if not provided
        if has_tenant_id and not py_kwargs.get("tenant_id"):
            _, tenant = require_context(require_tenant=True)
            py_kwargs["tenant_id"] = tenant

        # Print context prefix (virtualenv-style)
        instance, tenant = resolve_context()
        if instance:
            if tenant:
                console.print(f"[dim]({instance}/{tenant})[/dim]")
            else:
                console.print(f"[dim]({instance})[/dim]")

        result = run_async(method(**py_kwargs))

        # Format output based on method type
        if is_delete_command:
            # Show success message instead of True/False
            if result is True or result is None:
                # Try to get the ID from kwargs
                deleted_id = py_kwargs.get("id") or py_kwargs.get("account_id") or py_kwargs.get("message_id") or py_kwargs.get("tenant_id")
                if deleted_id:
                    output_console.print(f"[green]✓[/green] {endpoint_name.rstrip('s').title()} '{deleted_id}' deleted")
                else:
                    output_console.print(f"[green]✓[/green] Deleted successfully")
            elif result is False:
                output_console.print("[red]✗[/red] Delete failed")
            else:
                click.echo(result)
        elif is_list_command and isinstance(result, list) and not output_json:
            # Show table for list commands (unless --json)
            _format_list_as_table(result, output_console)
        elif result is not None:
            if isinstance(result, (dict, list)):
                click.echo(json.dumps(result, indent=2, default=str))
            else:
                click.echo(result)

    cmd: click.Command = click.command(help=doc)(cmd_func)
    for opt in reversed(options):
        cmd = opt(cmd)
    for arg in reversed(arguments):
        cmd = arg(cmd)

    return cmd


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

        cmd = _create_click_command(method, run_async, endpoint_name=name)
        cmd.name = method_name.replace("_", "-")
        endpoint_group.add_command(cmd)

    return endpoint_group


__all__ = ["register_endpoint"]
