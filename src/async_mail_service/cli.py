"""Command-line interface for genro-mail-proxy.

This module provides a CLI for managing tenants, accounts, and messages
directly from the command line without going through the HTTP API.

Usage:
    mail-proxy tenant list
    mail-proxy tenant add acme-corp --name "ACME Corporation"
    mail-proxy account add main-smtp --tenant acme-corp --host smtp.example.com --port 587
    mail-proxy message list --tenant acme-corp

Example:
    $ mail-proxy tenant add mycompany --name "My Company" \\
        --sync-url "https://api.mycompany.com/mail/sync" \\
        --sync-auth-method bearer --sync-auth-token secret123

    $ mail-proxy account add primary --tenant mycompany \\
        --host smtp.mycompany.com --port 587 \\
        --user mailer@mycompany.com --password secret
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime
from typing import Annotated, Any, Dict, Optional

import click
from pydantic import ValidationError
from rich.console import Console
from rich.table import Table

from async_mail_service.models import (
    AccountCreate,
    TenantCreate,
    TenantSyncAuth,
    TenantAttachmentConfig,
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


# Main CLI group
@click.group()
@click.option(
    "--db", "-d",
    envvar="GMP_DB_PATH",
    default="/data/mail_service.db",
    help="Path to SQLite database file.",
    show_default=True,
)
@click.pass_context
def main(ctx: click.Context, db: str) -> None:
    """genro-mail-proxy CLI - Manage tenants, accounts, and messages.

    Use --db or GMP_DB_PATH environment variable to specify the database path.
    """
    ctx.ensure_object(dict)
    ctx.obj["db_path"] = db
    # Persistence created lazily when needed (avoid blocking on --help)
    ctx.obj["_persistence_factory"] = lambda: get_persistence(db)


def _get_persistence(ctx: click.Context) -> Persistence:
    """Get or create persistence instance lazily."""
    if "persistence" not in ctx.obj:
        ctx.obj["persistence"] = ctx.obj["_persistence_factory"]()
    return ctx.obj["persistence"]


# ============================================================================
# TENANT commands
# ============================================================================

@main.group()
def tenant() -> None:
    """Manage tenants."""
    pass


@tenant.command("list")
@click.option("--active-only", "-a", is_flag=True, help="Show only active tenants.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def tenant_list(ctx: click.Context, active_only: bool, as_json: bool) -> None:
    """List all tenants."""
    persistence = _get_persistence(ctx)

    async def _list():
        await persistence.init_db()
        return await persistence.list_tenants(active_only=active_only)

    tenants = run_async(_list())

    if as_json:
        print_json(tenants)
        return

    if not tenants:
        console.print("[dim]No tenants found.[/dim]")
        return

    table = Table(title="Tenants")
    table.add_column("ID", style="cyan")
    table.add_column("Name")
    table.add_column("Active", justify="center")
    table.add_column("Sync URL")

    for t in tenants:
        active = "[green]✓[/green]" if t.get("active") else "[red]✗[/red]"
        table.add_row(
            t["id"],
            t.get("name") or "-",
            active,
            t.get("client_sync_url") or "-",
        )

    console.print(table)


@tenant.command("show")
@click.argument("tenant_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def tenant_show(ctx: click.Context, tenant_id: str, as_json: bool) -> None:
    """Show details for a specific tenant."""
    persistence = _get_persistence(ctx)

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
    console.print(f"  Sync URL:        {tenant_data.get('client_sync_url') or '-'}")
    console.print(f"  Created:         {tenant_data.get('created_at') or '-'}")
    console.print(f"  Updated:         {tenant_data.get('updated_at') or '-'}")

    if tenant_data.get("client_sync_auth"):
        auth = tenant_data["client_sync_auth"]
        console.print(f"  Sync Auth:       {auth.get('method', 'none')}")

    if tenant_data.get("rate_limits"):
        limits = tenant_data["rate_limits"]
        console.print(f"  Rate Limits:     hourly={limits.get('hourly', 0)}, daily={limits.get('daily', 0)}")

    if tenant_data.get("attachment_config"):
        att = tenant_data["attachment_config"]
        console.print(f"  Attachment Dir:  {att.get('base_dir') or '-'}")
        console.print(f"  HTTP Endpoint:   {att.get('http_endpoint') or '-'}")

    console.print()


@tenant.command("add")
@click.argument("tenant_id")
@click.option("--name", "-n", help="Human-readable tenant name.")
@click.option("--sync-url", help="URL for delivery report callbacks.")
@click.option("--sync-auth-method", type=click.Choice(["none", "bearer", "basic"]), default="none",
              help="Authentication method for sync endpoint.")
@click.option("--sync-auth-token", help="Bearer token (for bearer auth).")
@click.option("--sync-auth-user", help="Username (for basic auth).")
@click.option("--sync-auth-password", help="Password (for basic auth).")
@click.option("--attachment-base-dir", help="Base directory for relative attachment paths.")
@click.option("--attachment-http-endpoint", help="Default HTTP endpoint for attachment fetcher.")
@click.option("--rate-limit-hourly", type=int, default=0, help="Max emails per hour (0=unlimited).")
@click.option("--rate-limit-daily", type=int, default=0, help="Max emails per day (0=unlimited).")
@click.option("--inactive", is_flag=True, help="Create tenant as inactive.")
@click.pass_context
def tenant_add(
    ctx: click.Context,
    tenant_id: str,
    name: Optional[str],
    sync_url: Optional[str],
    sync_auth_method: str,
    sync_auth_token: Optional[str],
    sync_auth_user: Optional[str],
    sync_auth_password: Optional[str],
    attachment_base_dir: Optional[str],
    attachment_http_endpoint: Optional[str],
    rate_limit_hourly: int,
    rate_limit_daily: int,
    inactive: bool,
) -> None:
    """Add a new tenant.

    TENANT_ID must be alphanumeric with underscores/hyphens (e.g., acme-corp).
    """
    persistence = _get_persistence(ctx)

    # Build sync auth config
    sync_auth = None
    if sync_auth_method != "none":
        sync_auth = {
            "method": sync_auth_method,
            "token": sync_auth_token,
            "user": sync_auth_user,
            "password": sync_auth_password,
        }

    # Build attachment config
    attachment_config = None
    if attachment_base_dir or attachment_http_endpoint:
        attachment_config = {
            "base_dir": attachment_base_dir,
            "http_endpoint": attachment_http_endpoint,
        }

    # Build rate limits
    rate_limits = None
    if rate_limit_hourly > 0 or rate_limit_daily > 0:
        rate_limits = {
            "hourly": rate_limit_hourly,
            "daily": rate_limit_daily,
        }

    # Validate with Pydantic
    try:
        tenant_data = TenantCreate(
            id=tenant_id,
            name=name,
            client_sync_url=sync_url,
            client_sync_auth=TenantSyncAuth(**sync_auth) if sync_auth else None,
            attachment_config=TenantAttachmentConfig(**attachment_config) if attachment_config else None,
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


@tenant.command("update")
@click.argument("tenant_id")
@click.option("--name", "-n", help="Human-readable tenant name.")
@click.option("--sync-url", help="URL for delivery report callbacks.")
@click.option("--sync-auth-method", type=click.Choice(["none", "bearer", "basic"]),
              help="Authentication method for sync endpoint.")
@click.option("--sync-auth-token", help="Bearer token (for bearer auth).")
@click.option("--sync-auth-user", help="Username (for basic auth).")
@click.option("--sync-auth-password", help="Password (for basic auth).")
@click.option("--attachment-base-dir", help="Base directory for relative attachment paths.")
@click.option("--attachment-http-endpoint", help="Default HTTP endpoint for attachment fetcher.")
@click.option("--rate-limit-hourly", type=int, help="Max emails per hour (0=unlimited).")
@click.option("--rate-limit-daily", type=int, help="Max emails per day (0=unlimited).")
@click.option("--active/--inactive", default=None, help="Set tenant active status.")
@click.pass_context
def tenant_update(
    ctx: click.Context,
    tenant_id: str,
    name: Optional[str],
    sync_url: Optional[str],
    sync_auth_method: Optional[str],
    sync_auth_token: Optional[str],
    sync_auth_user: Optional[str],
    sync_auth_password: Optional[str],
    attachment_base_dir: Optional[str],
    attachment_http_endpoint: Optional[str],
    rate_limit_hourly: Optional[int],
    rate_limit_daily: Optional[int],
    active: Optional[bool],
) -> None:
    """Update an existing tenant.

    Only provided options will be updated.
    """
    persistence = _get_persistence(ctx)

    updates: Dict[str, Any] = {}

    if name is not None:
        updates["name"] = name
    if sync_url is not None:
        updates["client_sync_url"] = sync_url
    if active is not None:
        updates["active"] = active

    # Build sync auth if any auth option provided
    if any([sync_auth_method, sync_auth_token, sync_auth_user, sync_auth_password]):
        updates["client_sync_auth"] = {
            "method": sync_auth_method or "none",
            "token": sync_auth_token,
            "user": sync_auth_user,
            "password": sync_auth_password,
        }

    # Build attachment config if any option provided
    if attachment_base_dir is not None or attachment_http_endpoint is not None:
        updates["attachment_config"] = {
            "base_dir": attachment_base_dir,
            "http_endpoint": attachment_http_endpoint,
        }

    # Build rate limits if any option provided
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


@tenant.command("delete")
@click.argument("tenant_id")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def tenant_delete(ctx: click.Context, tenant_id: str, force: bool) -> None:
    """Delete a tenant and all associated accounts/messages.

    This operation is irreversible!
    """
    persistence = _get_persistence(ctx)

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
# ACCOUNT commands
# ============================================================================

@main.group()
def account() -> None:
    """Manage SMTP accounts."""
    pass


@account.command("list")
@click.option("--tenant", "-t", help="Filter by tenant ID.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def account_list(ctx: click.Context, tenant: Optional[str], as_json: bool) -> None:
    """List all SMTP accounts."""
    persistence = _get_persistence(ctx)

    async def _list():
        await persistence.init_db()
        return await persistence.list_accounts(tenant_id=tenant)

    accounts = run_async(_list())

    if as_json:
        print_json(accounts)
        return

    if not accounts:
        console.print("[dim]No accounts found.[/dim]")
        return

    table = Table(title="SMTP Accounts")
    table.add_column("ID", style="cyan")
    table.add_column("Tenant")
    table.add_column("Host")
    table.add_column("Port", justify="right")
    table.add_column("User")
    table.add_column("TLS", justify="center")

    for acc in accounts:
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


@account.command("show")
@click.argument("account_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def account_show(ctx: click.Context, account_id: str, as_json: bool) -> None:
    """Show details for a specific account."""
    persistence = _get_persistence(ctx)

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

    # Remove password from display
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


@account.command("add")
@click.argument("account_id")
@click.option("--tenant", "-t", required=True, help="Tenant ID (required).")
@click.option("--host", "-h", required=True, help="SMTP server hostname.")
@click.option("--port", "-p", type=int, required=True, help="SMTP server port.")
@click.option("--user", "-u", help="SMTP username.")
@click.option("--password", help="SMTP password.")
@click.option("--tls/--no-tls", default=True, help="Use STARTTLS (default: yes).")
@click.option("--batch-size", type=int, help="Max messages per dispatch cycle.")
@click.option("--limit-hour", type=int, help="Max emails per hour.")
@click.option("--limit-day", type=int, help="Max emails per day.")
@click.pass_context
def account_add(
    ctx: click.Context,
    account_id: str,
    tenant: str,
    host: str,
    port: int,
    user: Optional[str],
    password: Optional[str],
    tls: bool,
    batch_size: Optional[int],
    limit_hour: Optional[int],
    limit_day: Optional[int],
) -> None:
    """Add a new SMTP account.

    ACCOUNT_ID must be unique and alphanumeric with underscores/hyphens.
    """
    persistence = _get_persistence(ctx)

    # Validate with Pydantic
    try:
        account_data = AccountCreate(
            id=account_id,
            tenant_id=tenant,
            host=host,
            port=port,
            user=user,
            password=password,
            use_tls=tls,
            batch_size=batch_size,
        )
    except ValidationError as e:
        print_error(f"Validation error: {e}")
        sys.exit(1)

    async def _add():
        await persistence.init_db()
        # Verify tenant exists
        tenant_data = await persistence.get_tenant(tenant)
        if not tenant_data:
            return False, f"Tenant '{tenant}' not found."

        acc_dict = account_data.model_dump(exclude_none=True)
        if limit_hour:
            acc_dict["limit_per_hour"] = limit_hour
        if limit_day:
            acc_dict["limit_per_day"] = limit_day
        await persistence.add_account(acc_dict)
        return True, None

    success, error = run_async(_add())

    if success:
        print_success(f"Account '{account_id}' created for tenant '{tenant}'.")
    else:
        print_error(error)
        sys.exit(1)


@account.command("delete")
@click.argument("account_id")
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def account_delete(ctx: click.Context, account_id: str, force: bool) -> None:
    """Delete an SMTP account and all associated messages."""
    persistence = _get_persistence(ctx)

    if not force:
        if not click.confirm(f"Delete account '{account_id}' and all associated messages?"):
            console.print("Aborted.")
            return

    async def _delete():
        await persistence.init_db()
        await persistence.delete_account(account_id)

    run_async(_delete())
    print_success(f"Account '{account_id}' deleted.")


# ============================================================================
# MESSAGE commands
# ============================================================================

@main.group()
def message() -> None:
    """View and manage messages in the queue."""
    pass


@message.command("list")
@click.option("--tenant", "-t", help="Filter by tenant ID.")
@click.option("--account", "-a", help="Filter by account ID.")
@click.option("--status", "-s", type=click.Choice(["pending", "sent", "error", "all"]), default="all",
              help="Filter by status.")
@click.option("--limit", "-l", type=int, default=50, help="Max messages to show.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def message_list(
    ctx: click.Context,
    tenant: Optional[str],
    account: Optional[str],
    status: str,
    limit: int,
    as_json: bool,
) -> None:
    """List messages in the queue."""
    persistence = _get_persistence(ctx)

    async def _list():
        await persistence.init_db()
        messages = await persistence.list_messages(
            limit=limit,
            account_id=account,
        )

        # Filter by tenant if specified
        if tenant:
            tenant_accounts = await persistence.list_accounts(tenant_id=tenant)
            account_ids = {a["id"] for a in tenant_accounts}
            messages = [m for m in messages if m.get("account_id") in account_ids]

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

        return messages

    messages = run_async(_list())

    if as_json:
        print_json(messages)
        return

    if not messages:
        console.print("[dim]No messages found.[/dim]")
        return

    table = Table(title=f"Messages (showing up to {limit})")
    table.add_column("ID", style="cyan", max_width=20)
    table.add_column("Account")
    table.add_column("Status")
    table.add_column("Subject", max_width=30)
    table.add_column("Created")

    for msg in messages[:limit]:
        # Determine status
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


@message.command("show")
@click.argument("message_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.pass_context
def message_show(ctx: click.Context, message_id: str, as_json: bool) -> None:
    """Show details for a specific message."""
    persistence = _get_persistence(ctx)

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


@message.command("delete")
@click.argument("message_ids", nargs=-1, required=True)
@click.option("--force", "-f", is_flag=True, help="Skip confirmation prompt.")
@click.pass_context
def message_delete(ctx: click.Context, message_ids: tuple, force: bool) -> None:
    """Delete one or more messages from the queue."""
    persistence = _get_persistence(ctx)

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
# STATS command
# ============================================================================

@main.command()
@click.option("--tenant", "-t", help="Show stats for specific tenant.")
@click.pass_context
def stats(ctx: click.Context, tenant: Optional[str]) -> None:
    """Show queue statistics."""
    persistence = _get_persistence(ctx)

    async def _stats():
        await persistence.init_db()

        tenants = await persistence.list_tenants()
        accounts = await persistence.list_accounts(tenant_id=tenant)
        messages = await persistence.list_messages()

        # Filter by tenant if specified
        if tenant:
            account_ids = {a["id"] for a in accounts}
            messages = [m for m in messages if m.get("account_id") in account_ids]

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

    title = f"Stats for tenant '{tenant}'" if tenant else "Overall Stats"
    console.print(f"\n[bold]{title}[/bold]\n")

    if not tenant:
        console.print(f"  Tenants:    {data['tenants']}")
    console.print(f"  Accounts:   {data['accounts']}")
    console.print(f"  Messages:")
    console.print(f"    Total:    {data['messages']['total']}")
    console.print(f"    Pending:  {data['messages']['pending']}")
    console.print(f"    Sent:     {data['messages']['sent']}")
    console.print(f"    Errors:   {data['messages']['error']}")
    console.print()


# ============================================================================
# INIT command
# ============================================================================

@main.command()
@click.pass_context
def init(ctx: click.Context) -> None:
    """Initialize the database schema."""
    persistence = _get_persistence(ctx)

    async def _init():
        await persistence.init_db()

    run_async(_init())
    print_success(f"Database initialized at {ctx.obj['db_path']}")


# ============================================================================
# SERVE command
# ============================================================================

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
# URL for delivery report callbacks
# client_sync_url = https://api.example.com/mail/delivery-report

# Authentication (bearer or basic)
# auth_method = bearer
# auth_token =
# auth_user =
# auth_password =
"""


