# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Command-line interface for genro-mail-proxy.

This module provides a CLI for managing tenants, accounts, and messages
directly from the command line without going through the HTTP API.

Usage:
    # Instance-level commands
    mail-proxy <instance> serve start
    mail-proxy <instance> serve stop
    mail-proxy <instance> tenants list
    mail-proxy <instance> tenants add acme-corp --name "ACME Corporation"
    mail-proxy <instance> stats

    # Tenant-level commands
    mail-proxy <instance> <tenant> accounts list
    mail-proxy <instance> <tenant> accounts add main-smtp --host smtp.example.com --port 587
    mail-proxy <instance> <tenant> messages list
    mail-proxy <instance> <tenant> send email.eml

Example:
    $ mail-proxy myserver tenants add mycompany --name "My Company" \\
        --sync-url "https://api.mycompany.com/mail/sync" \\
        --attachment-url "https://api.mycompany.com/attachments" \\
        --auth-method bearer --auth-token secret123

    $ mail-proxy myserver mycompany accounts add primary \\
        --host smtp.mycompany.com --port 587 \\
        --user mailer@mycompany.com --password secret
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import click
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from mail_proxy.mailproxy_db import MailProxyDb
from mail_proxy.entities.account.schema import AccountCreate
from mail_proxy.entities.tenant.schema import (
    DEFAULT_ATTACHMENT_PATH,
    DEFAULT_SYNC_PATH,
    TenantAuth,
    TenantCreate,
    TenantRateLimits,
)

console = Console()
err_console = Console(stderr=True)


def get_persistence(db_path: str) -> MailProxyDb:
    """Create a MailProxyDb instance for database operations.

    Args:
        db_path: Path to the SQLite database file.

    Returns:
        MailProxyDb: Configured persistence instance.
    """
    return MailProxyDb(db_path)


def run_async(coro):
    """Execute an async coroutine synchronously from CLI context.

    Args:
        coro: Async coroutine to execute.

    Returns:
        The coroutine's return value.
    """
    return asyncio.run(coro)


def print_error(message: str) -> None:
    """Print a formatted error message to stderr.

    Args:
        message: Error message text to display.
    """
    err_console.print(f"[red]Error:[/red] {message}")


def print_success(message: str) -> None:
    """Print a formatted success message with checkmark.

    Args:
        message: Success message text to display.
    """
    console.print(f"[green]✓[/green] {message}")


def print_json(data: Any) -> None:
    """Print data as syntax-highlighted JSON.

    Args:
        data: Any JSON-serializable data structure.
    """
    console.print_json(json.dumps(data, indent=2, default=str))


# ============================================================================
# Instance utilities
# ============================================================================

def _get_instance_dir(name: str) -> Path:
    """Get the filesystem path for an instance's data directory.

    Args:
        name: Instance name.

    Returns:
        Path: ~/.mail-proxy/<name>/
    """
    return Path.home() / ".mail-proxy" / name


def _get_pid_file(name: str) -> Path:
    """Get the PID file path for tracking server process.

    Args:
        name: Instance name.

    Returns:
        Path: ~/.mail-proxy/<name>/server.pid
    """
    return _get_instance_dir(name) / "server.pid"


def _get_db_path(name: str) -> str:
    """Get the SQLite database file path for an instance.

    Args:
        name: Instance name.

    Returns:
        str: ~/.mail-proxy/<name>/mail_service.db
    """
    return str(_get_instance_dir(name) / "mail_service.db")


def _is_instance_running(name: str) -> tuple[bool, int | None, int | None]:
    """Check if an instance is running.

    Returns:
        (is_running, pid, port)
    """
    import os

    pid_file = _get_pid_file(name)
    if not pid_file.exists():
        return False, None, None

    try:
        data = json.loads(pid_file.read_text())
        pid = data.get("pid")
        port = data.get("port")

        if pid is None:
            return False, None, port

        # Check if process is alive
        os.kill(pid, 0)  # Doesn't kill, just checks
        return True, pid, port
    except (json.JSONDecodeError, ProcessLookupError, PermissionError, OSError):
        # Process not running or PID file corrupt
        return False, None, None


def _write_pid_file(name: str, pid: int, port: int, host: str) -> None:
    """Write server process information to PID file.

    Args:
        name: Instance name.
        pid: Process ID of the running server.
        port: TCP port the server is listening on.
        host: Host address the server is bound to.
    """
    pid_file = _get_pid_file(name)
    pid_file.write_text(json.dumps({
        "pid": pid,
        "port": port,
        "host": host,
        "started_at": datetime.now().isoformat(),
    }, indent=2))


def _remove_pid_file(name: str) -> None:
    """Delete the PID file when server stops.

    Args:
        name: Instance name.
    """
    pid_file = _get_pid_file(name)
    if pid_file.exists():
        pid_file.unlink()


def _generate_api_token() -> str:
    """Generate a cryptographically secure random API token.

    Returns:
        str: 32-byte URL-safe base64-encoded token.
    """
    import secrets
    return secrets.token_urlsafe(32)


def _get_next_available_port(start_port: int = 8000) -> int:
    """Find the next available TCP port for a new instance.

    Scans existing instances to find ports already in use and returns
    the next sequential port.

    Args:
        start_port: Port number to start searching from.

    Returns:
        int: First available port >= start_port.
    """
    mail_proxy_dir = Path.home() / ".mail-proxy"

    if not mail_proxy_dir.exists():
        return start_port

    used_ports = set()

    # Read port from each instance's database
    for item in mail_proxy_dir.iterdir():
        if item.is_dir():
            db_file = item / "mail_service.db"
            if db_file.exists():
                config = _get_instance_config(item.name)
                if config and config.get("port"):
                    used_ports.add(config["port"])

    # Find next available port
    port = start_port
    while port in used_ports:
        port += 1

    return port


def _ensure_instance(name: str, port: int = 8000, host: str = "0.0.0.0") -> dict[str, Any]:
    """Ensure an instance exists, creating it with defaults if needed.

    Creates the instance directory, initializes the database, and generates
    an API token if this is a new instance.

    Args:
        name: Instance name.
        port: TCP port for the server.
        host: Host address to bind to.

    Returns:
        dict: Instance configuration with name, db_path, host, port, api_token.
    """
    instance_dir = _get_instance_dir(name)
    db_path = str(instance_dir / "mail_service.db")

    # Create directory if needed
    instance_dir.mkdir(parents=True, exist_ok=True)

    # Initialize DB and save config
    persistence = MailProxyDb(db_path)

    async def _init():
        await persistence.init_db()
        # Check if already configured
        existing_token = await persistence.get_config("api_token")
        if not existing_token:
            # First initialization
            api_token = _generate_api_token()
            await persistence.set_config("api_token", api_token)
            await persistence.set_config("name", name)
            await persistence.set_config("host", host)
            await persistence.set_config("port", str(port))
            await persistence.set_config("start_active", "true")
            console.print(f"[green]Created new instance:[/green] {name}")
            console.print(f"  API Token: {api_token}")

    run_async(_init())
    return _get_instance_config(name)


def _get_instance_config(name: str) -> dict[str, Any] | None:
    """Read instance configuration from its SQLite database.

    Args:
        name: Instance name.

    Returns:
        dict: Configuration with name, db_path, host, port, api_token,
        or None if instance doesn't exist.
    """
    db_path = _get_db_path(name)
    if not Path(db_path).exists():
        return None

    persistence = MailProxyDb(db_path)

    async def _get():
        await persistence.init_db()
        return await persistence.get_all_config()

    config = run_async(_get())
    if not config:
        return None

    return {
        "name": config.get("name", name),
        "db_path": db_path,
        "host": config.get("host", "0.0.0.0"),
        "port": int(config.get("port", "8000")),
        "api_token": config.get("api_token"),
    }


def _get_persistence_for_instance(name: str) -> MailProxyDb:
    """Get a MailProxyDb instance for database operations.

    Args:
        name: Instance name.

    Returns:
        MailProxyDb: Configured for the instance's database.

    Raises:
        SystemExit: If the instance doesn't exist.
    """
    config = _get_instance_config(name)
    if not config:
        print_error(f"Instance '{name}' not found. Use 'mail-proxy {name} serve start' to create it.")
        sys.exit(1)
    return MailProxyDb(config["db_path"])


# ============================================================================
# Main CLI group
# ============================================================================

