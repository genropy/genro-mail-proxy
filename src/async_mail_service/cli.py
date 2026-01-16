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
    ctx.obj["persistence"] = get_persistence(db)


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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

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
    persistence = ctx.obj["persistence"]

    async def _init():
        await persistence.init_db()

    run_async(_init())
    print_success(f"Database initialized at {ctx.obj['db_path']}")


if __name__ == "__main__":
    main()