def _get_instance_dir(name: str) -> "Path":
    """Get the instance directory path."""
    from pathlib import Path
    return Path.home() / ".mail-proxy" / name


def _get_pid_file(name: str) -> "Path":
    """Get the PID file path for an instance."""
    return _get_instance_dir(name) / "server.pid"


def _is_instance_running(name: str) -> tuple[bool, Optional[int], Optional[int]]:
    """Check if an instance is running.

    Returns:
        (is_running, pid, port)
    """
    import os
    import signal

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


def _ensure_config_file(name: str, port: int, host: str) -> str:
    """Ensure config file exists, creating with defaults if needed.

    Returns the path to the config file.
    """
    from pathlib import Path

    # Default config location: ~/.mail-proxy/<name>/config.ini
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

    return str(config_file)


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


def _start_server_background(name: str, host: str, port: int, reload: bool = False) -> bool:
    """Start server in background and wait for it to be ready.

    Returns True if server started successfully and is responding to HTTP requests.
    """
    import subprocess
    import time

    import requests

    cmd = ["mail-proxy", "serve", name, "--host", host, "--port", str(port)]
    if reload:
        cmd.append("--reload")

    # Start server in background
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for server to be ready (process running + HTTP responding)
    url = f"http://localhost:{port}/status"
    for _ in range(50):  # Max 5 seconds
        time.sleep(0.1)
        is_running, pid, _ = _is_instance_running(name)
        if is_running:
            # Process is running, now check if HTTP server is ready
            try:
                resp = requests.get(url, timeout=0.5)
                if resp.status_code == 200:
                    return True
            except requests.RequestException:
                # Server not ready yet, keep waiting
                pass
    return False


