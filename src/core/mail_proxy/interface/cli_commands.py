# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Special CLI commands not derived from endpoint introspection.

This module provides CLI commands that don't map directly to REST API
endpoints. These are administrative and utility commands requiring
special handling (interactive sessions, file I/O, server communication).

Components:
    add_connect_command: Interactive Python REPL with pre-configured client.
    add_stats_command: Display aggregate queue statistics.
    add_send_command: Queue email from .eml file.
    add_token_command: API token management (show/regenerate).
    add_run_now_command: Trigger immediate dispatch cycle via HTTP.
    add_list_command: List all configured instances with status.
    add_stop_command: Stop running instances.
    add_restart_command: Restart running instances.

Instance Management:
    Instances are stored in ~/.mail-proxy/<name>/ with config.ini files.
    The list/stop/restart commands manage these instances by tracking
    PID files for process management.

Example:
    Add special commands to CLI group::

        from core.mail_proxy.interface.cli_commands import (
            add_connect_command,
            add_stats_command,
            add_send_command,
            add_list_command,
            add_stop_command,
        )

        @click.group()
        def cli():
            pass

        add_connect_command(cli, get_url, get_token, "myinstance")
        add_stats_command(cli, db)
        add_send_command(cli, db, "tenant1")
        add_list_command(cli)
        add_stop_command(cli)

    Run commands::

        mail-proxy myinstance connect
        mail-proxy myinstance stats --json
        mail-proxy myinstance tenant1 send email.eml
        mail-proxy list
        mail-proxy stop myserver

Note:
    These commands are registered separately from endpoint-derived
    commands because they require special parameters (callbacks,
    file paths) or interactive behavior not suitable for introspection.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click
from rich.console import Console

if TYPE_CHECKING:
    from core.mail_proxy.mailproxy_db import MailProxyDb

console = Console()


def _run_async(coro: Any) -> Any:
    """Run async coroutine in synchronous Click command context."""
    return asyncio.run(coro)


def add_connect_command(
    group: click.Group,
    get_url: Callable[[], str],
    get_token: Callable[[], str | None],
    instance_name: str,
) -> None:
    """Register 'connect' command for interactive Python REPL.

    Creates a REPL session with a pre-configured MailProxyClient
    for interactive server administration and debugging.

    Args:
        group: Click group to register command on.
        get_url: Callback returning server URL (from instance config).
        get_token: Callback returning API token (from instance config).
        instance_name: Instance name for display and client configuration.

    Example:
        ::

            mail-proxy myserver connect
            mail-proxy myserver connect --url http://remote:8000 --token secret

            # In REPL:
            >>> proxy.status()
            >>> proxy.messages.list(tenant_id="acme")
    """

    @group.command("connect")
    @click.option("--token", "-t", envvar="GMP_API_TOKEN", help="API token for authentication.")
    @click.option("--url", "-u", help="Server URL (default: auto-detect from running instance).")
    def connect_cmd(token: str | None, url: str | None) -> None:
        """Connect to this instance with an interactive REPL.

        Opens a Python REPL with a pre-configured proxy client for
        interacting with the mail-proxy server.

        Example:
            mail-proxy myserver connect
            mail-proxy myserver connect --url http://remote:8000 --token secret
        """
        import code

        try:
            import readline  # noqa: F401
            import rlcompleter  # noqa: F401
        except ImportError:
            pass  # readline not available on all platforms

        from tools.http_client import MailProxyClient
        from tools.http_client import connect as client_connect
        from tools.repl import repl_wrap

        # Get URL and token
        server_url = url or get_url()
        api_token = token or get_token()

        if not server_url:
            console.print("[red]Error:[/red] Cannot determine server URL.")
            console.print("[dim]Either start the server or specify --url[/dim]")
            sys.exit(1)

        try:
            proxy = client_connect(server_url, token=api_token, name=instance_name)

            if not proxy.health():
                console.print(f"[red]Error:[/red] Cannot connect to {instance_name} ({server_url})")
                console.print("[dim]Make sure the server is running.[/dim]")
                return

            console.print(f"\n[bold green]Connected to {instance_name}[/bold green]")
            console.print(f"  URL: {server_url}")
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
            console.print("[dim]Type 'exit()' or Ctrl+D to quit.[/dim]")
            console.print()

            namespace = {
                "proxy": repl_wrap(proxy),
                "MailProxyClient": MailProxyClient,
                "console": console,
            }

            code.interact(banner="", local=namespace, exitmsg="Goodbye!")

        except Exception as e:
            console.print(f"[red]Error:[/red] Connection failed: {e}")
            sys.exit(1)


