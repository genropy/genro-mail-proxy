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

Example:
    Add special commands to CLI group::

        from core.mail_proxy.interface.cli_commands import (
            add_connect_command,
            add_stats_command,
            add_send_command,
        )

        @click.group()
        def cli():
            pass

        add_connect_command(cli, get_url, get_token, "myinstance")
        add_stats_command(cli, db)
        add_send_command(cli, db, "tenant1")

    Run commands::

        mail-proxy myinstance connect
        mail-proxy myinstance stats --json
        mail-proxy myinstance tenant1 send email.eml

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


__all__ = [
    "add_connect_command",
    "add_stats_command",
    "add_send_command",
    "add_token_command",
    "add_run_now_command",
]