@main.command()
@click.argument("name", default="default-mailer")
@click.option("--host", "-h", default=None, help="Host to bind to (default: 0.0.0.0).")
@click.option("--port", "-p", type=int, default=None, help="Port to listen on (default: 8000).")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
@click.option("--connect", "-c", is_flag=True, help="Start in background and open REPL.")
@click.pass_context
def serve(
    ctx: click.Context,
    name: str,
    host: Optional[str],
    port: Optional[int],
    reload: bool,
    connect: bool,
) -> None:
    """Start a mail-proxy server instance.

    If the instance doesn't exist, creates it with default config.
    If already running, shows status and exits.

    Example:
        mail-proxy serve                    # Start default-mailer
        mail-proxy serve myserver           # Start/create myserver
        mail-proxy serve myserver -p 8080   # Start on specific port
        mail-proxy serve myserver -c        # Start and open REPL
    """
    import os
    import uvicorn

    # Check if already running
    is_running, pid, running_port = _is_instance_running(name)
    if is_running:
        if connect:
            # Already running, just connect
            console.print(f"[dim]Instance '{name}' already running, connecting...[/dim]")
            ctx.invoke(connect_cmd, name_or_url=name)
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
        host = host or "0.0.0.0"
        port = port or 8000
        config_path = _ensure_config_file(name, port, host)
        instance_config = _get_instance_config(name)
    else:
        # Existing instance - use config values, allow override
        config_path = instance_config["config_file"]
        host = host or instance_config["host"]
        port = port or instance_config["port"]

    db_path = instance_config["db_path"]

    if connect:
        # Start in background and open REPL
        console.print(f"[bold cyan]Starting {name} in background...[/bold cyan]")
        if _start_server_background(name, host, port, reload):
            is_running, pid, _ = _is_instance_running(name)
            console.print(f"  PID:  {pid}")
            console.print(f"  Port: {port}")
            console.print()
            ctx.invoke(connect_cmd, name_or_url=name)
        else:
            print_error(f"Failed to start {name}")
        return

    # Set environment variables for config (used by server.py)
    os.environ["GMP_CONFIG_FILE"] = config_path
    os.environ["GMP_INSTANCE_NAME"] = name
    os.environ["GMP_DB_PATH"] = db_path
    os.environ["GMP_PORT"] = str(port)
    os.environ["GMP_HOST"] = host

    console.print(f"\n[bold cyan]Starting {name}[/bold cyan]")
    console.print(f"  Config:  {config_path}")
    console.print(f"  DB:      {db_path}")
    console.print(f"  Listen:  {host}:{port}")
    console.print()

    # Write PID file before starting uvicorn (important for --reload mode)
    _write_pid_file(name, os.getpid(), port, host)

    try:
        uvicorn.run(
            "async_mail_service.server:app",
            host=host,
            port=port,
            reload=reload,
            log_level="info",
        )
    finally:
        # Clean up PID file on exit
        _remove_pid_file(name)


