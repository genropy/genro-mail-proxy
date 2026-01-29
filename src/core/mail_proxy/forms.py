# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Interactive terminal forms with Pydantic validation.

This module provides interactive forms for creating tenants, accounts,
and other entities with real-time validation using Pydantic models.

Nested model fields (like client_auth) are automatically expanded
into separate fields (client_auth_method, client_auth_token, etc.)
for easier input.

Usage in REPL:
    >>> from mail_proxy.forms import TenantForm, AccountForm
    >>> tenant_data = TenantForm().run()
    >>> account_data = AccountForm().run()

The forms display field descriptions from Pydantic annotations and
validate input in real-time, showing errors immediately.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

from pydantic import BaseModel, ValidationError
from pydantic.fields import FieldInfo
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

console = Console()


def get_field_description(field_info: FieldInfo) -> str:
    """Extract the description string from a Pydantic FieldInfo.

    Args:
        field_info: Pydantic field metadata object.

    Returns:
        str: Field description, or empty string if not defined.
    """
    if field_info.description:
        return field_info.description
    return ""


def get_field_type_hint(annotation: Any) -> str:
    """Convert a type annotation to a human-readable hint for prompts.

    Args:
        annotation: Python type annotation (str, int, Optional[str], etc.).

    Returns:
        str: Short description like "text", "integer", "yes/no", or enum choices.
    """
    origin = get_origin(annotation)

    if origin is None:
        match annotation:
            case _ if annotation is str:
                return "text"
            case _ if annotation is int:
                return "integer"
            case _ if annotation is bool:
                return "yes/no"
            case _ if annotation is float:
                return "number"
            case _ if hasattr(annotation, "__members__"):
                # Enum - show choices inline
                choices = list(annotation.__members__.keys())
                return "/".join(c.lower() for c in choices)
            case _ if hasattr(annotation, "__name__"):
                # Other classes - just show simple name
                return annotation.__name__.lower()
            case _:
                return "text"

    # Handle Optional[X]
    args = get_args(annotation)
    if origin is type(None) or (len(args) == 2 and type(None) in args):
        inner = [a for a in args if a is not type(None)][0] if args else str
        return get_field_type_hint(inner)

    # Handle List[X]
    if origin is list:
        return "list"

    return "text"


def is_optional(annotation: Any) -> bool:
    """Check if a type annotation represents an optional field.

    Args:
        annotation: Python type annotation.

    Returns:
        bool: True if annotation is Optional[T] or Union[T, None].
    """
    origin = get_origin(annotation)
    if origin is None:
        return False
    args = get_args(annotation)
    return type(None) in args


def get_inner_type(annotation: Any) -> Any:
    """Extract the inner type from Optional[X] or other union types.

    Args:
        annotation: Python type annotation.

    Returns:
        The unwrapped inner type, or the original if not a union.
    """
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = get_args(annotation)
    non_none = [a for a in args if a is not type(None)]
    return non_none[0] if non_none else str


def is_nested_model(annotation: Any) -> bool:
    """Check if annotation references a nested Pydantic BaseModel.

    Args:
        annotation: Python type annotation.

    Returns:
        bool: True if the inner type is a Pydantic BaseModel subclass.
    """
    inner = get_inner_type(annotation)
    return isinstance(inner, type) and issubclass(inner, BaseModel)


def get_nested_model_class(annotation: Any) -> type[BaseModel] | None:
    """Extract the Pydantic model class from a nested annotation.

    Args:
        annotation: Python type annotation.

    Returns:
        The BaseModel subclass, or None if not a nested model.
    """
    inner = get_inner_type(annotation)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return inner
    return None