def add_stats_command(
    group: click.Group,
    db: MailProxyDb,
) -> None:
    """Register 'stats' command for aggregate queue statistics.

    Displays tenant/account/message counts with breakdown by status.

    Args:
        group: Click group to register command on.
        db: Database instance for querying statistics.

    Example:
        ::

            mail-proxy myserver stats
            mail-proxy myserver stats --json
    """

    @group.command("stats")
    @click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
    def stats_cmd(as_json: bool) -> None:
        """Show queue statistics for this instance."""

        async def _stats() -> dict[str, Any]:
            tenants = await db.table("tenants").list_all()
            accounts = await db.table("accounts").list_all()

            all_messages: list[dict] = []
            for tenant in tenants:
                tenant_messages = await db.table("messages").list_all(tenant["id"])
                all_messages.extend(tenant_messages)

            pending = sum(1 for m in all_messages if not m.get("smtp_ts") and not m.get("error_ts"))
            sent = sum(1 for m in all_messages if m.get("smtp_ts"))
            errors = sum(1 for m in all_messages if m.get("error_ts"))

            return {
                "tenants": len(tenants),
                "accounts": len(accounts),
                "messages": {
                    "total": len(all_messages),
                    "pending": pending,
                    "sent": sent,
                    "error": errors,
                },
            }

        data = _run_async(_stats())

        if as_json:
            click.echo(json.dumps(data, indent=2))
            return

        console.print("\n[bold]Queue Statistics[/bold]\n")
        console.print(f"  Tenants:    {data['tenants']}")
        console.print(f"  Accounts:   {data['accounts']}")
        console.print("  Messages:")
        console.print(f"    Total:    {data['messages']['total']}")
        console.print(f"    Pending:  {data['messages']['pending']}")
        console.print(f"    Sent:     {data['messages']['sent']}")
        console.print(f"    Errors:   {data['messages']['error']}")
        console.print()


def add_send_command(
    group: click.Group,
    db: MailProxyDb,
    tenant_id: str,
) -> None:
    """Register 'send' command to queue email from .eml file.

    Parses RFC 5322 email file and queues for delivery.

    Args:
        group: Click group to register command on.
        db: Database instance for message operations.
        tenant_id: Tenant context for the send operation.

    Example:
        ::

            mail-proxy myserver acme send email.eml
            mail-proxy myserver acme send email.eml --account smtp1 --priority 1
    """

    @group.command("send")
    @click.argument("file", type=click.Path(exists=True))
    @click.option("--account", "-a", help="Account ID to use (default: first available).")
    @click.option(
        "--priority", "-p", type=int, default=2, help="Priority (1=high, 2=normal, 3=low)."
    )
    def send_cmd(file: str, account: str | None, priority: int) -> None:
        """Send an email from a .eml file.

        Example:
            mail-proxy myserver acme send email.eml
            mail-proxy myserver acme send email.eml --account smtp1
        """
        import email

        eml_path = Path(file)
        with open(eml_path, "rb") as f:
            msg = email.message_from_binary_file(f)

        async def _send() -> tuple[bool, str]:
            accounts = await db.table("accounts").list_all(tenant_id=tenant_id)
            if not accounts:
                return False, f"No accounts found for tenant '{tenant_id}'."

            if account:
                acc = next((a for a in accounts if a["id"] == account), None)
                if not acc:
                    return False, f"Account '{account}' not found for tenant '{tenant_id}'."
                account_id = acc["id"]
            else:
                account_id = accounts[0]["id"]

            from_addr = msg.get("From", "")
            to_addr = msg.get("To", "")
            subject = msg.get("Subject", "")

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

            message_id = await db.table("messages").add(tenant_id, message_data)
            return True, message_id

        success, result = _run_async(_send())

        if success:
            console.print(f"[green]Message queued with ID:[/green] {result}")
        else:
            console.print(f"[red]Error:[/red] {result}")
            sys.exit(1)


def add_token_command(
    group: click.Group,
    db: MailProxyDb,
) -> None:
    """Register 'token' command for API token management.

    Shows current token or regenerates a new one.

    Args:
        group: Click group to register command on.
        db: Database instance for token storage.

    Example:
        ::

            mail-proxy myserver token
            mail-proxy myserver token --regenerate
    """

    @group.command("token")
    @click.option("--regenerate", "-r", is_flag=True, help="Generate a new token.")
    def token_cmd(regenerate: bool) -> None:
        """Show or regenerate the API token for this instance."""
        import secrets

        async def _token() -> tuple[str | None, bool]:
            instance_table = db.table("instance")
            if regenerate:
                new_token = secrets.token_urlsafe(32)
                await instance_table.set_config("api_token", new_token)
                return new_token, True
            return await instance_table.get_config("api_token"), False

        token, is_new = _run_async(_token())

        if is_new:
            console.print("[green]Token regenerated.[/green]")
            console.print(
                "[yellow]Note:[/yellow] Restart the instance for the new token to take effect."
            )
            console.print(f"\n{token}")
        else:
            if not token:
                console.print("[yellow]No API token configured.[/yellow]")
                console.print("Use --regenerate to generate one.")
                sys.exit(1)
            click.echo(token)