# ============================================================================
# STOP command
# ============================================================================

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
        # Wait for process to terminate
        wait_iterations = int(timeout / 0.1)
        for _ in range(wait_iterations):
            time.sleep(0.1)
            try:
                os.kill(pid, 0)  # Check if still alive
            except ProcessLookupError:
                # Process terminated
                _remove_pid_file(name)
                return True

        # Process still alive after timeout - try SIGKILL if enabled
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


@main.command()
@click.argument("name", default="*")
@click.option("--force", "-f", is_flag=True, help="Force kill (SIGKILL) instead of graceful shutdown.")
def stop(name: str, force: bool) -> None:
    """Stop running mail-proxy instance(s).

    NAME can be:
    - An instance name (e.g., 'myserver')
    - '*' to stop all running instances

    Example:
        mail-proxy stop                 # Stop all running instances
        mail-proxy stop myserver        # Stop specific instance
        mail-proxy stop myserver -f     # Force kill if not responding
    """
    import signal as sig
    from pathlib import Path

    signal_type = sig.SIGKILL if force else sig.SIGTERM
    signal_name = "SIGKILL" if force else "SIGTERM"

    if name == "*":
        # Stop all running instances
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
        # Stop specific instance
        is_running, pid, _ = _is_instance_running(name)
        if not is_running:
            console.print(f"[dim]Instance '{name}' is not running.[/dim]")
            return

        console.print(f"Stopping {name} (PID {pid})... ", end="")
        if _stop_instance(name, signal_type):
            console.print("[green]stopped[/green]")
        else:
            console.print(f"[yellow]sent {signal_name}, may still be shutting down[/yellow]")