class InstanceGroup(click.Group):
    """Custom group that handles dynamic instance/tenant routing."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._instance_commands = {}  # Commands at instance level
        self._tenant_commands = {}    # Commands at tenant level

    def get_command(self, ctx, cmd_name):
        # First check static commands (list, etc.)
        rv = super().get_command(ctx, cmd_name)
        if rv is not None:
            return rv

        # Otherwise, treat as instance name and return instance group
        return _create_instance_group(cmd_name)

    def list_commands(self, ctx):
        # Static commands plus hint about dynamic instances
        return super().list_commands(ctx)


@click.group(cls=InstanceGroup)
@click.version_option(package_name="genro-mail-proxy")
def main() -> None:
    """genro-mail-proxy CLI - Manage email dispatch instances.

    Usage:

        mail-proxy <instance> <command>       # Instance-level commands

        mail-proxy <instance> <tenant> <command>  # Tenant-level commands

    Examples:

        mail-proxy myserver serve start       # Start server

        mail-proxy myserver tenants list      # List tenants

        mail-proxy myserver acme accounts list  # List accounts for tenant 'acme'
    """
    pass


# ============================================================================
# Static commands (not instance-specific)
# ============================================================================

@main.command("list")
def list_instances() -> None:
    """List mail-proxy instances with their status.

    Shows all instances in ~/.mail-proxy/ with running status.
    """
    mail_proxy_dir = Path.home() / ".mail-proxy"

    if not mail_proxy_dir.exists():
        console.print("[dim]No instances configured.[/dim]")
        console.print("Use 'mail-proxy start <name>' to create one.")
        return

    # Find all instance directories (those with mail_service.db)
    instances = []
    for item in mail_proxy_dir.iterdir():
        if item.is_dir():
            db_file = item / "mail_service.db"
            if db_file.exists():
                instance_name = item.name
                config = _get_instance_config(instance_name)

                if config:
                    port = config.get("port", 8000)
                    host = config.get("host", "0.0.0.0")

                    # Check if running
                    is_running, pid, running_port = _is_instance_running(instance_name)

                    instances.append({
                        "name": instance_name,
                        "port": running_port or port,
                        "host": host,
                        "running": is_running,
                        "pid": pid,
                    })

    if not instances:
        console.print("[dim]No instances configured.[/dim]")
        console.print("Use 'mail-proxy start <name>' to create one.")
        return

    table = Table(title="Mail Proxy Instances")
    table.add_column("Name", style="cyan")
    table.add_column("Status")
    table.add_column("Port", justify="right")
    table.add_column("PID", justify="right")
    table.add_column("URL")

    for inst in sorted(instances, key=lambda x: x["name"]):
        if inst["running"]:
            status = "[green]running[/green]"
            pid_str = str(inst["pid"])
            url = f"http://localhost:{inst['port']}"
        else:
            status = "[dim]stopped[/dim]"
            pid_str = "[dim]-[/dim]"
            url = "[dim]-[/dim]"

        table.add_row(
            inst["name"],
            status,
            str(inst["port"]),
            pid_str,
            url,
        )

    console.print(table)


@main.command("delete")
@click.argument("instance_name")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
def delete_instance(instance_name: str, force: bool) -> None:
    """Delete an instance completely.

    Stops the server if running and removes all data (database, config, logs).

    Example:

        mail-proxy delete myserver

        mail-proxy delete myserver --force
    """
    import shutil
    import signal as sig

    instance_dir = _get_instance_dir(instance_name)

    if not instance_dir.exists():
        print_error(f"Instance '{instance_name}' does not exist.")
        sys.exit(1)

    if not force:
        console.print(f"\n[bold red]This will permanently delete instance '{instance_name}'[/bold red]")
        console.print(f"  Directory: {instance_dir}")
        console.print()
        if not click.confirm("Are you sure?", default=False):
            console.print("[dim]Cancelled.[/dim]")
            return

    # Stop server if running
    is_running, pid, _ = _is_instance_running(instance_name)
    if is_running:
        console.print(f"Stopping server (PID {pid})... ", end="")
        _stop_instance(instance_name, sig.SIGTERM, timeout=3.0)
        console.print("[green]stopped[/green]")

    # Remove instance directory
    console.print(f"Removing {instance_dir}... ", end="")
    shutil.rmtree(instance_dir)
    console.print("[green]done[/green]")

    print_success(f"Instance '{instance_name}' deleted.")


@main.command("start")
@click.argument("instance_name")
@click.option("--host", "-h", default=None, help="Host to bind to (default: 0.0.0.0).")
@click.option("--port", "-p", type=int, default=None, help="Port to listen on (default: 8000).")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
@click.option("--background", "-b", is_flag=True, help="Start in background.")
def start_instance(instance_name: str, host: str | None, port: int | None, reload: bool, background: bool) -> None:
    """Start an instance.

    Example:

        mail-proxy start myserver

        mail-proxy start myserver -p 8080 -b
    """
    _do_start_instance(instance_name, host, port, reload, background)


@main.command("stop")
@click.argument("instance_name")
@click.option("--force", "-f", is_flag=True, help="Force kill (SIGKILL) instead of graceful shutdown.")
def stop_instance(instance_name: str, force: bool) -> None:
    """Stop an instance.

    Example:

        mail-proxy stop myserver

        mail-proxy stop myserver --force
    """
    _do_stop_instance(instance_name, force)


@main.command("restart")
@click.argument("instance_name")
@click.option("--force", "-f", is_flag=True, help="Force kill before restart.")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
def restart_instance(instance_name: str, force: bool, reload: bool) -> None:
    """Restart an instance.

    Example:

        mail-proxy restart myserver
    """
    _do_restart_instance(instance_name, force, reload)


@main.command("status")
@click.argument("instance_name")
def status_instance(instance_name: str) -> None:
    """Show instance status.

    Example:

        mail-proxy status myserver
    """
    _do_status_instance(instance_name)


# ============================================================================
# Instance-level commands factory
# ============================================================================

def _create_instance_group(instance_name: str) -> click.Group:
    """Create a command group for a specific instance."""

    class TenantAwareGroup(click.Group):
        """Group that can route to tenant-specific commands."""

        def get_command(self, ctx, cmd_name):
            # First check if it's a known instance-level command
            rv = super().get_command(ctx, cmd_name)
            if rv is not None:
                return rv

            # Otherwise, treat as tenant name
            return _create_tenant_group(instance_name, cmd_name)

    @click.group(cls=TenantAwareGroup, invoke_without_command=True)
    @click.pass_context
    def instance_group(ctx):
        """Commands for this instance."""
        ctx.ensure_object(dict)
        ctx.obj["instance"] = instance_name

        if ctx.invoked_subcommand is None:
            # Show help with available subcommands
            click.echo(ctx.get_help())

    # Add instance-level commands
    _add_instance_info_command(instance_group, instance_name)
    _add_tenants_commands(instance_group, instance_name)
    _add_stats_command(instance_group, instance_name)
    _add_connect_command(instance_group, instance_name)
    _add_token_command(instance_group, instance_name)
    _add_config_command(instance_group, instance_name)
    _add_command_log_commands(instance_group, instance_name)

    return instance_group


# ============================================================================
# SERVE implementation functions (shared by top-level and nested commands)
# ============================================================================

def _do_start_instance(instance_name: str, host: str | None, port: int | None,
                       reload: bool, background: bool) -> None:
    """Start an instance."""
    import os
    import subprocess
    import time

    import uvicorn

    # Check if already running
    is_running, pid, running_port = _is_instance_running(instance_name)
    if is_running:
        console.print(f"[yellow]Instance '{instance_name}' is already running[/yellow]")
        console.print(f"  PID:  {pid}")
        console.print(f"  Port: {running_port}")
        console.print(f"  URL:  http://localhost:{running_port}")
        sys.exit(0)

    # Get or create instance config
    instance_config = _get_instance_config(instance_name)

    if instance_config is None:
        # New instance - use provided values or find next available port
        host = host or "0.0.0.0"
        if port is None:
            port = _get_next_available_port()
        instance_config = _ensure_instance(instance_name, port, host)
    else:
        # Existing instance - use config values, allow override
        host = host or instance_config["host"]
        port = port or instance_config["port"]

    db_path = instance_config["db_path"]

    if background:
        # Start in background
        console.print(f"[bold cyan]Starting {instance_name} in background...[/bold cyan]")
        cmd = ["mail-proxy", "start", instance_name, "--host", host, "--port", str(port)]
        if reload:
            cmd.append("--reload")

        # Pass only GMP_DB_PATH - server reads all config from database
        env = os.environ.copy()
        env["GMP_DB_PATH"] = db_path

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )

        # Wait for server to be ready
        import requests
        url = f"http://localhost:{port}/status"
        for _ in range(50):  # Max 5 seconds
            time.sleep(0.1)
            is_running, pid, _ = _is_instance_running(instance_name)
            if is_running:
                try:
                    resp = requests.get(url, timeout=0.5)
                    if resp.status_code == 200:
                        console.print(f"  PID:  {pid}")
                        console.print(f"  Port: {port}")
                        console.print(f"  URL:  http://localhost:{port}")
                        return
                except requests.RequestException:
                    pass

        console.print("[yellow]Server starting in background...[/yellow]")
        return

    # Set environment variable for server.py (reads all config from database)
    os.environ["GMP_DB_PATH"] = db_path

    console.print(f"\n[bold cyan]Starting {instance_name}[/bold cyan]")
    console.print(f"  DB:      {db_path}")
    console.print(f"  Listen:  {host}:{port}")
    console.print()

    # Write PID file before starting uvicorn
    _write_pid_file(instance_name, os.getpid(), port, host)

    try:
        uvicorn.run(
            "mail_proxy.server:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )
    finally:
        _remove_pid_file(instance_name)


def _do_stop_instance(instance_name: str, force: bool) -> None:
    """Stop an instance."""
    import signal as sig

    signal_type = sig.SIGKILL if force else sig.SIGTERM

    is_running, pid, _ = _is_instance_running(instance_name)
    if not is_running:
        console.print(f"[dim]Instance '{instance_name}' is not running.[/dim]")
        return

    console.print(f"Stopping {instance_name} (PID {pid})... ", end="")
    if _stop_instance(instance_name, signal_type):
        console.print("[green]stopped[/green]")
    else:
        signal_name = "SIGKILL" if force else "SIGTERM"
        console.print(f"[yellow]sent {signal_name}, may still be shutting down[/yellow]")


def _do_status_instance(instance_name: str) -> None:
    """Show instance status."""
    config = _get_instance_config(instance_name)
    if not config:
        console.print(f"[dim]Instance '{instance_name}' not found.[/dim]")
        return

    is_running, pid, port = _is_instance_running(instance_name)

    console.print(f"\n[bold cyan]Instance: {instance_name}[/bold cyan]")
    console.print(f"  Status:  {'[green]running[/green]' if is_running else '[dim]stopped[/dim]'}")
    if is_running:
        console.print(f"  PID:     {pid}")
        console.print(f"  Port:    {port}")
        console.print(f"  URL:     http://localhost:{port}")
    console.print(f"  DB:      {config['db_path']}")
    console.print()


def _do_restart_instance(instance_name: str, force: bool, reload: bool) -> None:
    """Restart an instance."""
    import os
    import signal as sig
    import subprocess
    import time

    signal_type = sig.SIGKILL if force else sig.SIGTERM

    config = _get_instance_config(instance_name)
    if not config:
        console.print(f"[dim]Instance '{instance_name}' not found.[/dim]")
        return

    is_running, pid, _ = _is_instance_running(instance_name)
    if is_running:
        console.print(f"Stopping {instance_name} (PID {pid})... ", end="")
        if _stop_instance(instance_name, signal_type, timeout=3.0):
            console.print("[green]stopped[/green]")
        else:
            if not force:
                console.print("[yellow]forcing...[/yellow] ", end="")
                _stop_instance(instance_name, sig.SIGKILL, timeout=1.0)
            console.print("[green]stopped[/green]")

    # Brief pause to ensure port is released
    time.sleep(0.5)

    # Start in background
    console.print(f"Starting {instance_name}... ", end="")
    cmd = ["mail-proxy", "start", instance_name]
    if reload:
        cmd.append("--reload")

    # Pass only GMP_DB_PATH - server reads all config from database
    env = os.environ.copy()
    env["GMP_DB_PATH"] = config["db_path"]

    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env=env,
    )

    # Wait for startup
    time.sleep(1.0)
    is_running, pid, port = _is_instance_running(instance_name)
    if is_running:
        console.print(f"[green]started[/green] (PID {pid}, port {port})")
    else:
        console.print("[yellow]starting in background...[/yellow]")


def _stop_instance(name: str, signal_type: int = 15, timeout: float = 5.0, fallback_kill: bool = True) -> bool:
    """Stop a running instance by sending a signal."""
    import os
    import signal as sig
    import time

    is_running, pid, _ = _is_instance_running(name)
    if not is_running or pid is None:
        return False

    try:
        os.kill(pid, signal_type)
        wait_iterations = int(timeout / 0.1)
        for _ in range(wait_iterations):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                _remove_pid_file(name)
                return True

        if fallback_kill and signal_type != sig.SIGKILL:
            os.kill(pid, sig.SIGKILL)
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                _remove_pid_file(name)
                return True

        return False
    except (ProcessLookupError, PermissionError, OSError):
        _remove_pid_file(name)
        return False


# ============================================================================
# TENANTS commands
# ============================================================================

def _add_tenants_commands(group: click.Group, instance_name: str) -> None:
    """Add tenants management commands."""

    def _do_tenants_list(active_only: bool = False, as_json: bool = False) -> None:
        """Internal function to list tenants."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _list():
            await persistence.init_db()
            return await persistence.list_tenants(active_only=active_only)

        tenant_list = run_async(_list())

        if as_json:
            print_json(tenant_list)
            return

        if not tenant_list:
            console.print("[dim]No tenants found.[/dim]")
            return

        table = Table(title="Tenants")
        table.add_column("ID", style="cyan")
        table.add_column("Name")
        table.add_column("Active", justify="center")
        table.add_column("Base URL")

        for t in tenant_list:
            active = "[green]✓[/green]" if t.get("active") else "[red]✗[/red]"
            table.add_row(
                t["id"],
                t.get("name") or "-",
                active,
                t.get("client_base_url") or "-",
            )

        console.print(table)

    @group.group("tenants", invoke_without_command=True)
    @click.pass_context
    def tenants(ctx):
        """Manage tenants for this instance."""
        if ctx.invoked_subcommand is None:
            click.echo(ctx.get_help())

    @tenants.command("list")
    @click.option("--active-only", "-a", is_flag=True, help="Show only active tenants.")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def tenants_list(active_only: bool, as_json: bool) -> None:
        """List all tenants."""
        _do_tenants_list(active_only, as_json)

    @tenants.command("show")
    @click.argument("tenant_id")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def tenants_show(tenant_id: str, as_json: bool) -> None:
        """Show details for a specific tenant."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _show():
            await persistence.init_db()
            return await persistence.get_tenant(tenant_id)

        tenant_data = run_async(_show())

        if not tenant_data:
            print_error(f"Tenant '{tenant_id}' not found.")
            sys.exit(1)

        if as_json:
            print_json(tenant_data)
            return

        console.print(f"\n[bold cyan]Tenant: {tenant_id}[/bold cyan]\n")
        console.print(f"  Name:            {tenant_data.get('name') or '-'}")
        console.print(f"  Active:          {'Yes' if tenant_data.get('active') else 'No'}")
        console.print(f"  Base URL:        {tenant_data.get('client_base_url') or '-'}")
        console.print(f"  Sync Path:       {tenant_data.get('client_sync_path') or DEFAULT_SYNC_PATH}")
        console.print(f"  Attachment Path: {tenant_data.get('client_attachment_path') or DEFAULT_ATTACHMENT_PATH}")
        console.print(f"  Created:         {tenant_data.get('created_at') or '-'}")
        console.print(f"  Updated:         {tenant_data.get('updated_at') or '-'}")

        if tenant_data.get("client_auth"):
            auth = tenant_data["client_auth"]
            method = auth.get('method', 'none')
            console.print(f"  Auth Method:     {method}")
            if method == "basic" and auth.get("user"):
                console.print(f"  Auth User:       {auth.get('user')}")

        if tenant_data.get("rate_limits"):
            limits = tenant_data["rate_limits"]
            console.print(f"  Rate Limits:     hourly={limits.get('hourly', 0)}, daily={limits.get('daily', 0)}")

        console.print()

    @tenants.command("add")
    @click.argument("tenant_id", required=False)
    @click.option("--name", "-n", help="Human-readable tenant name.")
    @click.option("--base-url", help="Base URL for tenant HTTP endpoints.")
    @click.option("--sync-path", help=f"Path for delivery callbacks (default: {DEFAULT_SYNC_PATH}).")
    @click.option("--attachment-path", help=f"Path for attachments (default: {DEFAULT_ATTACHMENT_PATH}).")
    @click.option("--auth-method", type=click.Choice(["none", "bearer", "basic"]),
                  help="Authentication method for HTTP endpoints.")
    @click.option("--auth-token", help="Bearer token (for bearer auth).")
    @click.option("--auth-user", help="Username (for basic auth).")
    @click.option("--auth-password", help="Password (for basic auth).")
    @click.option("--rate-limit-hourly", type=int, help="Max emails per hour (0=unlimited).")
    @click.option("--rate-limit-daily", type=int, help="Max emails per day (0=unlimited).")
    @click.option("--inactive", is_flag=True, help="Create tenant as inactive.")
    def tenants_add(
        tenant_id: str | None,
        name: str | None,
        base_url: str | None,
        sync_path: str | None,
        attachment_path: str | None,
        auth_method: str | None,
        auth_token: str | None,
        auth_user: str | None,
        auth_password: str | None,
        rate_limit_hourly: int | None,
        rate_limit_daily: int | None,
        inactive: bool,
    ) -> None:
        """Add a new tenant.

        Run without arguments for interactive mode.
        """
        # Interactive mode if tenant_id not provided
        if not tenant_id:
            console.print("\n[bold cyan]Add new tenant[/bold cyan]\n")
            tenant_id = click.prompt("Tenant ID (alphanumeric, underscores, hyphens)")

        if name is None:
            name = click.prompt("Display name", default="", show_default=False) or None

        if base_url is None:
            base_url = click.prompt("Base URL (e.g. https://api.example.com)", default="", show_default=False) or None

        if base_url and sync_path is None:
            sync_path = click.prompt("Sync path", default=DEFAULT_SYNC_PATH) or None

        if base_url and attachment_path is None:
            attachment_path = click.prompt("Attachment path", default=DEFAULT_ATTACHMENT_PATH) or None

        if auth_method is None:
            auth_method = click.prompt(
                "Auth method",
                type=click.Choice(["none", "bearer", "basic"]),
                default="none"
            )

        if auth_method == "bearer" and not auth_token:
            auth_token = click.prompt("Bearer token")
        elif auth_method == "basic":
            if not auth_user:
                auth_user = click.prompt("Auth username")
            if not auth_password:
                auth_password = click.prompt("Auth password", hide_input=True)

        if rate_limit_hourly is None:
            rate_limit_hourly = click.prompt("Rate limit hourly (0=unlimited)", type=int, default=0)

        if rate_limit_daily is None:
            rate_limit_daily = click.prompt("Rate limit daily (0=unlimited)", type=int, default=0)

        persistence = _get_persistence_for_instance(instance_name)

        client_auth = None
        if auth_method != "none":
            client_auth = {
                "method": auth_method,
                "token": auth_token,
                "user": auth_user,
                "password": auth_password,
            }

        rate_limits = None
        if rate_limit_hourly > 0 or rate_limit_daily > 0:
            rate_limits = {
                "hourly": rate_limit_hourly,
                "daily": rate_limit_daily,
            }

        try:
            tenant_data = TenantCreate(
                id=tenant_id,
                name=name,
                client_auth=TenantAuth(**client_auth) if client_auth else None,
                client_base_url=base_url,
                client_sync_path=sync_path,
                client_attachment_path=attachment_path,
                rate_limits=TenantRateLimits(**rate_limits) if rate_limits else None,
                active=not inactive,
            )
        except ValidationError as e:
            print_error(f"Validation error: {e}")
            sys.exit(1)

        async def _add():
            await persistence.init_db()
            await persistence.add_tenant(tenant_data.model_dump(exclude_none=True))

        run_async(_add())
        print_success(f"Tenant '{tenant_id}' created.")

    @tenants.command("update")
    @click.argument("tenant_id")
    @click.option("--name", "-n", help="Human-readable tenant name.")
    @click.option("--base-url", help="Base URL for tenant HTTP endpoints.")
    @click.option("--sync-path", help="Path for delivery callbacks.")
    @click.option("--attachment-path", help="Path for attachments.")
    @click.option("--auth-method", type=click.Choice(["none", "bearer", "basic"]),
                  help="Authentication method for HTTP endpoints.")
    @click.option("--auth-token", help="Bearer token (for bearer auth).")
    @click.option("--auth-user", help="Username (for basic auth).")
    @click.option("--auth-password", help="Password (for basic auth).")
    @click.option("--rate-limit-hourly", type=int, help="Max emails per hour (0=unlimited).")
    @click.option("--rate-limit-daily", type=int, help="Max emails per day (0=unlimited).")
    @click.option("--active/--inactive", default=None, help="Set tenant active status.")
    def tenants_update(
        tenant_id: str,
        name: str | None,
        base_url: str | None,
        sync_path: str | None,
        attachment_path: str | None,
        auth_method: str | None,
        auth_token: str | None,
        auth_user: str | None,
        auth_password: str | None,
        rate_limit_hourly: int | None,
        rate_limit_daily: int | None,
        active: bool | None,
    ) -> None:
        """Update an existing tenant."""
        persistence = _get_persistence_for_instance(instance_name)

        updates: dict[str, Any] = {}

        if name is not None:
            updates["name"] = name
        if base_url is not None:
            updates["client_base_url"] = base_url
        if sync_path is not None:
            updates["client_sync_path"] = sync_path
        if attachment_path is not None:
            updates["client_attachment_path"] = attachment_path
        if active is not None:
            updates["active"] = active

        if any([auth_method, auth_token, auth_user, auth_password]):
            updates["client_auth"] = {
                "method": auth_method or "none",
                "token": auth_token,
                "user": auth_user,
                "password": auth_password,
            }

        if rate_limit_hourly is not None or rate_limit_daily is not None:
            updates["rate_limits"] = {
                "hourly": rate_limit_hourly or 0,
                "daily": rate_limit_daily or 0,
            }

        if not updates:
            print_error("No updates specified.")
            sys.exit(1)

        async def _update():
            await persistence.init_db()
            return await persistence.update_tenant(tenant_id, updates)

        success = run_async(_update())

        if success:
            print_success(f"Tenant '{tenant_id}' updated.")
        else:
            print_error(f"Tenant '{tenant_id}' not found.")
            sys.exit(1)

    @tenants.command("delete")
    @click.argument("tenant_id")
    @click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
    def tenants_delete(tenant_id: str, force: bool) -> None:
        """Delete a tenant and all associated accounts/messages."""
        persistence = _get_persistence_for_instance(instance_name)

        if not force and not click.confirm(f"Delete tenant '{tenant_id}' and all associated data?"):
            console.print("Aborted.")
            return

        async def _delete():
            await persistence.init_db()
            return await persistence.delete_tenant(tenant_id)

        success = run_async(_delete())

        if success:
            print_success(f"Tenant '{tenant_id}' deleted.")
        else:
            print_error(f"Tenant '{tenant_id}' not found.")
            sys.exit(1)


# ============================================================================
# INSTANCE INFO command
# ============================================================================

def _add_instance_info_command(group: click.Group, instance_name: str) -> None:
    """Add info command for instance."""

    @group.command("info")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def info(as_json: bool) -> None:
        """Show instance configuration and status."""
        config = _get_instance_config(instance_name)
        if not config:
            print_error(f"Instance '{instance_name}' not found.")
            sys.exit(1)

        is_running, pid, running_port = _is_instance_running(instance_name)

        data = {
            "name": config["name"],
            "db_path": config["db_path"],
            "host": config["host"],
            "port": config["port"],
            "api_token": config.get("api_token"),
            "status": "running" if is_running else "stopped",
            "pid": pid,
        }

        if as_json:
            print_json(data)
            return

        console.print(f"\n[bold cyan]Instance: {instance_name}[/bold cyan]\n")
        console.print(f"  Database:    {config['db_path']}")
        console.print(f"  Host:        {config['host']}")
        console.print(f"  Port:        {config['port']}")
        console.print(f"  API Token:   {config.get('api_token') or '-'}")

        if is_running:
            console.print(f"  Status:      [green]Running[/green] (PID: {pid})")
            console.print(f"  URL:         http://localhost:{running_port or config['port']}")
        else:
            console.print("  Status:      [dim]Stopped[/dim]")

        console.print()


# ============================================================================
# STATS command
# ============================================================================

def _add_stats_command(group: click.Group, instance_name: str) -> None:
    """Add stats command."""

    @group.command("stats")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def stats(as_json: bool) -> None:
        """Show queue statistics for this instance."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _stats():
            await persistence.init_db()

            tenants = await persistence.list_tenants()
            accounts = await persistence.list_accounts()
            messages = await persistence.list_messages()

            pending = sum(1 for m in messages if not m.get("smtp_ts") and not m.get("error_ts"))
            sent = sum(1 for m in messages if m.get("smtp_ts"))
            errors = sum(1 for m in messages if m.get("error_ts"))

            return {
                "tenants": len(tenants),
                "accounts": len(accounts),
                "messages": {
                    "total": len(messages),
                    "pending": pending,
                    "sent": sent,
                    "error": errors,
                },
            }

        data = run_async(_stats())

        if as_json:
            print_json(data)
            return

        console.print(f"\n[bold]Stats for {instance_name}[/bold]\n")
        console.print(f"  Tenants:    {data['tenants']}")
        console.print(f"  Accounts:   {data['accounts']}")
        console.print("  Messages:")
        console.print(f"    Total:    {data['messages']['total']}")
        console.print(f"    Pending:  {data['messages']['pending']}")
        console.print(f"    Sent:     {data['messages']['sent']}")
        console.print(f"    Errors:   {data['messages']['error']}")
        console.print()


