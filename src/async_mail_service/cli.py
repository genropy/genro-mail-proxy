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
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import click
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from async_mail_service.models import (
    AccountCreate,
    DEFAULT_ATTACHMENT_PATH,
    DEFAULT_SYNC_PATH,
    TenantCreate,
    TenantAuth,
    TenantRateLimits,
)
from async_mail_service.persistence import Persistence

console = Console()
err_console = Console(stderr=True)


def get_persistence(db_path: str) -> Persistence:
    """Create a Persistence instance with the given database path."""
    return Persistence(db_path)


def run_async(coro):
    """Run an async coroutine synchronously."""
    return asyncio.run(coro)


def print_error(message: str) -> None:
    """Print an error message to stderr."""
    err_console.print(f"[red]Error:[/red] {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[green]✓[/green] {message}")


def print_json(data: Any) -> None:
    """Print data as formatted JSON."""
    console.print_json(json.dumps(data, indent=2, default=str))


# ============================================================================
# Instance utilities
# ============================================================================

def _get_instance_dir(name: str) -> Path:
    """Get the instance directory path."""
    return Path.home() / ".mail-proxy" / name


def _get_pid_file(name: str) -> Path:
    """Get the PID file path for an instance."""
    return _get_instance_dir(name) / "server.pid"


def _get_db_path(name: str) -> str:
    """Get database path for an instance."""
    return str(_get_instance_dir(name) / "mail_service.db")


def _is_instance_running(name: str) -> tuple[bool, Optional[int], Optional[int]]:
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
    """Write PID file for an instance."""
    pid_file = _get_pid_file(name)
    pid_file.write_text(json.dumps({
        "pid": pid,
        "port": port,
        "host": host,
        "started_at": datetime.now().isoformat(),
    }, indent=2))


def _remove_pid_file(name: str) -> None:
    """Remove PID file for an instance."""
    pid_file = _get_pid_file(name)
    if pid_file.exists():
        pid_file.unlink()


def _generate_api_token() -> str:
    """Generate a secure random API token."""
    import secrets
    return secrets.token_urlsafe(32)


DEFAULT_CONFIG_TEMPLATE = """\
# genro-mail-proxy configuration
# Generated automatically - edit as needed

[server]
# Instance name for identification
name = {name}

# Database path
db_path = {db_path}

# Server binding
host = {host}
port = {port}

# API token for authentication (auto-generated)
api_token = {api_token}

[scheduler]
# Start scheduler active (true/false)
start_active = true

# Dispatch loop interval in seconds
send_loop_interval = 0.5

# Messages per account per dispatch cycle
batch_size_per_account = 50

[retry]
# Maximum retry attempts for temporary failures
max_retries = 5

# Retry delays in seconds (comma-separated)
retry_delays = 60, 300, 900, 3600, 7200

[attachments]
# Base directory for relative filesystem paths
# base_dir = /var/mail-proxy/attachments

# Default HTTP endpoint for @params paths
# default_endpoint = https://api.example.com/attachments
# auth_method = bearer
# auth_token =

# Cache settings (uncomment to enable)
# cache_disk_dir = /var/mail-proxy/cache
# cache_memory_max_mb = 50
# cache_disk_max_mb = 500

[client_sync]
# Base URL for delivery report callbacks
# client_base_url = https://api.example.com

# Authentication (bearer or basic)
# auth_method = bearer
# auth_token =
# auth_user =
# auth_password =
"""


def _ensure_instance(name: str, port: int = 8000, host: str = "0.0.0.0") -> Dict[str, Any]:
    """Ensure instance exists, creating with defaults if needed.

    Returns the instance config.
    """
    config_dir = _get_instance_dir(name)
    config_file = config_dir / "config.ini"
    db_path = str(config_dir / "mail_service.db")

    if not config_file.exists():
        # Create directory if needed
        config_dir.mkdir(parents=True, exist_ok=True)

        # Generate API token automatically
        api_token = _generate_api_token()

        # Generate default config
        config_content = DEFAULT_CONFIG_TEMPLATE.format(
            name=name,
            db_path=db_path,
            port=port,
            host=host,
            api_token=api_token,
        )
        config_file.write_text(config_content)
        console.print(f"[green]Created new instance:[/green] {name}")
        console.print(f"  Config: {config_file}")
        console.print(f"  API Token: {api_token}")

    return _get_instance_config(name)