# ============================================================================
# RESTART command
# ============================================================================

@main.command()
@click.argument("name", default="*")
@click.option("--force", "-f", is_flag=True, help="Force kill before restart.")
@click.option("--reload", is_flag=True, help="Enable auto-reload for development.")
def restart(name: str, force: bool, reload: bool) -> None:
    """Restart mail-proxy instance(s).

    NAME can be:
    - An instance name (e.g., 'myserver')
    - '*' to restart all running instances

    Example:
        mail-proxy restart              # Restart all running instances
        mail-proxy restart myserver     # Restart specific instance
        mail-proxy restart myserver -f  # Force kill then restart
    """
    import os
    import signal as sig
    import subprocess
    import time
    from pathlib import Path

    signal_type = sig.SIGKILL if force else sig.SIGTERM

    instances_to_restart: list[tuple[str, Dict[str, Any]]] = []

    if name == "*":
        # Collect all running instances
        mail_proxy_dir = Path.home() / ".mail-proxy"
        if not mail_proxy_dir.exists():
            console.print("[dim]No instances configured.[/dim]")
            return

        for item in mail_proxy_dir.iterdir():
            if item.is_dir() and (item / "config.ini").exists():
                instance_name = item.name
                is_running, pid, _ = _is_instance_running(instance_name)
                if is_running:
                    config = _get_instance_config(instance_name)
                    if config:
                        instances_to_restart.append((instance_name, config))

        if not instances_to_restart:
            console.print("[dim]No running instances found.[/dim]")
            return
    else:
        # Single instance
        is_running, pid, _ = _is_instance_running(name)
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
                # Try SIGKILL if SIGTERM didn't work
                if not force:
                    console.print("[yellow]forcing...[/yellow] ", end="")
                    _stop_instance(instance_name, sig.SIGKILL, timeout=1.0)
                console.print("[green]stopped[/green]")

    # Brief pause to ensure ports are released
    time.sleep(0.5)

    # Restart instances in background
    for instance_name, config in instances_to_restart:
        console.print(f"Starting {instance_name}... ", end="")
        cmd = ["mail-proxy", "serve", instance_name]
        if reload:
            cmd.append("--reload")
        # Start in background
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Wait briefly for startup
        time.sleep(1.0)
        is_running, pid, port = _is_instance_running(instance_name)
        if is_running:
            console.print(f"[green]started[/green] (PID {pid}, port {port})")
        else:
            console.print("[yellow]starting in background...[/yellow]")

    console.print(f"\n[green]Restarted {len(instances_to_restart)} instance(s)[/green]")