# ============================================================================
# CONNECT command
# ============================================================================

def _add_connect_command(group: click.Group, instance_name: str) -> None:
    """Add connect command for REPL."""

    @group.command("connect")
    @click.option("--token", "-t", envvar="GMP_API_TOKEN", help="API token for authentication.")
    def connect_cmd(token: str | None) -> None:
        """Connect to this instance with an interactive REPL."""
        import code
        import readline  # noqa: F401
        import rlcompleter  # noqa: F401

        from mail_proxy.client import MailProxyClient
        from mail_proxy.client import connect as client_connect
        from mail_proxy.forms import (
            AccountForm,
            MessageForm,
            TenantForm,
            new_account,
            new_message,
            new_tenant,
            set_proxy,
        )

        # Check if running
        is_running, pid, port = _is_instance_running(instance_name)
        if not is_running:
            print_error(f"Instance '{instance_name}' is not running")
            console.print(f"[dim]Start it with: mail-proxy {instance_name} serve start[/dim]")
            sys.exit(1)

        url = f"http://localhost:{port}"

        # Get token from config if not provided
        if not token:
            config = _get_instance_config(instance_name)
            if config:
                token = config.get("api_token")

        try:
            proxy = client_connect(url, token=token, name=instance_name)

            if not proxy.health():
                print_error(f"Cannot connect to {instance_name} ({url})")
                console.print("[dim]Make sure the server is running.[/dim]")
                return

            set_proxy(proxy)

            console.print(f"\n[bold green]Connected to {instance_name}[/bold green]")
            console.print(f"  URL: {url}")
            console.print()

            console.print("[bold]Available objects:[/bold]")
            console.print("  [cyan]proxy[/cyan]          - The connected client")
            console.print("  [cyan]proxy.messages[/cyan] - Message management")
            console.print("  [cyan]proxy.accounts[/cyan] - Account management")
            console.print("  [cyan]proxy.tenants[/cyan]  - Tenant management")
            console.print()
            console.print("[bold]Quick commands:[/bold]")
            console.print("  [cyan]proxy.status()[/cyan]          - Server status")
            console.print("  [cyan]proxy.stats()[/cyan]           - Queue statistics")
            console.print("  [cyan]proxy.run_now()[/cyan]         - Trigger dispatch cycle")
            console.print()
            console.print("[bold]Interactive forms:[/bold]")
            console.print("  [cyan]new_tenant()[/cyan]   - Create tenant")
            console.print("  [cyan]new_account()[/cyan]  - Create account")
            console.print("  [cyan]new_message()[/cyan]  - Create message")
            console.print()
            console.print("[dim]Type 'exit()' or Ctrl+D to quit.[/dim]")
            console.print()

            namespace = {
                "proxy": proxy,
                "MailProxyClient": MailProxyClient,
                "console": console,
                "new_tenant": new_tenant,
                "new_account": new_account,
                "new_message": new_message,
                "TenantForm": TenantForm,
                "AccountForm": AccountForm,
                "MessageForm": MessageForm,
            }

            code.interact(banner="", local=namespace, exitmsg="Goodbye!")

        except Exception as e:
            print_error(f"Connection failed: {e}")
            sys.exit(1)