def add_run_now_command(
    group: click.Group,
    get_url: Callable[[], str],
    get_token: Callable[[], str | None],
    tenant_id: str | None = None,
) -> None:
    """Register 'run-now' command to trigger immediate dispatch.

    Sends HTTP POST to running server to force dispatch cycle.

    Args:
        group: Click group to register command on.
        get_url: Callback returning server URL.
        get_token: Callback returning API token.
        tenant_id: Optional tenant scope (None = all tenants).

    Example:
        ::

            mail-proxy myserver run-now
            mail-proxy myserver acme run-now
    """

    @group.command("run-now")
    def run_now_cmd() -> None:
        """Trigger immediate dispatch and sync cycle."""
        import httpx

        url = get_url()
        token = get_token()

        if not url:
            console.print("[red]Error:[/red] Server not running or URL not available.")
            sys.exit(1)

        try:
            headers = {"X-API-Token": token} if token else {}
            params = {"tenant_id": tenant_id} if tenant_id else {}

            with httpx.Client(timeout=10) as client:
                resp = client.post(
                    f"{url}/commands/run-now",
                    headers=headers,
                    params=params,
                )
                resp.raise_for_status()
                result = resp.json()

            if result.get("ok"):
                if tenant_id:
                    console.print(
                        f"[green]Dispatch cycle triggered for tenant '{tenant_id}'.[/green]"
                    )
                else:
                    console.print("[green]Dispatch cycle triggered.[/green]")
            else:
                console.print(f"[red]Error:[/red] Server returned: {result}")
        except httpx.HTTPError as e:
            console.print(f"[red]Error:[/red] Failed to trigger run-now: {e}")
            sys.exit(1)


# ============================================================================
# Instance management helpers
# ============================================================================

_MAIL_PROXY_DIR = Path.home() / ".mail-proxy"
_CURRENT_INSTANCE_FILE = _MAIL_PROXY_DIR / ".current"


def _get_instance_dir(name: str) -> Path:
    """Get the instance directory path (~/.mail-proxy/<name>/)."""
    return _MAIL_PROXY_DIR / name


def _list_instances() -> list[str]:
    """List all configured instance names."""
    if not _MAIL_PROXY_DIR.exists():
        return []
    return [
        item.name
        for item in _MAIL_PROXY_DIR.iterdir()
        if item.is_dir() and (
            (item / "config.ini").exists() or (item / "mail_service.db").exists()
        )
    ]


def _parse_context(value: str) -> tuple[str | None, str | None]:
    """Parse instance/tenant context string.

    Formats:
        "instance" -> (instance, None)
        "instance/tenant" -> (instance, tenant)
        "/tenant" -> (None, tenant)
        "instance/" -> (instance, None) - explicit no tenant

    Args:
        value: Context string to parse.

    Returns:
        (instance, tenant) tuple. None means "keep current" or "not specified".
    """
    if "/" in value:
        parts = value.split("/", 1)
        instance = parts[0] or None
        tenant = parts[1] or None
        return instance, tenant
    return value, None


def _get_current_context() -> tuple[str | None, str | None]:
    """Get current instance and tenant from .current file.

    Returns:
        (instance, tenant) tuple.
    """
    if not _CURRENT_INSTANCE_FILE.exists():
        return None, None
    content = _CURRENT_INSTANCE_FILE.read_text().strip()
    if not content:
        return None, None
    return _parse_context(content)


def _set_current_context(instance: str | None, tenant: str | None) -> None:
    """Set current instance and tenant in .current file.

    Args:
        instance: Instance name (required).
        tenant: Tenant name (optional).
    """
    if not instance:
        return
    _MAIL_PROXY_DIR.mkdir(parents=True, exist_ok=True)
    if tenant:
        _CURRENT_INSTANCE_FILE.write_text(f"{instance}/{tenant}")
    else:
        _CURRENT_INSTANCE_FILE.write_text(instance)