# ============================================================================
# CONNECT command (REPL)
# ============================================================================

def _resolve_instance_url(name_or_url: str) -> tuple[str, str, Optional[str]]:
    """Resolve instance name or URL to (url, display_name, token).

    Checks in order:
    1. If it looks like a URL, use it directly
    2. If it's a running instance name, use its port
    3. If it's a registered connection name, use that
    """
    import configparser
    from pathlib import Path

    # If it looks like a URL, use it directly
    if name_or_url.startswith("http://") or name_or_url.startswith("https://"):
        return name_or_url, name_or_url, None

    # Check if it's a running instance
    is_running, pid, port = _is_instance_running(name_or_url)
    if is_running and port:
        return f"http://localhost:{port}", name_or_url, None

    # Check if instance exists but not running
    instance_config = _get_instance_config(name_or_url)
    if instance_config:
        # Instance exists but not running
        print_error(f"Instance '{name_or_url}' is not running")
        console.print(f"[dim]Start it with: mail-proxy serve {name_or_url}[/dim]")
        raise SystemExit(1)

    # Check registered connections
    connections_file = Path.home() / ".mail-proxy" / "connections.json"
    if connections_file.exists():
        try:
            connections = json.loads(connections_file.read_text())
            if name_or_url in connections:
                conn = connections[name_or_url]
                return conn["url"], name_or_url, conn.get("token")
        except json.JSONDecodeError:
            pass

    # Not found anywhere
    print_error(f"Unknown instance or connection: '{name_or_url}'")
    console.print("[dim]Use 'mail-proxy list' to see available instances[/dim]")
    console.print("[dim]Use 'mail-proxy connections' to see registered connections[/dim]")
    raise SystemExit(1)