# ============================================================================
# RUN-NOW command (tenant-level)
# ============================================================================

def _add_run_now_command(group: click.Group, instance_name: str, tenant_id: str) -> None:
    """Add run-now command to trigger immediate dispatch cycle for a tenant."""

    @group.command("run-now")
    def run_now_cmd() -> None:
        """Trigger immediate dispatch and sync cycle for this tenant."""
        config = _get_instance_config(instance_name)
        if not config:
            print_error(f"Instance '{instance_name}' not found or not configured.")
            sys.exit(1)

        running, pid, port = _is_instance_running(instance_name)
        if not running:
            print_error(f"Instance '{instance_name}' is not running.")
            sys.exit(1)

        host = config.get("host", "127.0.0.1")
        if host == "0.0.0.0":
            host = "127.0.0.1"
        token = config.get("api_token")

        import requests
        try:
            headers = {"X-API-Token": token} if token else {}
            resp = requests.post(
                f"http://{host}:{port}/commands/run-now",
                headers=headers,
                params={"tenant_id": tenant_id},
                timeout=10
            )
            resp.raise_for_status()
            result = resp.json()
            if result.get("ok"):
                print_success(f"Dispatch cycle triggered for tenant '{tenant_id}'.")
            else:
                print_error(f"Server returned: {result}")
        except requests.RequestException as e:
            print_error(f"Failed to trigger run-now: {e}")
            sys.exit(1)