class InteractiveForm:
    """Base class for interactive terminal forms.

    Subclasses should define:
    - model: The Pydantic model class
    - title: Form title
    - fields: List of field names to include (in order)
    - field_groups: Optional dict of group_name -> [field_names]

    Nested model fields are automatically expanded into separate fields
    with the pattern: parent_field_subfield (e.g., client_auth_method).
    """

    model: type[BaseModel]
    title: str = "Form"
    fields: list[str] = []
    field_groups: dict[str, list[str]] = {}

    def __init__(self):
        self.values: dict[str, Any] = {}
        self.errors: dict[str, str] = {}
        # Expanded fields list (with nested fields flattened)
        self._expanded_fields: list[str] = []
        # Map expanded field name -> (parent_field, subfield) for nested fields
        self._nested_map: dict[str, tuple[str, str]] = {}
        # Map parent field -> nested model class
        self._nested_models: dict[str, type[BaseModel]] = {}
        self._expand_fields()

    def _expand_fields(self) -> None:
        """Expand nested model fields into separate subfields."""
        self._expanded_fields = []
        self._nested_map = {}
        self._nested_models = {}

        for field_name in self.fields:
            # Get annotation from Pydantic model_fields (handles forward refs correctly)
            field_info = self.model.model_fields.get(field_name)
            if field_info is None:
                self._expanded_fields.append(field_name)
                continue

            annotation = field_info.annotation

            if is_nested_model(annotation):
                # This is a nested model - expand its fields
                nested_class = get_nested_model_class(annotation)
                if nested_class:
                    self._nested_models[field_name] = nested_class
                    for subfield in nested_class.model_fields:
                        expanded_name = f"{field_name}_{subfield}"
                        self._expanded_fields.append(expanded_name)
                        self._nested_map[expanded_name] = (field_name, subfield)
            else:
                # Regular field
                self._expanded_fields.append(field_name)

    def _get_field_info(self, field_name: str) -> FieldInfo | None:
        """Get Pydantic FieldInfo for a field (handles expanded nested fields)."""
        # Check if it's an expanded nested field
        if field_name in self._nested_map:
            parent_field, subfield = self._nested_map[field_name]
            nested_class = self._nested_models.get(parent_field)
            if nested_class and subfield in nested_class.model_fields:
                return nested_class.model_fields[subfield]
            return None

        # Regular field
        if field_name in self.model.model_fields:
            return self.model.model_fields[field_name]
        return None

    def _get_annotation(self, field_name: str) -> Any:
        """Get type annotation for a field (handles expanded nested fields)."""
        # Check if it's an expanded nested field
        if field_name in self._nested_map:
            parent_field, subfield = self._nested_map[field_name]
            nested_class = self._nested_models.get(parent_field)
            if nested_class:
                subfield_info = nested_class.model_fields.get(subfield)
                if subfield_info:
                    return subfield_info.annotation
            return str

        # Regular field - use model_fields for proper type resolution
        field_info = self.model.model_fields.get(field_name)
        if field_info:
            return field_info.annotation
        return str

    def _is_parent_optional(self, field_name: str) -> bool:
        """Check if the parent field of a nested subfield is optional."""
        if field_name in self._nested_map:
            parent_field, _ = self._nested_map[field_name]
            parent_info = self.model.model_fields.get(parent_field)
            if parent_info:
                return is_optional(parent_info.annotation)
        return False

    def _prompt_field(self, field_name: str, current_value: Any = None) -> Any:
        """Prompt for a single field value."""
        field_info = self._get_field_info(field_name)
        annotation = self._get_annotation(field_name)

        # Build prompt text
        description = get_field_description(field_info) if field_info else ""
        type_hint = get_field_type_hint(annotation)
        optional = is_optional(annotation) or self._is_parent_optional(field_name)
        inner_type = get_inner_type(annotation)

        # Field label with formatting
        label = field_name.replace("_", " ").title()

        # Show description if available
        if description:
            console.print(f"  [dim]{description}[/dim]")

        # Get default value
        default = None
        if field_info and field_info.default is not None:
            from pydantic_core import PydanticUndefined
            if field_info.default is not PydanticUndefined:
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
            choices_lower = [c.lower() for c in choices]
            console.print(f"    [dim]Choices: {', '.join(choices_lower)}[/dim]")
            while True:
                value = Prompt.ask(
                    f"  [cyan]{label}[/cyan] [dim]({type_hint})[/dim]",
                    default=default_str.lower() if default_str else None,
                )
                if not value and optional:
                    return None
                if value and value.lower() in choices_lower:
                    # Return the actual enum value (lowercase)
                    return value.lower()
                console.print(f"    [red]Please choose from: {', '.join(choices_lower)}[/red]")

        # Handle list fields (comma-separated input)
        if get_origin(annotation) is list or (
            is_optional(annotation) and get_origin(get_inner_type(annotation)) is list
        ):
            value = Prompt.ask(
                f"  [cyan]{label}[/cyan] [dim](comma-separated)[/dim]",
                default=default_str if default_str else None,
            )
            if not value and optional:
                return None
            if value:
                return [v.strip() for v in value.split(",") if v.strip()]
            return []

        # Default: string prompt
        value = Prompt.ask(
            f"  [cyan]{label}[/cyan] [dim]({type_hint})[/dim]",
            default=default_str if default_str else None,
        )

        if not value and optional:
            return None

        return value

    def _collect_nested_values(self) -> dict[str, Any]:
        """Collect expanded nested field values back into nested dicts."""
        result = {}

        # First, add all non-nested values
        for field_name in self.fields:
            if field_name not in self._nested_models:
                if field_name in self.values:
                    result[field_name] = self.values[field_name]

        # Then, collect nested values
        for parent_field, nested_class in self._nested_models.items():
            nested_dict = {}
            has_value = False

            for subfield in nested_class.model_fields:
                expanded_name = f"{parent_field}_{subfield}"
                if expanded_name in self.values and self.values[expanded_name] is not None:
                    nested_dict[subfield] = self.values[expanded_name]
                    has_value = True

            if has_value:
                result[parent_field] = nested_dict

        return result

    def _validate(self) -> bool:
        """Validate current values against the model."""
        try:
            # Collect nested values back into proper structure
            collected = self._collect_nested_values()
            # Filter out None values
            filtered = {k: v for k, v in collected.items() if v is not None}
            self.model(**filtered)
            self.errors = {}
            return True
        except ValidationError as e:
            self.errors = {}
            for error in e.errors():
                if error["loc"]:
                    # Handle nested field errors
                    if len(error["loc"]) > 1:
                        field = f"{error['loc'][0]}_{error['loc'][1]}"
                    else:
                        field = str(error["loc"][0])
                    self.errors[field] = error["msg"]
            return False

    def _show_summary(self) -> None:
        """Show a summary of entered values."""
        table = Table(title="Summary", show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_column("Status", justify="center")

        for field_name in self._expanded_fields:
            value = self.values.get(field_name)
            error = self.errors.get(field_name)

            # Format value for display
            if value is None:
                value_str = "[dim]-[/dim]"
            elif isinstance(value, bool):
                value_str = "[green]yes[/green]" if value else "[red]no[/red]"
            elif isinstance(value, list):
                value_str = ", ".join(str(v) for v in value) if value else "[dim]-[/dim]"
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

    def _get_expanded_group_fields(self, group_fields: list[str]) -> list[str]:
        """Get expanded field names for a group."""
        result = []
        for field_name in group_fields:
            if field_name in self._nested_models:
                # Add all expanded subfields
                nested_class = self._nested_models[field_name]
                for subfield in nested_class.model_fields:
                    result.append(f"{field_name}_{subfield}")
            else:
                result.append(field_name)
        return result

    def run(self) -> dict[str, Any] | None:
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
                expanded_group = self._get_expanded_group_fields(group_fields)
                if expanded_group:
                    console.print(f"\n[bold yellow]{group_name}[/bold yellow]")
                    for field_name in expanded_group:
                        if field_name in self._expanded_fields:
                            self.values[field_name] = self._prompt_field(field_name)
        else:
            # Prompt all fields in order
            for field_name in self._expanded_fields:
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

            match action:
                case "save" if is_valid:
                    # Return validated dict (mode="json" converts enums to strings)
                    collected = self._collect_nested_values()
                    filtered = {k: v for k, v in collected.items() if v is not None}
                    return self.model(**filtered).model_dump(mode="json", exclude_none=True)

                case "edit":
                    # Show field choices
                    field_choices = {str(i+1): f for i, f in enumerate(self._expanded_fields)}
                    console.print("\n[bold]Fields:[/bold]")
                    for num, field_name in field_choices.items():
                        label = field_name.replace("_", " ").title()
                        console.print(f"  {num}. {label}")

                    choice = Prompt.ask("Edit field number", default="1")
                    if choice in field_choices:
                        self._edit_field(field_choices[choice])
                        is_valid = self._validate()

                case "cancel":
                    console.print("[dim]Cancelled[/dim]")
                    return None


# ============================================================================
# Concrete Forms
# ============================================================================

class TenantForm(InteractiveForm):
    """Interactive form for creating a tenant."""

    from core.mail_proxy.entities.tenant.schema import TenantCreate
    model = TenantCreate
    title = "Create New Tenant"
    fields = [
        "id",
        "name",
        "client_auth",
        "client_base_url",
        "client_sync_path",
        "client_attachment_path",
        "rate_limits",
        "active",
    ]
    field_groups = {
        "Basic Info": ["id", "name", "active"],
        "Authentication": ["client_auth"],
        "Endpoints": ["client_base_url", "client_sync_path", "client_attachment_path"],
        "Rate Limits": ["rate_limits"],
    }


class AccountForm(InteractiveForm):
    """Interactive form for creating an SMTP account."""

    from core.mail_proxy.entities.account.schema import AccountCreate
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
        "ttl",
        "limit_per_minute",
        "limit_per_hour",
        "limit_per_day",
        "limit_behavior",
    ]
    field_groups = {
        "Identity": ["id", "tenant_id"],
        "SMTP Server": ["host", "port", "use_tls"],
        "Authentication": ["user", "password"],
        "Settings": ["batch_size", "ttl"],
        "Rate Limits": ["limit_per_minute", "limit_per_hour", "limit_per_day", "limit_behavior"],
    }