def _get_instance_config(name: str) -> Optional[Dict[str, Any]]:
    """Read instance configuration from config.ini."""
    import configparser

    config_file = _get_instance_dir(name) / "config.ini"
    if not config_file.exists():
        return None

    config = configparser.ConfigParser()
    config.read(config_file)

    return {
        "name": config.get("server", "name", fallback=name),
        "db_path": config.get("server", "db_path", fallback=str(_get_instance_dir(name) / "mail_service.db")),
        "host": config.get("server", "host", fallback="0.0.0.0"),
        "port": config.getint("server", "port", fallback=8000),
        "api_token": config.get("server", "api_token", fallback=None) or None,
        "config_file": str(config_file),
    }


def _get_persistence_for_instance(name: str) -> Persistence:
    """Get persistence instance for a given instance name."""
    config = _get_instance_config(name)
    if not config:
        print_error(f"Instance '{name}' not found. Use 'mail-proxy {name} serve start' to create it.")
        sys.exit(1)
    return Persistence(config["db_path"])


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
@click.version_option()
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
    import configparser

    mail_proxy_dir = Path.home() / ".mail-proxy"

    if not mail_proxy_dir.exists():
        console.print("[dim]No instances configured.[/dim]")
        console.print("Use 'mail-proxy <name> serve start' to create one.")
        return

    # Find all instance directories (those with config.ini)
    instances = []
    for item in mail_proxy_dir.iterdir():
        if item.is_dir():
            config_file = item / "config.ini"
            if config_file.exists():
                # Parse config to get details
                config = configparser.ConfigParser()
                config.read(config_file)

                instance_name = item.name
                port = config.getint("server", "port", fallback=8000)
                host = config.get("server", "host", fallback="0.0.0.0")

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
        console.print("Use 'mail-proxy <name> serve start' to create one.")
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
def start_instance(instance_name: str, host: Optional[str], port: Optional[int], reload: bool, background: bool) -> None:
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
    _add_tenants_commands(instance_group, instance_name)
    _add_stats_command(instance_group, instance_name)
    _add_connect_command(instance_group, instance_name)
    _add_token_command(instance_group, instance_name)

    return instance_group


# ============================================================================
# SERVE implementation functions (shared by top-level and nested commands)
# ============================================================================