# ============================================================================
# TOKEN command
# ============================================================================

def _add_token_command(group: click.Group, instance_name: str) -> None:
    """Add token command."""

    @group.command("token")
    @click.option("--regenerate", "-r", is_flag=True, help="Generate a new token.")
    def token_cmd(regenerate: bool) -> None:
        """Show or regenerate the API token for this instance."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _token():
            await persistence.init_db()
            if regenerate:
                new_token = _generate_api_token()
                await persistence.set_config("api_token", new_token)
                return new_token, True
            return await persistence.get_config("api_token"), False

        token, is_new = run_async(_token())

        if is_new:
            console.print(f"[green]Token regenerated for instance:[/green] {instance_name}")
            console.print("[yellow]Note:[/yellow] Restart the instance for the new token to take effect.")
            console.print(f"\n{token}")
        else:
            if not token:
                console.print(f"[yellow]No API token configured for instance:[/yellow] {instance_name}")
                console.print("Use --regenerate to generate one.")
                sys.exit(1)

            console.print(token)


# ============================================================================
# CONFIG command (instance configuration)
# ============================================================================

def _add_config_command(group: click.Group, instance_name: str) -> None:
    """Add config command for instance configuration."""

    @group.group("config", invoke_without_command=True)
    @click.pass_context
    def config_group(ctx):
        """Manage instance configuration."""
        if ctx.invoked_subcommand is None:
            click.echo(ctx.get_help())

    @config_group.command("show")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def config_show(as_json: bool) -> None:
        """Show instance configuration."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _show():
            await persistence.init_db()
            return await persistence.instance.get_instance()

        instance = run_async(_show())

        if as_json:
            print_json(instance or {})
            return

        if not instance:
            console.print("[dim]Instance not configured.[/dim]")
            return

        console.print(f"\n[bold cyan]Instance Configuration[/bold cyan]\n")
        console.print(f"  Name:              {instance.get('name') or 'mail-proxy'}")
        console.print(f"  API Token:         {'***' if instance.get('api_token') else '-'}")

        # Bounce detection config
        bounce_enabled = bool(instance.get("bounce_enabled"))
        console.print(f"\n  [bold]Bounce Detection:[/bold]")
        console.print(f"    Enabled:         {'[green]Yes[/green]' if bounce_enabled else '[dim]No[/dim]'}")
        if bounce_enabled:
            console.print(f"    IMAP Host:       {instance.get('bounce_imap_host') or '-'}")
            console.print(f"    IMAP Port:       {instance.get('bounce_imap_port') or 993}")
            console.print(f"    IMAP User:       {instance.get('bounce_imap_user') or '-'}")
            console.print(f"    IMAP Folder:     {instance.get('bounce_imap_folder') or 'INBOX'}")
            console.print(f"    Return-Path:     {instance.get('bounce_return_path') or '-'}")
            console.print(f"    Last UID:        {instance.get('bounce_last_uid') or '-'}")
            console.print(f"    Last Sync:       {instance.get('bounce_last_sync') or '-'}")

        console.print()

    @config_group.command("set")
    @click.option("--name", "-n", help="Instance name.")
    @click.option("--bounce-enabled/--bounce-disabled", default=None, help="Enable/disable bounce detection.")
    @click.option("--bounce-imap-host", help="IMAP host for bounce mailbox.")
    @click.option("--bounce-imap-port", type=int, help="IMAP port (default: 993).")
    @click.option("--bounce-imap-user", help="IMAP username.")
    @click.option("--bounce-imap-password", help="IMAP password.")
    @click.option("--bounce-imap-folder", help="IMAP folder (default: INBOX).")
    @click.option("--bounce-return-path", help="Return-Path header for outgoing emails.")
    def config_set(
        name: str | None,
        bounce_enabled: bool | None,
        bounce_imap_host: str | None,
        bounce_imap_port: int | None,
        bounce_imap_user: str | None,
        bounce_imap_password: str | None,
        bounce_imap_folder: str | None,
        bounce_return_path: str | None,
    ) -> None:
        """Update instance configuration."""
        persistence = _get_persistence_for_instance(instance_name)

        updates: dict[str, Any] = {}
        if name is not None:
            updates["name"] = name
        if bounce_enabled is not None:
            updates["bounce_enabled"] = 1 if bounce_enabled else 0
        if bounce_imap_host is not None:
            updates["bounce_imap_host"] = bounce_imap_host
        if bounce_imap_port is not None:
            updates["bounce_imap_port"] = bounce_imap_port
        if bounce_imap_user is not None:
            updates["bounce_imap_user"] = bounce_imap_user
        if bounce_imap_password is not None:
            updates["bounce_imap_password"] = bounce_imap_password
        if bounce_imap_folder is not None:
            updates["bounce_imap_folder"] = bounce_imap_folder
        if bounce_return_path is not None:
            updates["bounce_return_path"] = bounce_return_path

        if not updates:
            print_error("No configuration changes specified.")
            sys.exit(1)

        async def _update():
            await persistence.init_db()
            await persistence.instance.update_instance(updates)

        run_async(_update())
        print_success("Instance configuration updated.")

    @config_group.command("bounce")
    @click.option("--host", "-h", help="IMAP host.", prompt="IMAP host")
    @click.option("--port", "-p", type=int, default=993, help="IMAP port (default: 993).")
    @click.option("--user", "-u", help="IMAP username.", prompt="IMAP username")
    @click.option("--password", help="IMAP password.", prompt="IMAP password", hide_input=True)
    @click.option("--folder", "-f", default="INBOX", help="IMAP folder (default: INBOX).")
    @click.option("--return-path", "-r", help="Return-Path header for outgoing emails.", prompt="Return-Path email")
    def config_bounce(
        host: str,
        port: int,
        user: str,
        password: str,
        folder: str,
        return_path: str,
    ) -> None:
        """Configure bounce detection (interactive).

        Example:

            mail-proxy myserver config bounce
        """
        persistence = _get_persistence_for_instance(instance_name)

        async def _configure():
            await persistence.init_db()
            await persistence.instance.set_bounce_config(
                enabled=True,
                imap_host=host,
                imap_port=port,
                imap_user=user,
                imap_password=password,
                imap_folder=folder,
                return_path=return_path,
            )

        run_async(_configure())
        print_success("Bounce detection configured and enabled.")
        console.print(f"  IMAP:         {user}@{host}:{port}/{folder}")
        console.print(f"  Return-Path:  {return_path}")