def resolve_context(
    explicit_instance: str | None = None,
    explicit_tenant: str | None = None,
) -> tuple[str | None, str | None]:
    """Resolve active instance and tenant using priority chain.

    Resolution order for instance:
        1. Explicit argument
        2. GMP_INSTANCE environment variable
        3. ~/.mail-proxy/.current file (instance part)
        4. Auto-select if only one instance exists

    Resolution order for tenant:
        1. Explicit argument
        2. GMP_TENANT environment variable
        3. ~/.mail-proxy/.current file (tenant part)

    Args:
        explicit_instance: Explicitly specified instance name.
        explicit_tenant: Explicitly specified tenant name.

    Returns:
        (instance, tenant) tuple. Either can be None.
    """
    import os

    # Resolve instance
    instance: str | None = None

    if explicit_instance:
        instance = explicit_instance
    else:
        env_instance = os.environ.get("GMP_INSTANCE")
        if env_instance:
            instance = env_instance
        else:
            current_instance, _ = _get_current_context()
            if current_instance:
                instance = current_instance
            else:
                instances = _list_instances()
                if len(instances) == 1:
                    instance = instances[0]

    # Resolve tenant
    tenant: str | None = None

    if explicit_tenant:
        tenant = explicit_tenant
    else:
        env_tenant = os.environ.get("GMP_TENANT")
        if env_tenant:
            tenant = env_tenant
        else:
            _, current_tenant = _get_current_context()
            if current_tenant:
                tenant = current_tenant

    return instance, tenant


def require_context(
    explicit_instance: str | None = None,
    explicit_tenant: str | None = None,
    require_tenant: bool = False,
) -> tuple[str, str | None]:
    """Resolve context or exit with error if ambiguous.

    Args:
        explicit_instance: Explicitly specified instance name.
        explicit_tenant: Explicitly specified tenant name.
        require_tenant: If True, tenant must be resolved.

    Returns:
        (instance, tenant) tuple.

    Raises:
        SystemExit: If required context cannot be resolved.
    """
    instance, tenant = resolve_context(explicit_instance, explicit_tenant)

    if not instance:
        instances = _list_instances()
        if not instances:
            console.print("[red]Error:[/red] No instances configured.")
            console.print("Use 'mail-proxy serve <name>' to create one.")
            sys.exit(1)

        console.print("[red]Error:[/red] Multiple instances found. Specify which one:")
        console.print()
        for name in sorted(instances):
            console.print(f"  • {name}")
        console.print()
        console.print("Options:")
        console.print("  • Use 'mail-proxy use <instance>' to set default")
        console.print("  • Use 'mail-proxy use <instance>/<tenant>' for full context")
        console.print("  • Set GMP_INSTANCE environment variable")
        sys.exit(1)

    if require_tenant and not tenant:
        console.print("[red]Error:[/red] Tenant required for this command.")
        console.print()
        console.print("Options:")
        console.print(f"  • Use 'mail-proxy use {instance}/<tenant>'")
        console.print("  • Set GMP_TENANT environment variable")
        sys.exit(1)

    return instance, tenant


# Keep backwards compatibility
def resolve_instance(explicit: str | None = None) -> str | None:
    """Resolve the active instance (backwards compatible wrapper)."""
    instance, _ = resolve_context(explicit_instance=explicit)
    return instance


def require_instance(explicit: str | None = None) -> str:
    """Resolve instance or exit (backwards compatible wrapper)."""
    instance, _ = require_context(explicit_instance=explicit)
    return instance


def _get_pid_file(name: str) -> Path:
    """Get the PID file path for an instance."""
    return _get_instance_dir(name) / "server.pid"


def _is_instance_running(name: str) -> tuple[bool, int | None, int | None]:
    """Check if an instance is running.

    Returns:
        (is_running, pid, port) tuple
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

        # Check if process is alive (signal 0 doesn't kill, just checks)
        os.kill(pid, 0)
        return True, pid, port
    except (json.JSONDecodeError, ProcessLookupError, PermissionError, OSError):
        return False, None, None


def _remove_pid_file(name: str) -> None:
    """Remove PID file for an instance."""
    pid_file = _get_pid_file(name)
    if pid_file.exists():
        pid_file.unlink()


def _stop_instance(name: str, signal_type: int = 15, timeout: float = 5.0, fallback_kill: bool = True) -> bool:
    """Stop a running instance by sending a signal.

    Args:
        name: Instance name.
        signal_type: Signal to send (15=SIGTERM, 9=SIGKILL).
        timeout: Seconds to wait for process to terminate.
        fallback_kill: If True, send SIGKILL if SIGTERM doesn't work.

    Returns:
        True if successfully stopped, False otherwise.
    """
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


def _get_instance_config(name: str) -> dict[str, Any] | None:
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
        "api_token": config.get("server", "api_token", fallback=""),
        "config_file": str(config_file),
    }


def _write_pid_file(name: str, pid: int, port: int, host: str) -> None:
    """Write PID file for an instance."""
    from datetime import datetime

    pid_file = _get_pid_file(name)
    pid_file.write_text(json.dumps({
        "pid": pid,
        "port": port,
        "host": host,
        "started_at": datetime.now().isoformat(),
    }, indent=2))


_DEFAULT_CONFIG_TEMPLATE = """\
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