class MessageForm(InteractiveForm):
    """Interactive form for creating an email message."""

    from core.mail_proxy.entities.message.schema import MessageCreate
    model = MessageCreate
    title = "Create New Message"
    fields = [
        "id",
        "account_id",
        "from_addr",
        "to",
        "cc",
        "bcc",
        "reply_to",
        "return_path",
        "subject",
        "body",
        "content_type",
        "message_id",
        "priority",
        "deferred_ts",
    ]
    field_groups = {
        "Identity": ["id", "account_id"],
        "Addressing": ["from_addr", "to", "cc", "bcc", "reply_to", "return_path"],
        "Content": ["subject", "body", "content_type"],
        "Settings": ["message_id", "priority", "deferred_ts"],
    }


# ============================================================================
# Quick Functions for REPL
# ============================================================================

# Reference to the proxy client, set by REPL when connecting
_proxy = None


def set_proxy(proxy) -> None:
    """Set the proxy client for auto-save functionality."""
    global _proxy
    _proxy = proxy


def new_tenant() -> dict[str, Any] | None:
    """Interactive form to create and save a new tenant.

    Returns:
        Validated tenant dict, or None if cancelled.
    """
    data = TenantForm().run()
    if data and _proxy:
        try:
            _proxy.tenants.add(data)
            console.print(f"[green]✓[/green] Tenant '{data['id']}' saved")
        except Exception as e:
            console.print(f"[red]Error saving tenant:[/red] {e}")
    return data


def new_account() -> dict[str, Any] | None:
    """Interactive form to create and save a new SMTP account.

    Returns:
        Validated account dict, or None if cancelled.
    """
    data = AccountForm().run()
    if data and _proxy:
        try:
            _proxy.accounts.add(data)
            console.print(f"[green]✓[/green] Account '{data['id']}' saved")
        except Exception as e:
            console.print(f"[red]Error saving account:[/red] {e}")
    return data


def new_message() -> dict[str, Any] | None:
    """Interactive form to create and queue a new message.

    Returns:
        Validated message dict, or None if cancelled.
    """
    data = MessageForm().run()
    if data and _proxy:
        try:
            _proxy.messages.add([data])
            console.print("[green]✓[/green] Message queued")
        except Exception as e:
            console.print(f"[red]Error queuing message:[/red] {e}")
    return data