# ============================================================================
# COMMAND-LOG commands (audit trail)
# ============================================================================

def _add_command_log_commands(group: click.Group, instance_name: str) -> None:
    """Add command-log subgroup to the instance group."""

    @group.group("command-log")
    @click.pass_context
    def command_log(ctx):
        """API command audit log for replay."""
        ctx.ensure_object(dict)
        if ctx.invoked_subcommand is None:
            click.echo(ctx.get_help())

    @command_log.command("list")
    @click.option("--tenant", "-t", help="Filter by tenant ID.")
    @click.option("--since", type=int, help="Filter commands after Unix timestamp.")
    @click.option("--until", type=int, help="Filter commands before Unix timestamp.")
    @click.option("--endpoint", "-e", help="Filter by endpoint (partial match).")
    @click.option("--limit", "-l", type=int, default=50, help="Max commands to show.")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def command_log_list(
        tenant: str | None,
        since: int | None,
        until: int | None,
        endpoint: str | None,
        limit: int,
        as_json: bool,
    ) -> None:
        """List logged API commands."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _list():
            await persistence.init_db()
            return await persistence.list_commands(
                tenant_id=tenant,
                since_ts=since,
                until_ts=until,
                endpoint_filter=endpoint,
                limit=limit,
            )

        commands = run_async(_list())

        if as_json:
            print_json(commands)
            return

        if not commands:
            console.print("[dim]No commands logged.[/dim]")
            return

        from datetime import datetime

        console.print(f"\n[bold cyan]Command Log ({len(commands)} commands)[/bold cyan]\n")

        for cmd in commands:
            ts = datetime.fromtimestamp(cmd["command_ts"]).strftime("%Y-%m-%d %H:%M:%S")
            status = cmd.get("response_status", "-")
            tenant_id = cmd.get("tenant_id") or "-"
            endpoint_str = cmd["endpoint"]

            status_color = "green" if status and 200 <= status < 300 else "red" if status else "dim"
            console.print(
                f"  [{status_color}]{status}[/{status_color}] "
                f"[dim]{ts}[/dim] "
                f"[cyan]{endpoint_str}[/cyan] "
                f"[dim](tenant: {tenant_id})[/dim]"
            )

        console.print()

    @command_log.command("export")
    @click.option("--tenant", "-t", help="Filter by tenant ID.")
    @click.option("--since", type=int, help="Filter commands after Unix timestamp.")
    @click.option("--until", type=int, help="Filter commands before Unix timestamp.")
    @click.option("--output", "-o", type=click.Path(), help="Output file (default: stdout).")
    def command_log_export(
        tenant: str | None,
        since: int | None,
        until: int | None,
        output: str | None,
    ) -> None:
        """Export commands for replay in JSON format."""
        import json

        persistence = _get_persistence_for_instance(instance_name)

        async def _export():
            await persistence.init_db()
            return await persistence.export_commands(
                tenant_id=tenant,
                since_ts=since,
                until_ts=until,
            )

        commands = run_async(_export())

        json_data = json.dumps(commands, indent=2)

        if output:
            with open(output, "w") as f:
                f.write(json_data)
            print_success(f"Exported {len(commands)} commands to {output}")
        else:
            click.echo(json_data)

    @command_log.command("purge")
    @click.option("--before", type=int, required=True, help="Delete commands before Unix timestamp.")
    @click.option("--force", "-f", is_flag=True, help="Skip confirmation.")
    def command_log_purge(before: int, force: bool) -> None:
        """Delete old command logs."""
        from datetime import datetime

        persistence = _get_persistence_for_instance(instance_name)

        ts_str = datetime.fromtimestamp(before).strftime("%Y-%m-%d %H:%M:%S")
        if not force and not click.confirm(f"Delete all commands before {ts_str}?"):
            console.print("Aborted.")
            return

        async def _purge():
            await persistence.init_db()
            return await persistence.purge_commands_before(before)

        deleted = run_async(_purge())
        print_success(f"Deleted {deleted} command(s).")


# ============================================================================
# Tenant-level commands factory
# ============================================================================

def _create_tenant_group(instance_name: str, tenant_id: str) -> click.Group:
    """Create a command group for a specific tenant within an instance."""

    @click.group(invoke_without_command=True)
    @click.pass_context
    def tenant_group(ctx):
        """Commands for this tenant."""
        ctx.ensure_object(dict)
        ctx.obj["instance"] = instance_name
        ctx.obj["tenant"] = tenant_id

        if ctx.invoked_subcommand is None:
            # Show help with available subcommands
            click.echo(ctx.get_help())

    # Add tenant-level commands
    _add_info_command(tenant_group, instance_name, tenant_id)
    _add_accounts_commands(tenant_group, instance_name, tenant_id)
    _add_messages_commands(tenant_group, instance_name, tenant_id)
    _add_send_command(tenant_group, instance_name, tenant_id)
    _add_run_now_command(tenant_group, instance_name, tenant_id)

    return tenant_group


def _add_info_command(group: click.Group, instance_name: str, tenant_id: str) -> None:
    """Add info command for a tenant."""

    @group.command("info")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def info_cmd(as_json: bool) -> None:
        """Show tenant details."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _show():
            await persistence.init_db()
            return await persistence.get_tenant(tenant_id)

        tenant_data = run_async(_show())

        if not tenant_data:
            print_error(f"Tenant '{tenant_id}' not found in instance '{instance_name}'.")
            sys.exit(1)

        if as_json:
            print_json(tenant_data)
            return

        console.print(f"\n[bold cyan]Tenant: {tenant_id}[/bold cyan] (instance: {instance_name})\n")
        console.print(f"  Name:            {tenant_data.get('name') or '-'}")
        console.print(f"  Active:          {'Yes' if tenant_data.get('active') else 'No'}")
        console.print(f"  Base URL:        {tenant_data.get('client_base_url') or '-'}")
        console.print(f"  Sync Path:       {tenant_data.get('client_sync_path') or DEFAULT_SYNC_PATH}")
        console.print(f"  Attachment Path: {tenant_data.get('client_attachment_path') or DEFAULT_ATTACHMENT_PATH}")
        console.print(f"  Created:         {tenant_data.get('created_at') or '-'}")
        console.print(f"  Updated:         {tenant_data.get('updated_at') or '-'}")

        if tenant_data.get("client_auth"):
            auth = tenant_data["client_auth"]
            method = auth.get('method', 'none')
            console.print(f"  Auth Method:     {method}")
            if method == "basic" and auth.get("user"):
                console.print(f"  Auth User:       {auth.get('user')}")

        if tenant_data.get("rate_limits"):
            limits = tenant_data["rate_limits"]
            console.print(f"  Rate Limits:     hourly={limits.get('hourly', 0)}, daily={limits.get('daily', 0)}")

        console.print()


# ============================================================================
# ACCOUNTS commands (tenant-level)
# ============================================================================