def _do_start_instance(instance_name: str, host: Optional[str], port: Optional[int],
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
        # New instance - use provided values or defaults
        host = host or "0.0.0.0"
        port = port or 8000
        instance_config = _ensure_instance(instance_name, port, host)
    else:
        # Existing instance - use config values, allow override
        host = host or instance_config["host"]
        port = port or instance_config["port"]

    config_path = instance_config["config_file"]
    db_path = instance_config["db_path"]

    if background:
        # Start in background
        console.print(f"[bold cyan]Starting {instance_name} in background...[/bold cyan]")
        cmd = ["mail-proxy", "start", instance_name, "--host", host, "--port", str(port)]
        if reload:
            cmd.append("--reload")

        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
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

    # Set environment variables for config (used by server.py)
    os.environ["GMP_CONFIG_FILE"] = config_path
    os.environ["GMP_INSTANCE_NAME"] = instance_name
    os.environ["GMP_DB_PATH"] = db_path
    os.environ["GMP_PORT"] = str(port)
    os.environ["GMP_HOST"] = host

    console.print(f"\n[bold cyan]Starting {instance_name}[/bold cyan]")
    console.print(f"  Config:  {config_path}")
    console.print(f"  DB:      {db_path}")
    console.print(f"  Listen:  {host}:{port}")
    console.print()

    # Write PID file before starting uvicorn
    _write_pid_file(instance_name, os.getpid(), port, host)

    try:
        uvicorn.run(
            "async_mail_service.server:app",
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
    console.print(f"  Config:  {config['config_file']}")
    console.print(f"  DB:      {config['db_path']}")
    console.print()


def _do_restart_instance(instance_name: str, force: bool, reload: bool) -> None:
    """Restart an instance."""
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

    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
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
            console.print(f"  Auth Method:     {auth.get('method', 'none')}")

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
        tenant_id: Optional[str],
        name: Optional[str],
        base_url: Optional[str],
        sync_path: Optional[str],
        attachment_path: Optional[str],
        auth_method: Optional[str],
        auth_token: Optional[str],
        auth_user: Optional[str],
        auth_password: Optional[str],
        rate_limit_hourly: Optional[int],
        rate_limit_daily: Optional[int],
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
        name: Optional[str],
        base_url: Optional[str],
        sync_path: Optional[str],
        attachment_path: Optional[str],
        auth_method: Optional[str],
        auth_token: Optional[str],
        auth_user: Optional[str],
        auth_password: Optional[str],
        rate_limit_hourly: Optional[int],
        rate_limit_daily: Optional[int],
        active: Optional[bool],
    ) -> None:
        """Update an existing tenant."""
        persistence = _get_persistence_for_instance(instance_name)

        updates: Dict[str, Any] = {}

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

        if not force:
            if not click.confirm(f"Delete tenant '{tenant_id}' and all associated data?"):
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

            pending = sum(1 for m in messages if not m.get("sent_ts") and not m.get("error_ts"))
            sent = sum(1 for m in messages if m.get("sent_ts"))
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
        console.print(f"  Messages:")
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
    def connect_cmd(token: Optional[str]) -> None:
        """Connect to this instance with an interactive REPL."""
        import code
        import readline  # noqa: F401
        import rlcompleter  # noqa: F401

        from async_mail_service.client import MailProxyClient, connect as client_connect
        from async_mail_service.forms import (
            new_tenant,
            new_account,
            new_message,
            set_proxy,
            TenantForm,
            AccountForm,
            MessageForm,
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
# TOKEN command
# ============================================================================

def _add_token_command(group: click.Group, instance_name: str) -> None:
    """Add token command."""

    @group.command("token")
    @click.option("--regenerate", "-r", is_flag=True, help="Generate a new token.")
    def token_cmd(regenerate: bool) -> None:
        """Show or regenerate the API token for this instance."""
        import configparser

        config_dir = _get_instance_dir(instance_name)
        config_file = config_dir / "config.ini"

        if not config_file.exists():
            print_error(f"Instance '{instance_name}' not found.")
            sys.exit(1)

        config = configparser.ConfigParser()
        config.read(config_file)

        if regenerate:
            new_token = _generate_api_token()

            if not config.has_section("server"):
                config.add_section("server")
            config.set("server", "api_token", new_token)

            with open(config_file, "w") as f:
                config.write(f)

            console.print(f"[green]Token regenerated for instance:[/green] {instance_name}")
            console.print(f"[yellow]Note:[/yellow] Restart the instance for the new token to take effect.")
            console.print(f"\n{new_token}")
        else:
            token_value = config.get("server", "api_token", fallback=None)

            if not token_value or token_value.strip() == "":
                console.print(f"[yellow]No API token configured for instance:[/yellow] {instance_name}")
                console.print("Use --regenerate to generate one.")
                sys.exit(1)

            console.print(token_value.strip())


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
            console.print(f"  Auth Method:     {auth.get('method', 'none')}")

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
        table.add_column("Host")
        table.add_column("Port", justify="right")
        table.add_column("User")
        table.add_column("TLS", justify="center")

        for acc in account_list:
            tls = "[green]✓[/green]" if acc.get("use_tls") else "[dim]-[/dim]"
            table.add_row(
                acc["id"],
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
                return await persistence.get_account(account_id)
            except ValueError:
                return None

        account_data = run_async(_show())

        if not account_data:
            print_error(f"Account '{account_id}' not found.")
            sys.exit(1)

        # Verify it belongs to this tenant
        if account_data.get("tenant_id") != tenant_id:
            print_error(f"Account '{account_id}' does not belong to tenant '{tenant_id}'.")
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
    @click.option("--tls/--no-tls", default=None, help="Use STARTTLS (default: yes).")
    @click.option("--ssl/--no-ssl", default=None, help="Use SSL/TLS connection.")
    @click.option("--batch-size", type=int, help="Max messages per dispatch cycle.")
    @click.option("--ttl", type=int, help="Connection TTL in seconds (default: 300).")
    @click.option("--limit-minute", type=int, help="Max emails per minute.")
    @click.option("--limit-hour", type=int, help="Max emails per hour.")
    @click.option("--limit-day", type=int, help="Max emails per day.")
    @click.option("--limit-behavior", type=click.Choice(["defer", "reject"]), help="Behavior when rate limit is hit.")
    def accounts_add(
        account_id: Optional[str],
        host: Optional[str],
        port: Optional[int],
        user: Optional[str],
        password: Optional[str],
        tls: Optional[bool],
        ssl: Optional[bool],
        batch_size: Optional[int],
        ttl: Optional[int],
        limit_minute: Optional[int],
        limit_hour: Optional[int],
        limit_day: Optional[int],
        limit_behavior: Optional[str],
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
            tls = click.confirm("Use STARTTLS?", default=True)

        if ssl is None:
            ssl = click.confirm("Use SSL/TLS connection?", default=False)

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
                use_ssl=ssl,
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

        if not force:
            if not click.confirm(f"Delete account '{account_id}' and all associated messages?"):
                console.print("Aborted.")
                return

        async def _delete():
            await persistence.init_db()
            # Verify it belongs to this tenant
            try:
                acc = await persistence.get_account(account_id)
                if acc and acc.get("tenant_id") != tenant_id:
                    return False, f"Account '{account_id}' does not belong to tenant '{tenant_id}'."
            except ValueError:
                return False, f"Account '{account_id}' not found."

            await persistence.delete_account(account_id)
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
        account: Optional[str] = None,
        status: str = "all",
        limit: int = 50,
        as_json: bool = False
    ) -> None:
        """Internal function to list messages."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _list():
            await persistence.init_db()
            messages = await persistence.list_messages()

            # Filter by tenant
            tenant_accounts = await persistence.list_accounts(tenant_id=tenant_id)
            account_ids = {a["id"] for a in tenant_accounts}
            messages = [m for m in messages if m.get("account_id") in account_ids]

            # Filter by account if specified
            if account:
                messages = [m for m in messages if m.get("account_id") == account]

            # Filter by status
            if status != "all":
                filtered = []
                for m in messages:
                    if status == "pending" and not m.get("sent_ts") and not m.get("error_ts"):
                        filtered.append(m)
                    elif status == "sent" and m.get("sent_ts"):
                        filtered.append(m)
                    elif status == "error" and m.get("error_ts"):
                        filtered.append(m)
                messages = filtered

            return messages[:limit]

        msg_list = run_async(_list())

        if as_json:
            print_json(msg_list)
            return

        if not msg_list:
            console.print("[dim]No messages found.[/dim]")
            return

        table = Table(title=f"Messages (tenant: {tenant_id}, showing up to {limit})")
        table.add_column("ID", style="cyan", max_width=20)
        table.add_column("Account")
        table.add_column("Status")
        table.add_column("Subject", max_width=30)
        table.add_column("Created")

        for msg in msg_list[:limit]:
            if msg.get("error_ts"):
                msg_status = "[red]error[/red]"
            elif msg.get("sent_ts"):
                msg_status = "[green]sent[/green]"
            elif msg.get("deferred_ts"):
                msg_status = "[yellow]deferred[/yellow]"
            else:
                msg_status = "[blue]pending[/blue]"

            subject = msg.get("message", {}).get("subject", "-")[:30]

            table.add_row(
                msg["id"][:20] + "..." if len(msg["id"]) > 20 else msg["id"],
                msg.get("account_id") or "-",
                msg_status,
                subject,
                msg.get("created_at") or "-",
            )

        console.print(table)

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
    def messages_list(account: Optional[str], status: str, limit: int, as_json: bool) -> None:
        """List messages for this tenant."""
        _do_messages_list(account, status, limit, as_json)

    @messages.command("show")
    @click.argument("message_id")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def messages_show(message_id: str, as_json: bool) -> None:
        """Show details for a specific message."""
        persistence = _get_persistence_for_instance(instance_name)

        async def _show():
            await persistence.init_db()
            messages = await persistence.list_messages()
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
        console.print(f"  Account:     {msg.get('account_id') or '-'}")
        console.print(f"  Priority:    {msg.get('priority', 2)}")
        console.print(f"  Created:     {msg.get('created_at') or '-'}")
        console.print(f"  Deferred:    {msg.get('deferred_ts') or '-'}")
        console.print(f"  Sent:        {msg.get('sent_ts') or '-'}")
        console.print(f"  Error:       {msg.get('error') or '-'}")

        if msg.get("message"):
            m = msg["message"]
            console.print(f"\n  [bold]Message Content:[/bold]")
            console.print(f"    From:      {m.get('from', '-')}")
            console.print(f"    To:        {m.get('to', '-')}")
            console.print(f"    Subject:   {m.get('subject', '-')}")

        console.print()

    @messages.command("delete")
    @click.argument("message_ids", nargs=-1, required=True)
    @click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
    def messages_delete(message_ids: tuple, force: bool) -> None:
        """Delete one or more messages."""
        persistence = _get_persistence_for_instance(instance_name)

        if not force:
            if not click.confirm(f"Delete {len(message_ids)} message(s)?"):
                console.print("Aborted.")
                return

        async def _delete():
            await persistence.init_db()
            deleted = 0
            for mid in message_ids:
                if await persistence.delete_message(mid):
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
    def send_cmd(file: str, account: Optional[str], priority: int) -> None:
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