@main.command("connect")
@click.argument("name_or_url", default="default-mailer")
@click.option("--token", "-t", envvar="GMP_API_TOKEN", help="API token for authentication.")
def connect_cmd(name_or_url: str, token: Optional[str]) -> None:
    """Connect to a mail-proxy instance with an interactive REPL.

    NAME_OR_URL can be:
    - An instance name (e.g., 'myserver') - connects to running instance
    - A registered connection name
    - A full URL (e.g., 'http://localhost:8000')

    Example:
        mail-proxy connect                  # Connect to default-mailer
        mail-proxy connect myserver         # Connect to myserver instance
        mail-proxy connect http://host:8000 # Connect to URL
    """
    import code
    import readline  # noqa: F401 - enables history in REPL
    import rlcompleter  # noqa: F401 - enables tab completion

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

    # Resolve the URL
    url, display_name, saved_token = _resolve_instance_url(name_or_url)
    token = token or saved_token

    try:
        proxy = client_connect(url, token=token, name=display_name)

        # Test connection
        if not proxy.health():
            print_error(f"Cannot connect to {display_name} ({url})")
            console.print("[dim]Make sure the server is running.[/dim]")
            return

        # Set proxy for forms auto-save
        set_proxy(proxy)

        console.print(f"\n[bold green]Connected to {display_name}[/bold green]")
        console.print(f"  URL: {url}")
        console.print()

        # Show quick help
        console.print("[bold]Available objects:[/bold]")
        console.print("  [cyan]proxy[/cyan]          - The connected client")
        console.print("  [cyan]proxy.messages[/cyan] - Message management (list, pending, sent, errors, add, delete)")
        console.print("  [cyan]proxy.accounts[/cyan] - Account management (list, get, add, delete)")
        console.print("  [cyan]proxy.tenants[/cyan]  - Tenant management (list, get, add, delete)")
        console.print()
        console.print("[bold]Quick commands:[/bold]")
        console.print("  [cyan]proxy.status()[/cyan]          - Server status")
        console.print("  [cyan]proxy.stats()[/cyan]           - Queue statistics")
        console.print("  [cyan]proxy.run_now()[/cyan]         - Trigger dispatch cycle")
        console.print("  [cyan]proxy.messages.pending()[/cyan] - List pending messages")
        console.print()
        console.print("[bold]Interactive forms:[/bold]")
        console.print("  [cyan]new_tenant()[/cyan]   - Create tenant with interactive form")
        console.print("  [cyan]new_account()[/cyan]  - Create account with interactive form")
        console.print("  [cyan]new_message()[/cyan]  - Create message with interactive form")
        console.print()
        console.print("[dim]Type 'exit()' or Ctrl+D to quit.[/dim]")
        console.print()

        # Prepare namespace for REPL
        namespace = {
            "proxy": proxy,
            "MailProxyClient": MailProxyClient,
            "console": console,
            # Interactive forms
            "new_tenant": new_tenant,
            "new_account": new_account,
            "new_message": new_message,
            "TenantForm": TenantForm,
            "AccountForm": AccountForm,
            "MessageForm": MessageForm,
        }

        # Start interactive REPL
        banner = ""
        code.interact(banner=banner, local=namespace, exitmsg="Goodbye!")

    except Exception as e:
        print_error(f"Connection failed: {e}")
        raise SystemExit(1)