def _add_accounts_commands(group: click.Group, instance_name: str, tenant_id: str) -> None:
    """Add accounts management commands for a tenant."""

    def _do_accounts_list(as_json: bool = False) -> None:
        """Internal function to list accounts."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _list():
            await persistence.init_db()
            return await persistence.list_accounts(tenant_id=tenant_id)

        account_list = run_async(_list())

        if as_json:
            print_json(account_list)
            return

        if not account_list:
            console.print("[dim]No accounts found.[/dim]")
            return

        table = Table(title=f"SMTP Accounts (tenant: {tenant_id})")
        table.add_column("ID", style="cyan")
        table.add_column("Tenant ID", style="magenta")
        table.add_column("Host")
        table.add_column("Port", justify="right")
        table.add_column("User")
        table.add_column("TLS", justify="center")

        for acc in account_list:
            tls = "[green]✓[/green]" if acc.get("use_tls") else "[dim]-[/dim]"
            table.add_row(
                acc["id"],
                acc.get("tenant_id") or "-",
                acc["host"],
                str(acc["port"]),
                acc.get("user") or "-",
                tls,
            )

        console.print(table)

    @group.group("accounts", invoke_without_command=True)
    @click.pass_context
    def accounts(ctx):
        """Manage SMTP accounts for this tenant."""
        if ctx.invoked_subcommand is None:
            click.echo(ctx.get_help())

    @accounts.command("list")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def accounts_list(as_json: bool) -> None:
        """List SMTP accounts for this tenant."""
        _do_accounts_list(as_json)

    @accounts.command("show")
    @click.argument("account_id")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def accounts_show(account_id: str, as_json: bool) -> None:
        """Show details for a specific account."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _show():
            await persistence.init_db()
            try:
                return await persistence.get_account(tenant_id, account_id)
            except ValueError:
                return None

        account_data = run_async(_show())

        if not account_data:
            print_error(f"Account '{account_id}' not found for tenant '{tenant_id}'.")
            sys.exit(1)

        display_data = {k: v for k, v in account_data.items() if k != "password"}
        display_data["password"] = "***" if account_data.get("password") else None

        if as_json:
            print_json(display_data)
            return

        console.print(f"\n[bold cyan]Account: {account_id}[/bold cyan]\n")
        console.print(f"  Tenant:          {account_data.get('tenant_id') or '-'}")
        console.print(f"  Host:            {account_data['host']}")
        console.print(f"  Port:            {account_data['port']}")
        console.print(f"  User:            {account_data.get('user') or '-'}")
        console.print(f"  Password:        {'***' if account_data.get('password') else '-'}")
        console.print(f"  Use TLS:         {'Yes' if account_data.get('use_tls') else 'No'}")
        console.print(f"  Batch Size:      {account_data.get('batch_size') or 'default'}")
        console.print(f"  Rate Limits:     hour={account_data.get('limit_per_hour') or 0}, day={account_data.get('limit_per_day') or 0}")
        console.print(f"  Created:         {account_data.get('created_at') or '-'}")
        console.print()

    @accounts.command("add")
    @click.argument("account_id", required=False)
    @click.option("--host", "-h", help="SMTP server hostname.")
    @click.option("--port", "-p", type=int, help="SMTP server port.")
    @click.option("--user", "-u", help="SMTP username.")
    @click.option("--password", help="SMTP password.")
    @click.option("--tls/--no-tls", default=None, help="Use TLS (STARTTLS on 587, implicit on 465).")
    @click.option("--batch-size", type=int, help="Max messages per dispatch cycle.")
    @click.option("--ttl", type=int, help="Connection TTL in seconds (default: 300).")
    @click.option("--limit-minute", type=int, help="Max emails per minute.")
    @click.option("--limit-hour", type=int, help="Max emails per hour.")
    @click.option("--limit-day", type=int, help="Max emails per day.")
    @click.option("--limit-behavior", type=click.Choice(["defer", "reject"]), help="Behavior when rate limit is hit.")
    def accounts_add(
        account_id: str | None,
        host: str | None,
        port: int | None,
        user: str | None,
        password: str | None,
        tls: bool | None,
        batch_size: int | None,
        ttl: int | None,
        limit_minute: int | None,
        limit_hour: int | None,
        limit_day: int | None,
        limit_behavior: str | None,
    ) -> None:
        """Add a new SMTP account to this tenant.

        Run without arguments for interactive mode.
        """
        # Interactive mode if account_id not provided
        if not account_id:
            console.print(f"\n[bold cyan]Add SMTP account for tenant '{tenant_id}'[/bold cyan]\n")
            account_id = click.prompt("Account ID")

        if not host:
            host = click.prompt("SMTP host")

        if port is None:
            port = click.prompt("SMTP port", type=int, default=587)

        if user is None:
            user = click.prompt("SMTP username", default="", show_default=False) or None

        if user and password is None:
            password = click.prompt("SMTP password", hide_input=True, default="", show_default=False) or None

        if tls is None:
            tls = click.confirm("Use TLS?", default=True)

        if batch_size is None:
            batch_size_str = click.prompt("Batch size (empty=default)", default="", show_default=False)
            batch_size = int(batch_size_str) if batch_size_str else None

        if ttl is None:
            ttl_str = click.prompt("Connection TTL in seconds", default="300", show_default=False)
            ttl = int(ttl_str) if ttl_str else 300

        if limit_minute is None:
            limit_minute_str = click.prompt("Rate limit per minute (0=unlimited)", default="0", show_default=False)
            limit_minute = int(limit_minute_str) if limit_minute_str else 0

        if limit_hour is None:
            limit_hour_str = click.prompt("Rate limit per hour (0=unlimited)", default="0", show_default=False)
            limit_hour = int(limit_hour_str) if limit_hour_str else 0

        if limit_day is None:
            limit_day_str = click.prompt("Rate limit per day (0=unlimited)", default="0", show_default=False)
            limit_day = int(limit_day_str) if limit_day_str else 0

        if limit_behavior is None:
            limit_behavior = click.prompt(
                "Limit behavior",
                type=click.Choice(["defer", "reject"]),
                default="defer"
            )

        persistence = _get_persistence_for_instance(instance_name)

        try:
            account_data = AccountCreate(
                id=account_id,
                tenant_id=tenant_id,
                host=host,
                port=port,
                user=user,
                password=password,
                use_tls=tls,
                batch_size=batch_size,
                ttl=ttl,
                limit_per_minute=limit_minute if limit_minute else None,
                limit_per_hour=limit_hour if limit_hour else None,
                limit_per_day=limit_day if limit_day else None,
                limit_behavior=limit_behavior,
            )
        except ValidationError as e:
            print_error(f"Validation error: {e}")
            sys.exit(1)

        async def _add():
            await persistence.init_db()
            # Verify tenant exists
            tenant_data = await persistence.get_tenant(tenant_id)
            if not tenant_data:
                return False, f"Tenant '{tenant_id}' not found."

            acc_dict = account_data.model_dump(exclude_none=True)
            await persistence.add_account(acc_dict)
            return True, None

        success, error = run_async(_add())

        if success:
            print_success(f"Account '{account_id}' created for tenant '{tenant_id}'.")
        else:
            print_error(error)
            sys.exit(1)

    @accounts.command("delete")
    @click.argument("account_id")
    @click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
    def accounts_delete(account_id: str, force: bool) -> None:
        """Delete an SMTP account."""
        persistence = _get_persistence_for_instance(instance_name)

        if not force and not click.confirm(f"Delete account '{account_id}' and all associated messages?"):
            console.print("Aborted.")
            return

        async def _delete():
            await persistence.init_db()
            # Verify account exists for this tenant
            try:
                await persistence.get_account(tenant_id, account_id)
            except ValueError:
                return False, f"Account '{account_id}' not found for tenant '{tenant_id}'."

            await persistence.delete_account(tenant_id, account_id)
            return True, None

        success, error = run_async(_delete())

        if success:
            print_success(f"Account '{account_id}' deleted.")
        else:
            print_error(error)
            sys.exit(1)


# ============================================================================
# MESSAGES commands (tenant-level)
# ============================================================================

