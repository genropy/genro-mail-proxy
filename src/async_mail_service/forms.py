"""Interactive terminal forms with Pydantic validation.

This module provides interactive forms for creating tenants, accounts,
and other entities with real-time validation using Pydantic models.

Usage in REPL:
    >>> from async_mail_service.forms import TenantForm, AccountForm
    >>> tenant_data = TenantForm().run()
    >>> account_data = AccountForm().run()

The forms display field descriptions from Pydantic annotations and
validate input in real-time, showing errors immediately.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Type, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table
from rich.text import Text

console = Console()


def get_field_description(field_info: FieldInfo) -> str:
    """Extract description from Pydantic field."""
    if field_info.description:
        return field_info.description
    return ""


def get_field_type_hint(annotation: Any) -> str:
    """Get a human-readable type hint."""
    origin = get_origin(annotation)

    if origin is None:
        if annotation is str:
            return "text"
        elif annotation is int:
            return "integer"
        elif annotation is bool:
            return "yes/no"
        elif annotation is float:
            return "number"
        elif hasattr(annotation, "__name__"):
            return annotation.__name__
        return str(annotation)

    # Handle Optional[X]
    args = get_args(annotation)
    if origin is type(None) or (len(args) == 2 and type(None) in args):
        inner = [a for a in args if a is not type(None)][0] if args else str
        return f"{get_field_type_hint(inner)} (optional)"

    # Handle List[X]
    if origin is list:
        inner = args[0] if args else Any
        return f"list of {get_field_type_hint(inner)}"

    return str(annotation)


def is_optional(annotation: Any) -> bool:
    """Check if a type annotation is Optional."""
    origin = get_origin(annotation)
    if origin is None:
        return False
    args = get_args(annotation)
    return type(None) in args


def get_inner_type(annotation: Any) -> Any:
    """Get the inner type of Optional[X]."""
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = get_args(annotation)
    non_none = [a for a in args if a is not type(None)]
    return non_none[0] if non_none else str


class InteractiveForm:
    """Base class for interactive terminal forms.

    Subclasses should define:
    - model: The Pydantic model class
    - title: Form title
    - fields: List of field names to include (in order)
    - field_groups: Optional dict of group_name -> [field_names]
    """

    model: Type[BaseModel]
    title: str = "Form"
    fields: List[str] = []
    field_groups: Dict[str, List[str]] = {}

    def __init__(self):
        self.values: Dict[str, Any] = {}
        self.errors: Dict[str, str] = {}

    def _get_field_info(self, field_name: str) -> Optional[FieldInfo]:
        """Get Pydantic FieldInfo for a field."""
        if field_name in self.model.model_fields:
            return self.model.model_fields[field_name]
        return None

    def _get_annotation(self, field_name: str) -> Any:
        """Get type annotation for a field."""
        hints = self.model.__annotations__
        return hints.get(field_name, str)

    def _prompt_field(self, field_name: str, current_value: Any = None) -> Any:
        """Prompt for a single field value."""
        field_info = self._get_field_info(field_name)
        annotation = self._get_annotation(field_name)

        # Build prompt text
        description = get_field_description(field_info) if field_info else ""
        type_hint = get_field_type_hint(annotation)
        optional = is_optional(annotation)
        inner_type = get_inner_type(annotation)

        # Field label with formatting
        label = field_name.replace("_", " ").title()

        # Show description if available
        if description:
            console.print(f"  [dim]{description}[/dim]")

        # Get default value
        default = None
        if field_info and field_info.default is not None:
            default = field_info.default
        if current_value is not None:
            default = current_value

        # Format default for display
        default_str = ""
        if default is not None:
            if isinstance(default, bool):
                default_str = "yes" if default else "no"
            else:
                default_str = str(default)

        # Handle boolean fields
        if inner_type is bool:
            default_bool = default if isinstance(default, bool) else False
            return Confirm.ask(f"  [cyan]{label}[/cyan]", default=default_bool)

        # Handle integer fields
        if inner_type is int:
            while True:
                value = Prompt.ask(
                    f"  [cyan]{label}[/cyan] [dim]({type_hint})[/dim]",
                    default=default_str if default_str else None,
                )
                if not value and optional:
                    return None
                try:
                    return int(value)
                except ValueError:
                    console.print("    [red]Please enter a valid integer[/red]")

        # Handle enum/choice fields
        if hasattr(inner_type, "__members__"):
            choices = list(inner_type.__members__.keys())
            choices_str = ", ".join(choices)
            console.print(f"    [dim]Choices: {choices_str}[/dim]")
            while True:
                value = Prompt.ask(
                    f"  [cyan]{label}[/cyan]",
                    default=default_str if default_str else None,
                )
                if not value and optional:
                    return None
                if value.lower() in [c.lower() for c in choices]:
                    # Return the actual enum value
                    for c in choices:
                        if c.lower() == value.lower():
                            return c
                console.print(f"    [red]Please choose from: {choices_str}[/red]")

        # Handle nested models (show as sub-form)
        if isinstance(inner_type, type) and issubclass(inner_type, BaseModel):
            if optional:
                if not Confirm.ask(f"  [cyan]Configure {label}?[/cyan]", default=False):
                    return None
            console.print(f"  [bold]{label}:[/bold]")
            sub_form = NestedModelForm(inner_type)
            return sub_form.run()

        # Default: string prompt
        value = Prompt.ask(
            f"  [cyan]{label}[/cyan] [dim]({type_hint})[/dim]",
            default=default_str if default_str else None,
        )

        if not value and optional:
            return None

        return value

    def _validate(self) -> bool:
        """Validate current values against the model."""
        try:
            # Filter out None values for optional fields
            filtered = {k: v for k, v in self.values.items() if v is not None}
            self.model(**filtered)
            self.errors = {}
            return True
        except ValidationError as e:
            self.errors = {}
            for error in e.errors():
                field = error["loc"][0] if error["loc"] else "unknown"
                self.errors[str(field)] = error["msg"]
            return False

    def _show_summary(self) -> None:
        """Show a summary of entered values."""
        table = Table(title="Summary", show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_column("Status", justify="center")

        for field_name in self.fields:
            value = self.values.get(field_name)
            error = self.errors.get(field_name)

            # Format value for display
            if value is None:
                value_str = "[dim]-[/dim]"
            elif isinstance(value, bool):
                value_str = "[green]yes[/green]" if value else "[red]no[/red]"
            elif isinstance(value, dict):
                value_str = "[dim]<configured>[/dim]"
            else:
                value_str = str(value)

            # Status indicator
            if error:
                status = f"[red]✗ {error}[/red]"
            elif value is not None:
                status = "[green]✓[/green]"
            else:
                status = "[dim]-[/dim]"

            label = field_name.replace("_", " ").title()
            table.add_row(label, value_str, status)

        console.print(table)

    def _edit_field(self, field_name: str) -> None:
        """Edit a specific field."""
        current = self.values.get(field_name)
        self.values[field_name] = self._prompt_field(field_name, current)
        self._validate()

    def run(self) -> Optional[Dict[str, Any]]:
        """Run the interactive form.

        Returns:
            Dict of validated values, or None if cancelled.
        """
        console.print()
        console.print(Panel(f"[bold]{self.title}[/bold]", expand=False))
        console.print()

        # Group fields if groups are defined
        if self.field_groups:
            for group_name, group_fields in self.field_groups.items():
                console.print(f"\n[bold yellow]{group_name}[/bold yellow]")
                for field_name in group_fields:
                    if field_name in self.fields:
                        self.values[field_name] = self._prompt_field(field_name)
        else:
            # Prompt all fields in order
            for field_name in self.fields:
                self.values[field_name] = self._prompt_field(field_name)

        # Validate
        is_valid = self._validate()

        # Show summary and allow edits
        while True:
            console.print()
            self._show_summary()
            console.print()

            if is_valid:
                action = Prompt.ask(
                    "[bold]Action[/bold]",
                    choices=["save", "edit", "cancel"],
                    default="save"
                )
            else:
                console.print("[red]Please fix the errors above[/red]")
                action = Prompt.ask(
                    "[bold]Action[/bold]",
                    choices=["edit", "cancel"],
                    default="edit"
                )

            if action == "save" and is_valid:
                # Return validated dict
                filtered = {k: v for k, v in self.values.items() if v is not None}
                return self.model(**filtered).model_dump(exclude_none=True)

            elif action == "edit":
                # Show field choices
                field_choices = {str(i+1): f for i, f in enumerate(self.fields)}
                console.print("\n[bold]Fields:[/bold]")
                for num, field_name in field_choices.items():
                    label = field_name.replace("_", " ").title()
                    console.print(f"  {num}. {label}")

                choice = Prompt.ask("Edit field number", default="1")
                if choice in field_choices:
                    self._edit_field(field_choices[choice])
                    is_valid = self._validate()

            elif action == "cancel":
                console.print("[dim]Cancelled[/dim]")
                return None


class NestedModelForm:
    """Form for nested Pydantic models."""

    def __init__(self, model: Type[BaseModel]):
        self.model = model
        self.values: Dict[str, Any] = {}

    def run(self) -> Dict[str, Any]:
        """Run the nested form."""
        for field_name, field_info in self.model.model_fields.items():
            annotation = self.model.__annotations__.get(field_name, str)
            description = get_field_description(field_info)
            optional = is_optional(annotation)
            inner_type = get_inner_type(annotation)

            label = field_name.replace("_", " ").title()

            if description:
                console.print(f"    [dim]{description}[/dim]")

            # Handle boolean
            if inner_type is bool:
                default = field_info.default if field_info.default is not None else False
                self.values[field_name] = Confirm.ask(f"    [cyan]{label}[/cyan]", default=default)
                continue

            # Handle string/other
            default = field_info.default if field_info.default is not None else ""
            value = Prompt.ask(
                f"    [cyan]{label}[/cyan]",
                default=str(default) if default else None,
            )

            if value:
                if inner_type is int:
                    self.values[field_name] = int(value)
                else:
                    self.values[field_name] = value
            elif not optional:
                self.values[field_name] = value

        return self.values


# ============================================================================
# Concrete Forms
# ============================================================================

class TenantForm(InteractiveForm):
    """Interactive form for creating a tenant."""

    from async_mail_service.models import TenantCreate
    model = TenantCreate
    title = "Create New Tenant"
    fields = [
        "id",
        "name",
        "client_sync_url",
        "client_sync_auth",
        "rate_limits",
        "active",
    ]
    field_groups = {
        "Basic Info": ["id", "name", "active"],
        "Client Sync": ["client_sync_url", "client_sync_auth"],
        "Rate Limits": ["rate_limits"],
    }


class AccountForm(InteractiveForm):
    """Interactive form for creating an SMTP account."""

    from async_mail_service.models import AccountCreate
    model = AccountCreate
    title = "Create New SMTP Account"
    fields = [
        "id",
        "tenant_id",
        "host",
        "port",
        "user",
        "password",
        "use_tls",
        "batch_size",
    ]
    field_groups = {
        "Identity": ["id", "tenant_id"],
        "SMTP Server": ["host", "port", "use_tls"],
        "Authentication": ["user", "password"],
        "Settings": ["batch_size"],
    }


class MessageForm(InteractiveForm):
    """Interactive form for creating an email message."""

    from async_mail_service.models import MessageCreate
    model = MessageCreate
    title = "Create New Message"
    fields = [
        "id",
        "account_id",
        "from_addr",
        "to",
        "subject",
        "body",
        "content_type",
        "priority",
    ]
    field_groups = {
        "Identity": ["id", "account_id"],
        "Addressing": ["from_addr", "to"],
        "Content": ["subject", "body", "content_type"],
        "Settings": ["priority"],
    }


# ============================================================================
# Quick Functions for REPL
# ============================================================================

def new_tenant() -> Optional[Dict[str, Any]]:
    """Interactive form to create a new tenant.

    Returns:
        Validated tenant dict, or None if cancelled.

    Example:
        >>> data = new_tenant()
        >>> if data:
        ...     proxy.tenants.add(data)
    """
    return TenantForm().run()


def new_account() -> Optional[Dict[str, Any]]:
    """Interactive form to create a new SMTP account.

    Returns:
        Validated account dict, or None if cancelled.

    Example:
        >>> data = new_account()
        >>> if data:
        ...     proxy.accounts.add(data)
    """
    return AccountForm().run()


def new_message() -> Optional[Dict[str, Any]]:
    """Interactive form to create a new message.

    Returns:
        Validated message dict, or None if cancelled.

    Example:
        >>> data = new_message()
        >>> if data:
        ...     proxy.messages.add([data])
    """
    return MessageForm().run()