# ============================================================================
# REGISTER command (for named connections)
# ============================================================================

@main.command()
@click.argument("name")
@click.argument("url")
@click.option("--token", "-t", help="API token for this connection.")
def register(name: str, url: str, token: Optional[str]) -> None:
    """Register a named connection for easy access.

    Connections are stored in ~/.mail-proxy/connections.json

    Example:
        mail-proxy register prod https://mail.example.com --token secret
        mail-proxy connect prod
    """
    import os
    from pathlib import Path

    config_dir = Path.home() / ".mail-proxy"
    config_dir.mkdir(exist_ok=True)
    connections_file = config_dir / "connections.json"

    # Load existing connections
    connections = {}
    if connections_file.exists():
        try:
            connections = json.loads(connections_file.read_text())
        except json.JSONDecodeError:
            connections = {}

    # Add/update connection
    connections[name] = {"url": url}
    if token:
        connections[name]["token"] = token

    # Save
    connections_file.write_text(json.dumps(connections, indent=2))
    print_success(f"Registered connection '{name}' -> {url}")


@main.command("connections")
def list_connections() -> None:
    """List registered connections."""
    from pathlib import Path

    connections_file = Path.home() / ".mail-proxy" / "connections.json"

    if not connections_file.exists():
        console.print("[dim]No connections registered.[/dim]")
        console.print("Use 'mail-proxy register <name> <url>' to add one.")
        return

    try:
        connections = json.loads(connections_file.read_text())
    except json.JSONDecodeError:
        console.print("[dim]No connections registered.[/dim]")
        return

    if not connections:
        console.print("[dim]No connections registered.[/dim]")
        return

    table = Table(title="Registered Connections")
    table.add_column("Name", style="cyan")
    table.add_column("URL")
    table.add_column("Token", justify="center")

    for name, data in connections.items():
        has_token = "[green]✓[/green]" if data.get("token") else "[dim]-[/dim]"
        table.add_row(name, data["url"], has_token)

    console.print(table)


@main.command("list")
def list_instances() -> None:
    """List mail-proxy instances with their status.

    Shows all instances in ~/.mail-proxy/ with running status.
    """
    import configparser
    from pathlib import Path

    mail_proxy_dir = Path.home() / ".mail-proxy"

    if not mail_proxy_dir.exists():
        console.print("[dim]No instances configured.[/dim]")
        console.print("Use 'mail-proxy serve <name>' to create one.")
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
        console.print("Use 'mail-proxy serve <name>' to create one.")
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


@main.command("token")
@click.argument("name", default="default-mailer")
@click.option("--regenerate", "-r", is_flag=True, help="Generate a new token and update config.")
def show_token(name: str, regenerate: bool) -> None:
    """Show or regenerate the API token for an instance.

    Examples:

        mail-proxy token my-instance

        mail-proxy token my-instance --regenerate
    """
    import configparser

    config_dir = _get_instance_dir(name)
    config_file = config_dir / "config.ini"

    if not config_file.exists():
        print_error(f"Instance '{name}' not found. Use 'mail-proxy serve {name}' to create it.")
        raise SystemExit(1)

    config = configparser.ConfigParser()
    config.read(config_file)

    if regenerate:
        # Generate new token
        new_token = _generate_api_token()

        # Update config file
        if not config.has_section("server"):
            config.add_section("server")
        config.set("server", "api_token", new_token)

        with open(config_file, "w") as f:
            config.write(f)

        console.print(f"[green]Token regenerated for instance:[/green] {name}")
        console.print(f"[yellow]Note:[/yellow] Restart the instance for the new token to take effect.")
        console.print(f"\n{new_token}")
    else:
        # Show existing token
        token = config.get("server", "api_token", fallback=None)

        if not token or token.strip() == "":
            console.print(f"[yellow]No API token configured for instance:[/yellow] {name}")
            console.print("Use --regenerate to generate one.")
            raise SystemExit(1)

        console.print(token.strip())


if __name__ == "__main__":
    main()