def _add_messages_commands(group: click.Group, instance_name: str, tenant_id: str) -> None:
    """Add messages management commands for a tenant."""

    def _do_messages_list(
        account: str | None = None,
        status: str = "all",
        limit: int = 50,
        as_json: bool = False,
        as_csv: bool = False,
    ) -> None:
        """Internal function to list messages."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _list():
            await persistence.init_db()
            messages = await persistence.list_messages(tenant_id=tenant_id)

            # Filter by account if specified
            if account:
                messages = [m for m in messages if m.get("account_id") == account]

            # Filter by status
            if status != "all":
                filtered = []
                for m in messages:
                    if status == "pending" and not m.get("smtp_ts") and not m.get("error_ts") or status == "sent" and m.get("smtp_ts") or status == "error" and m.get("error_ts"):
                        filtered.append(m)
                messages = filtered

            return messages[:limit]

        msg_list = run_async(_list())

        if as_json:
            print_json(msg_list)
            return

        if as_csv:
            _output_messages_csv(msg_list)
            return

        if not msg_list:
            console.print("[dim]No messages found.[/dim]")
            return

        table = Table(title=f"Messages (tenant: {tenant_id}, showing up to {limit})")
        table.add_column("PK", style="dim")
        table.add_column("ID", style="cyan")
        table.add_column("Tenant ID", style="magenta")
        table.add_column("Account ID")
        table.add_column("Status")
        table.add_column("Subject", max_width=30)
        table.add_column("Created")

        for msg in msg_list[:limit]:
            if msg.get("error_ts"):
                msg_status = "[red]error[/red]"
            elif msg.get("smtp_ts"):
                msg_status = "[green]sent[/green]"
            elif msg.get("deferred_ts"):
                msg_status = "[yellow]deferred[/yellow]"
            else:
                msg_status = "[blue]pending[/blue]"

            subject = msg.get("message", {}).get("subject", "-")[:30]

            table.add_row(
                msg.get("pk") or "-",
                msg["id"],
                msg.get("tenant_id") or "-",
                msg.get("account_id") or "-",
                msg_status,
                subject,
                msg.get("created_at") or "-",
            )

        console.print(table)

    def _output_messages_csv(msg_list: list[dict[str, Any]]) -> None:
        """Output messages as CSV to stdout."""
        if not msg_list:
            return

        output = io.StringIO()
        fieldnames = [
            "pk", "id", "tenant_id", "tenant_name", "account_id",
            "status", "priority", "batch_code",
            "from", "to", "subject",
            "created_at", "deferred_ts", "smtp_ts", "error_ts", "error",
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for msg in msg_list:
            # Determine status
            if msg.get("error_ts"):
                status = "error"
            elif msg.get("smtp_ts"):
                status = "sent"
            elif msg.get("deferred_ts"):
                status = "deferred"
            else:
                status = "pending"

            message_data = msg.get("message", {})
            to_field = message_data.get("to", [])
            if isinstance(to_field, list):
                to_field = ", ".join(to_field)

            writer.writerow({
                "pk": msg.get("pk") or "",
                "id": msg.get("id") or "",
                "tenant_id": msg.get("tenant_id") or "",
                "tenant_name": msg.get("tenant_name") or "",
                "account_id": msg.get("account_id") or "",
                "status": status,
                "priority": msg.get("priority", ""),
                "batch_code": msg.get("batch_code") or "",
                "from": message_data.get("from") or "",
                "to": to_field,
                "subject": message_data.get("subject") or "",
                "created_at": msg.get("created_at") or "",
                "deferred_ts": msg.get("deferred_ts") or "",
                "smtp_ts": msg.get("smtp_ts") or "",
                "error_ts": msg.get("error_ts") or "",
                "error": msg.get("error") or "",
            })

        print(output.getvalue(), end="")

    @group.group("messages", invoke_without_command=True)
    @click.pass_context
    def messages(ctx):
        """Manage messages for this tenant."""
        if ctx.invoked_subcommand is None:
            click.echo(ctx.get_help())

    @messages.command("list")
    @click.option("--account", "-a", help="Filter by account ID.")
    @click.option("--status", "-s", type=click.Choice(["pending", "sent", "error", "all"]), default="all",
                  help="Filter by status.")
    @click.option("--limit", "-l", type=int, default=50, help="Max messages to show.")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    @click.option("--csv", "as_csv", is_flag=True, help="Output as CSV.")
    def messages_list(account: str | None, status: str, limit: int, as_json: bool, as_csv: bool) -> None:
        """List messages for this tenant."""
        _do_messages_list(account, status, limit, as_json, as_csv)

    @messages.command("show")
    @click.argument("message_id")
    @click.option("--history", "-H", is_flag=True, help="Include event history.")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def messages_show(message_id: str, history: bool, as_json: bool) -> None:
        """Show details for a specific message."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _show():
            await persistence.init_db()
            messages = await persistence.list_messages(
                tenant_id=tenant_id, include_history=history
            )
            for m in messages:
                if m["id"] == message_id:
                    return m
            return None

        msg = run_async(_show())

        if not msg:
            print_error(f"Message '{message_id}' not found.")
            sys.exit(1)

        if as_json:
            print_json(msg)
            return

        console.print(f"\n[bold cyan]Message: {message_id}[/bold cyan]\n")
        console.print(f"  PK:          {msg.get('pk') or '-'}")
        console.print(f"  ID:          {msg.get('id') or '-'}")
        console.print(f"  Tenant ID:   {msg.get('tenant_id') or '-'}")
        console.print(f"  Tenant Name: {msg.get('tenant_name') or '-'}")
        console.print(f"  Account ID:  {msg.get('account_id') or '-'}")
        console.print(f"  Priority:    {msg.get('priority', 2)}")
        console.print(f"  Created:     {msg.get('created_at') or '-'}")
        console.print(f"  Deferred:    {msg.get('deferred_ts') or '-'}")
        console.print(f"  Sent:        {msg.get('smtp_ts') or '-'}")
        console.print(f"  Error:       {msg.get('error') or '-'}")

        if msg.get("message"):
            m = msg["message"]
            console.print("\n  [bold]Message Content:[/bold]")
            console.print(f"    From:      {m.get('from', '-')}")
            console.print(f"    To:        {m.get('to', '-')}")
            console.print(f"    Subject:   {m.get('subject', '-')}")

        if history and msg.get("history"):
            console.print("\n  [bold]Event History:[/bold]")
            for event in msg["history"]:
                event_ts = event.get("event_ts", "-")
                event_type = event.get("event_type", "-")
                description = event.get("description") or ""
                reported = "[green]✓[/green]" if event.get("reported_ts") else "[yellow]○[/yellow]"
                console.print(f"    {reported} [{event_ts}] {event_type}: {description}")

        console.print()

    @messages.command("delete")
    @click.argument("message_ids", nargs=-1, required=True)
    @click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
    def messages_delete(message_ids: tuple, force: bool) -> None:
        """Delete one or more messages."""
        persistence = _get_persistence_for_instance(instance_name)

        if not force and not click.confirm(f"Delete {len(message_ids)} message(s)?"):
            console.print("Aborted.")
            return

        async def _delete():
            await persistence.init_db()
            deleted = 0
            for mid in message_ids:
                if await persistence.delete_message(mid, tenant_id=tenant_id):
                    deleted += 1
            return deleted

        deleted = run_async(_delete())
        print_success(f"Deleted {deleted} of {len(message_ids)} message(s).")


# ============================================================================
# SEND command (tenant-level)
# ============================================================================

def _add_send_command(group: click.Group, instance_name: str, tenant_id: str) -> None:
    """Add send command for a tenant."""

    @group.command("send")
    @click.argument("file", type=click.Path(exists=True))
    @click.option("--account", "-a", help="Account ID to use (default: first available).")
    @click.option("--priority", "-p", type=int, default=2, help="Priority (1=high, 2=normal, 3=low).")
    def send_cmd(file: str, account: str | None, priority: int) -> None:
        """Send an email from a .eml file.

        Example:

            mail-proxy myserver acme send email.eml

            mail-proxy myserver acme send email.eml --account smtp1
        """
        import email

        persistence = _get_persistence_for_instance(instance_name)

        # Read and parse the .eml file
        eml_path = Path(file)
        with open(eml_path, "rb") as f:
            msg = email.message_from_binary_file(f)

        async def _send():
            await persistence.init_db()

            # Get account to use
            accounts = await persistence.list_accounts(tenant_id=tenant_id)
            if not accounts:
                return False, f"No accounts found for tenant '{tenant_id}'."

            if account:
                acc = next((a for a in accounts if a["id"] == account), None)
                if not acc:
                    return False, f"Account '{account}' not found for tenant '{tenant_id}'."
                account_id = acc["id"]
            else:
                account_id = accounts[0]["id"]

            # Extract message details
            from_addr = msg.get("From", "")
            to_addr = msg.get("To", "")
            subject = msg.get("Subject", "")

            # Get body
            body_text = None
            body_html = None
            if msg.is_multipart():
                for part in msg.walk():
                    content_type = part.get_content_type()
                    if content_type == "text/plain" and body_text is None:
                        body_text = part.get_payload(decode=True).decode("utf-8", errors="replace")
                    elif content_type == "text/html" and body_html is None:
                        body_html = part.get_payload(decode=True).decode("utf-8", errors="replace")
            else:
                payload = msg.get_payload(decode=True)
                if payload:
                    if msg.get_content_type() == "text/html":
                        body_html = payload.decode("utf-8", errors="replace")
                    else:
                        body_text = payload.decode("utf-8", errors="replace")

            # Create message
            message_data = {
                "account_id": account_id,
                "priority": priority,
                "message": {
                    "from": from_addr,
                    "to": [to_addr] if isinstance(to_addr, str) else to_addr,
                    "subject": subject,
                    "body_text": body_text,
                    "body_html": body_html,
                },
            }

            message_id = await persistence.add_message(message_data)
            return True, message_id

        success, result = run_async(_send())

        if success:
            print_success(f"Message queued with ID: {result}")
        else:
            print_error(result)
            sys.exit(1)


if __name__ == "__main__":
    main()