# API token for authentication (auto-generated, change if needed)
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
"""


def _generate_api_token() -> str:
    """Generate a random API token."""
    import secrets
    return secrets.token_urlsafe(32)


def _ensure_instance_config(name: str, port: int, host: str) -> dict[str, Any]:
    """Ensure instance config exists, creating with defaults if needed.

    Returns the instance configuration dict.
    """
    config_dir = _get_instance_dir(name)
    config_file = config_dir / "config.ini"
    db_path = str(config_dir / "mail_service.db")

    if not config_file.exists():
        config_dir.mkdir(parents=True, exist_ok=True)

        api_token = _generate_api_token()
        config_content = _DEFAULT_CONFIG_TEMPLATE.format(
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

    return _get_instance_config(name) or {
        "name": name,
        "db_path": db_path,
        "host": host,
        "port": port,
        "api_token": "",
        "config_file": str(config_file),
    }


# ============================================================================
# Instance management commands
# ============================================================================


def add_serve_command(group: click.Group) -> None:
    """Register 'serve' command to start a mail-proxy server instance.

    Args:
        group: Click group to register command on.

    Example:
        ::

            mail-proxy serve                    # Start default-mailer
            mail-proxy serve myserver           # Start/create myserver
            mail-proxy serve myserver -p 8080   # Start on specific port
            mail-proxy serve myserver -c        # Start and open REPL
    """
    import os

    @group.command("serve")
    @click.argument("name", default="default-mailer")
    @click.option("--host", "-h", default=None, help="Host to bind to (default: 0.0.0.0).")
    @click.option("--port", "-p", type=int, default=None, help="Port to listen on (default: 8000).")
    @click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
    @click.option("--connect", "-c", is_flag=True, help="Start in background and open REPL.")
    @click.option("--foreground", "-f", is_flag=True, help="Run in foreground (default behavior).")
    def serve_cmd(
        name: str,
        host: str | None,
        port: int | None,
        reload: bool,
        connect: bool,
        foreground: bool,
    ) -> None:
        """Start a mail-proxy server instance.

        If the instance doesn't exist, creates it with default config.
        If already running, shows status and exits.

        NAME is the instance name (default: default-mailer).
        """
        import subprocess
        import time

        import uvicorn

        # Check if already running
        is_running, pid, running_port = _is_instance_running(name)
        if is_running:
            if connect:
                console.print(f"[dim]Instance '{name}' already running, connecting...[/dim]")
                # TODO: invoke connect command
                console.print(f"[yellow]Connect to:[/yellow] http://localhost:{running_port}")
                return
            console.print(f"[yellow]Instance '{name}' is already running[/yellow]")
            console.print(f"  PID:  {pid}")
            console.print(f"  Port: {running_port}")
            console.print(f"  URL:  http://localhost:{running_port}")
            sys.exit(0)

        # Get or create instance config
        instance_config = _get_instance_config(name)

        if instance_config is None:
            # New instance - use provided values or defaults
            effective_host: str = host or "0.0.0.0"
            effective_port: int = port or 8000
            instance_config = _ensure_instance_config(name, effective_port, effective_host)
        else:
            # Existing instance - use config values, allow override
            effective_host = host or instance_config["host"]
            effective_port = port or instance_config["port"]

        db_path: str = instance_config["db_path"]
        config_file: str = instance_config["config_file"]

        if connect:
            # Start in background and show connection info
            console.print(f"[bold cyan]Starting {name} in background...[/bold cyan]")

            cmd = ["mail-proxy", "serve", name, "--host", effective_host, "--port", str(effective_port)]
            if reload:
                cmd.append("--reload")

            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            # Wait for server to be ready
            for _ in range(50):  # Max 5 seconds
                time.sleep(0.1)
                is_running, pid, _ = _is_instance_running(name)
                if is_running:
                    break

            if is_running:
                console.print(f"  PID:  {pid}")
                console.print(f"  Port: {effective_port}")
                console.print(f"  URL:  http://localhost:{effective_port}")
                # TODO: invoke connect command or open REPL
            else:
                console.print("[red]Error:[/red] Failed to start server")
            return

        # Set environment variables for config (used by server.py)
        os.environ["GMP_CONFIG_FILE"] = config_file
        os.environ["GMP_INSTANCE_NAME"] = name
        os.environ["GMP_DB_PATH"] = db_path
        os.environ["GMP_PORT"] = str(effective_port)
        os.environ["GMP_HOST"] = effective_host

        console.print(f"\n[bold cyan]Starting {name}[/bold cyan]")
        console.print(f"  Config:  {config_file}")
        console.print(f"  DB:      {db_path}")
        console.print(f"  Listen:  {effective_host}:{effective_port}")
        console.print()

        # Write PID file before starting uvicorn
        _write_pid_file(name, os.getpid(), effective_port, effective_host)

        try:
            uvicorn.run(
                "core.mail_proxy.server:app",
                host=effective_host,
                port=effective_port,
                reload=reload,
                log_level="info",
            )
        finally:
            # Clean up PID file on exit
            _remove_pid_file(name)


def add_list_command(group: click.Group) -> None:
    """Register 'list' command to show all configured instances.

    Args:
        group: Click group to register command on.

    Example:
        ::

            mail-proxy list
    """
    from rich.table import Table

    @group.command("list")
    def list_cmd() -> None:
        """List mail-proxy instances with their status.

        Shows all instances in ~/.mail-proxy/ with running status.
        """
        import configparser

        mail_proxy_dir = Path.home() / ".mail-proxy"

        if not mail_proxy_dir.exists():
            console.print("[dim]No instances configured.[/dim]")
            console.print("Use 'mail-proxy serve <name>' to create one.")
            return

        instances = []
        for item in mail_proxy_dir.iterdir():
            if item.is_dir():
                config_file = item / "config.ini"
                db_file = item / "mail_service.db"
                instance_name = item.name

                # Check for config.ini (new format) or mail_service.db (legacy)
                if config_file.exists():
                    config = configparser.ConfigParser()
                    config.read(config_file)
                    port = config.getint("server", "port", fallback=8000)
                    host = config.get("server", "host", fallback="0.0.0.0")
                    is_legacy = False
                elif db_file.exists():
                    # Legacy instance: has database but no config.ini
                    port = 8000
                    host = "0.0.0.0"
                    is_legacy = True
                else:
                    # Neither config nor database - skip this directory
                    continue

                is_running, pid, running_port = _is_instance_running(instance_name)

                instances.append({
                    "name": instance_name,
                    "port": running_port or port,
                    "host": host,
                    "running": is_running,
                    "pid": pid,
                    "legacy": is_legacy,
                })

        if not instances:
            console.print("[dim]No instances configured.[/dim]")
            console.print("Use 'mail-proxy serve <name>' to create one.")
            return

        table = Table(title="Mail Proxy Instances")
        table.add_column("Name", style="cyan")
        table.add_column("Status")
        table.add_column("Port", justify="right")
        table.add_column("PID", justify="right")
        table.add_column("URL")
        table.add_column("Note")

        for inst in sorted(instances, key=lambda x: x["name"]):
            if inst["running"]:
                status = "[green]running[/green]"
                pid_str = str(inst["pid"])
                url = f"http://localhost:{inst['port']}"
            else:
                status = "[dim]stopped[/dim]"
                pid_str = "[dim]-[/dim]"
                url = "[dim]-[/dim]"

            note = "[yellow]legacy[/yellow]" if inst.get("legacy") else ""

            table.add_row(
                inst["name"],
                status,
                str(inst["port"]),
                pid_str,
                url,
                note,
            )

        console.print(table)


def add_stop_command(group: click.Group) -> None:
    """Register 'stop' command to stop running instances.

    Args:
        group: Click group to register command on.

    Example:
        ::

            mail-proxy stop              # Stop all running instances
            mail-proxy stop myserver     # Stop specific instance
            mail-proxy stop myserver -f  # Force kill
    """
    import signal as sig

    @group.command("stop")
    @click.argument("name", default="*")
    @click.option("--force", "-f", is_flag=True, help="Force kill (SIGKILL) instead of graceful shutdown.")
    def stop_cmd(name: str, force: bool) -> None:
        """Stop running mail-proxy instance(s).

        NAME can be an instance name or '*' to stop all.
        """
        signal_type = sig.SIGKILL if force else sig.SIGTERM
        signal_name = "SIGKILL" if force else "SIGTERM"

        if name == "*":
            mail_proxy_dir = Path.home() / ".mail-proxy"
            if not mail_proxy_dir.exists():
                console.print("[dim]No instances configured.[/dim]")
                return

            stopped = []
            for item in mail_proxy_dir.iterdir():
                if item.is_dir() and (item / "config.ini").exists():
                    instance_name = item.name
                    is_running, pid, _ = _is_instance_running(instance_name)
                    if is_running:
                        console.print(f"Stopping {instance_name} (PID {pid})... ", end="")
                        if _stop_instance(instance_name, signal_type):
                            console.print("[green]stopped[/green]")
                            stopped.append(instance_name)
                        else:
                            console.print(f"[yellow]sent {signal_name}[/yellow]")

            if not stopped:
                console.print("[dim]No running instances found.[/dim]")
            else:
                console.print(f"\n[green]Stopped {len(stopped)} instance(s)[/green]")
        else:
            is_running, pid, _ = _is_instance_running(name)
            if not is_running:
                console.print(f"[dim]Instance '{name}' is not running.[/dim]")
                return

            console.print(f"Stopping {name} (PID {pid})... ", end="")
            if _stop_instance(name, signal_type):
                console.print("[green]stopped[/green]")
            else:
                console.print(f"[yellow]sent {signal_name}, may still be shutting down[/yellow]")


def add_use_command(group: click.Group) -> None:
    """Register 'use' command to select current context (instance/tenant).

    Sets the default instance and optionally tenant for subsequent commands.

    Args:
        group: Click group to register command on.

    Example:
        ::

            mail-proxy use production           # instance only
            mail-proxy use production/acme      # instance + tenant
            mail-proxy use /beta                # change tenant only
    """

    @group.command("use")
    @click.argument("context")
    def use_cmd(context: str) -> None:
        """Set the current instance and tenant for subsequent commands.

        CONTEXT can be:
            instance         - set instance (clear tenant)
            instance/tenant  - set both instance and tenant
            /tenant          - change tenant only (keep current instance)

        Example:
            mail-proxy use production
            mail-proxy use production/acme
            mail-proxy use /beta
        """
        new_instance, new_tenant = _parse_context(context)

        # If only tenant specified, keep current instance
        if new_instance is None:
            current_instance, _ = _get_current_context()
            if not current_instance:
                console.print("[red]Error:[/red] No current instance. Use 'mail-proxy use <instance>' first.")
                sys.exit(1)
            new_instance = current_instance

        # Validate instance exists
        instances = _list_instances()
        if not instances:
            console.print("[red]Error:[/red] No instances configured.")
            console.print("Use 'mail-proxy serve <name>' to create one.")
            sys.exit(1)

        if new_instance not in instances:
            console.print(f"[red]Error:[/red] Instance '{new_instance}' not found.")
            console.print()
            console.print("Available instances:")
            for inst in sorted(instances):
                console.print(f"  • {inst}")
            sys.exit(1)

        _set_current_context(new_instance, new_tenant)

        is_running, _, port = _is_instance_running(new_instance)
        status = "[green]running[/green]" if is_running else "[dim]stopped[/dim]"

        # Build display string
        if new_tenant:
            display = f"{new_instance}/{new_tenant}"
        else:
            display = new_instance

        console.print(f"[green]✓[/green] Now using: [bold cyan]{display}[/bold cyan] ({status})")
        if is_running:
            console.print(f"  URL: http://localhost:{port}")

        console.print()
        console.print("[dim]Tip: Add to your shell prompt:[/dim]")
        console.print(f"  export GMP_INSTANCE={new_instance}")
        if new_tenant:
            console.print(f"  export GMP_TENANT={new_tenant}")


def add_current_command(group: click.Group) -> None:
    """Register 'current' command to show current context.

    Args:
        group: Click group to register command on.

    Example:
        ::

            mail-proxy current
            mail-proxy current --export
    """

    @group.command("current")
    @click.option("--export", "-e", "do_export", is_flag=True, help="Output as shell export statements.")
    def current_cmd(do_export: bool) -> None:
        """Show the current instance and tenant.

        Use --export to get shell export statements for your prompt.

        Example:
            mail-proxy current
            eval $(mail-proxy current --export)
        """
        import os

        instance, tenant = resolve_context()

        if do_export:
            if instance:
                click.echo(f"export GMP_INSTANCE={instance}")
            else:
                click.echo("unset GMP_INSTANCE")
            if tenant:
                click.echo(f"export GMP_TENANT={tenant}")
            else:
                click.echo("unset GMP_TENANT")
            return

        if not instance:
            instances = _list_instances()
            if not instances:
                console.print("[dim]No instances configured.[/dim]")
                console.print("Use 'mail-proxy serve <name>' to create one.")
            else:
                console.print("[yellow]No instance selected.[/yellow]")
                console.print()
                console.print("Available instances:")
                for name in sorted(instances):
                    is_running, _, _ = _is_instance_running(name)
                    status = "[green]●[/green]" if is_running else "[dim]○[/dim]"
                    console.print(f"  {status} {name}")
                console.print()
                console.print("Use 'mail-proxy use <instance>' or 'mail-proxy use <instance>/<tenant>'.")
            return

        is_running, pid, port = _is_instance_running(instance)

        # Build display
        if tenant:
            display = f"{instance}/{tenant}"
        else:
            display = instance

        console.print(f"[bold cyan]{display}[/bold cyan]")

        # Show how it was resolved
        current_instance, current_tenant = _get_current_context()
        if os.environ.get("GMP_INSTANCE"):
            inst_source = "GMP_INSTANCE env"
        elif current_instance == instance:
            inst_source = ".current file"
        else:
            inst_source = "auto-selected"

        if tenant:
            if os.environ.get("GMP_TENANT"):
                tenant_source = "GMP_TENANT env"
            elif current_tenant == tenant:
                tenant_source = ".current file"
            else:
                tenant_source = "unknown"
            console.print(f"  [dim]Instance: {inst_source}, Tenant: {tenant_source}[/dim]")
        else:
            console.print(f"  [dim]Source: {inst_source}[/dim]")

        if is_running:
            console.print(f"  Status: [green]running[/green] (PID {pid})")
            console.print(f"  URL: http://localhost:{port}")
        else:
            console.print("  Status: [dim]stopped[/dim]")


def add_restart_command(group: click.Group) -> None:
    """Register 'restart' command to restart running instances.

    Args:
        group: Click group to register command on.

    Example:
        ::

            mail-proxy restart              # Restart all running instances
            mail-proxy restart myserver     # Restart specific instance
    """
    import signal as sig
    import subprocess
    import time

    @group.command("restart")
    @click.argument("name", default="*")
    @click.option("--force", "-f", is_flag=True, help="Force kill before restart.")
    @click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
    def restart_cmd(name: str, force: bool, reload: bool) -> None:
        """Restart mail-proxy instance(s).

        NAME can be an instance name or '*' to restart all.
        """
        signal_type = sig.SIGKILL if force else sig.SIGTERM

        instances_to_restart: list[tuple[str, dict[str, Any]]] = []

        if name == "*":
            mail_proxy_dir = Path.home() / ".mail-proxy"
            if not mail_proxy_dir.exists():
                console.print("[dim]No instances configured.[/dim]")
                return

            for item in mail_proxy_dir.iterdir():
                if item.is_dir() and (item / "config.ini").exists():
                    instance_name = item.name
                    is_running, _, _ = _is_instance_running(instance_name)
                    if is_running:
                        config = _get_instance_config(instance_name)
                        if config:
                            instances_to_restart.append((instance_name, config))

            if not instances_to_restart:
                console.print("[dim]No running instances found.[/dim]")
                return
        else:
            is_running, _, _ = _is_instance_running(name)
            if not is_running:
                console.print(f"[dim]Instance '{name}' is not running.[/dim]")
                console.print(f"[dim]Use 'mail-proxy serve {name}' to start it.[/dim]")
                return
            config = _get_instance_config(name)
            if config:
                instances_to_restart.append((name, config))

        # Stop all instances first
        for instance_name, _ in instances_to_restart:
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

        # Brief pause to ensure ports are released
        time.sleep(0.5)

        # Restart instances in background
        for instance_name, _config in instances_to_restart:
            console.print(f"Starting {instance_name}... ", end="")
            cmd = ["mail-proxy", "serve", instance_name]
            if reload:
                cmd.append("--reload")
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(1.0)
            is_running, pid, port = _is_instance_running(instance_name)
            if is_running:
                console.print(f"[green]started[/green] (PID {pid}, port {port})")
            else:
                console.print("[yellow]starting in background...[/yellow]")

        console.print(f"\n[green]Restarted {len(instances_to_restart)} instance(s)[/green]")


__all__ = [
    "add_connect_command",
    "add_stats_command",
    "add_send_command",
    "add_token_command",
    "add_run_now_command",
    "add_serve_command",
    "add_list_command",
    "add_stop_command",
    "add_restart_command",
    "add_use_command",
    "add_current_command",
    "resolve_context",
    "require_context",
    "resolve_instance",
    "require_instance",
]
