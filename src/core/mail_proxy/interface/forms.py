# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Interactive terminal forms with dynamic schema generation.

This module provides Rich-based interactive forms for entity creation
with real-time validation. Schemas are generated dynamically via
introspection from endpoint method signatures - no hardcoded forms.

Components:
    DynamicForm: Interactive form generated from endpoint method signature.
    create_form: Factory function to create forms from endpoints.
    new_tenant: Pre-configured form for tenant creation.
    new_account: Pre-configured form for account creation.
    new_message: Pre-configured form for message creation.

Example:
    Create and run a form from an endpoint::

        from core.mail_proxy.interface.forms import create_form
        from core.mail_proxy.entities.tenant import TenantEndpoint

        endpoint = TenantEndpoint(table)
        form = create_form(endpoint, "add", title="New Tenant")
        data = form.run()  # Interactive prompts
        if data:
            print(f"Created: {data}")

    Use pre-configured forms in REPL::

        from core.mail_proxy.interface.forms import set_proxy, new_tenant
        set_proxy(proxy_client, dispatcher)
        new_tenant()  # Interactive form with auto-save

Note:
    Nested Pydantic models (e.g., ClientAuth) are automatically expanded
    into separate fields (client_auth_method, client_auth_token) for
    easier terminal input, then collapsed back on validation.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, get_args, get_origin, get_type_hints

from pydantic import BaseModel, ValidationError, create_model
from pydantic.fields import FieldInfo
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

if TYPE_CHECKING:
    from .endpoint_base import BaseEndpoint

console = Console()


def get_field_description(field_info: FieldInfo | None) -> str:
    """Extract description from Pydantic FieldInfo for prompt display."""
    if field_info and field_info.description:
        return field_info.description
    return ""


def get_field_type_hint(annotation: Any) -> str:
    """Convert type annotation to human-readable hint (e.g., 'integer', 'yes/no')."""
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
                choices = list(annotation.__members__.keys())
                return "/".join(c.lower() for c in choices)
            case _ if hasattr(annotation, "__name__"):
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
    """Check if type is Optional[X] (allows None)."""
    origin = get_origin(annotation)
    if origin is None:
        return False
    args = get_args(annotation)
    return type(None) in args


def get_inner_type(annotation: Any) -> Any:
    """Extract inner type from Optional[X] → X."""
    origin = get_origin(annotation)
    if origin is None:
        return annotation
    args = get_args(annotation)
    non_none = [a for a in args if a is not type(None)]
    return non_none[0] if non_none else str


def is_nested_model(annotation: Any) -> bool:
    """Check if annotation is a nested Pydantic BaseModel (requires expansion)."""
    inner = get_inner_type(annotation)
    return isinstance(inner, type) and issubclass(inner, BaseModel)


def get_nested_model_class(annotation: Any) -> type[BaseModel] | None:
    """Extract Pydantic BaseModel class from nested annotation."""
    inner = get_inner_type(annotation)
    if isinstance(inner, type) and issubclass(inner, BaseModel):
        return inner
    return None


class DynamicForm:
    """Interactive Rich form generated from endpoint method signature.

    Introspects method parameters to determine fields, types, defaults,
    and validation rules. Nested Pydantic models are expanded into
    separate fields for easier terminal input.

    Attributes:
        title: Form title displayed in panel header.
        method: The endpoint method this form is for.
        model: Dynamically created Pydantic model for validation.
        values: Current field values entered by user.
        errors: Validation errors by field name.

    Example:
        ::

            form = DynamicForm(
                title="Create Account",
                method=endpoint.add,
                model=AddAccountModel,
            )
            result = form.run()  # Returns validated dict or None
    """

    def __init__(
        self,
        title: str,
        method: Callable,
        model: type[BaseModel],
        field_groups: dict[str, list[str]] | None = None,
    ):
        self.title = title
        self.method = method
        self.model = model
        self.field_groups = field_groups or {}
        self.values: dict[str, Any] = {}
        self.errors: dict[str, str] = {}

        # Get fields from model
        self.fields = list(model.model_fields.keys())

        # Expanded fields list (with nested fields flattened)
        self._expanded_fields: list[str] = []
        # Map expanded field name -> (parent_field, subfield) for nested fields
        self._nested_map: dict[str, tuple[str, str]] = {}
        # Map parent field -> nested model class
        self._nested_models: dict[str, type[BaseModel]] = {}
        self._expand_fields()

    def _expand_fields(self) -> None:
        """Expand nested models into flat fields (client_auth → client_auth_method, etc.)."""
        self._expanded_fields = []
        self._nested_map = {}
        self._nested_models = {}

        for field_name in self.fields:
            field_info = self.model.model_fields.get(field_name)
            if field_info is None:
                self._expanded_fields.append(field_name)
                continue

            annotation = field_info.annotation

            if is_nested_model(annotation):
                nested_class = get_nested_model_class(annotation)
                if nested_class:
                    self._nested_models[field_name] = nested_class
                    for subfield in nested_class.model_fields:
                        expanded_name = f"{field_name}_{subfield}"
                        self._expanded_fields.append(expanded_name)
                        self._nested_map[expanded_name] = (field_name, subfield)
            else:
                self._expanded_fields.append(field_name)

    def _get_field_info(self, field_name: str) -> FieldInfo | None:
        """Get FieldInfo for field, resolving expanded nested field names."""
        if field_name in self._nested_map:
            parent_field, subfield = self._nested_map[field_name]
            nested_class = self._nested_models.get(parent_field)
            if nested_class and subfield in nested_class.model_fields:
                return nested_class.model_fields[subfield]
            return None

        if field_name in self.model.model_fields:
            return self.model.model_fields[field_name]
        return None

    def _get_annotation(self, field_name: str) -> Any:
        """Get type annotation, resolving expanded nested field names."""
        if field_name in self._nested_map:
            parent_field, subfield = self._nested_map[field_name]
            nested_class = self._nested_models.get(parent_field)
            if nested_class:
                subfield_info = nested_class.model_fields.get(subfield)
                if subfield_info:
                    return subfield_info.annotation
            return str

        field_info = self.model.model_fields.get(field_name)
        if field_info:
            return field_info.annotation
        return str

    def _is_parent_optional(self, field_name: str) -> bool:
        """Check if parent of nested subfield is optional (makes subfield optional too)."""
        if field_name in self._nested_map:
            parent_field, _ = self._nested_map[field_name]
            parent_info = self.model.model_fields.get(parent_field)
            if parent_info:
                return is_optional(parent_info.annotation)
        return False

    def _prompt_field(self, field_name: str, current_value: Any = None) -> Any:
        """Prompt user for field value with type-appropriate input handling."""
        field_info = self._get_field_info(field_name)
        annotation = self._get_annotation(field_name)

        description = get_field_description(field_info)
        type_hint = get_field_type_hint(annotation)
        optional = is_optional(annotation) or self._is_parent_optional(field_name)
        inner_type = get_inner_type(annotation)

        label = field_name.replace("_", " ").title()

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
                    default=default_str if default_str else "",
                )
                if not value and optional:
                    return None
                if not value:
                    console.print("    [red]Please enter a valid integer[/red]")
                    continue
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
        """Collapse expanded fields back to nested dicts for validation."""
        result = {}

        for field_name in self.fields:
            if field_name not in self._nested_models:
                if field_name in self.values:
                    result[field_name] = self.values[field_name]

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
        """Validate current values against Pydantic model."""
        try:
            collected = self._collect_nested_values()
            filtered = {k: v for k, v in collected.items() if v is not None}
            self.model(**filtered)
            self.errors = {}
            return True
        except ValidationError as e:
            self.errors = {}
            for error in e.errors():
                loc = list(error["loc"])
                if loc:
                    if len(loc) > 1:
                        field = f"{loc[0]}_{loc[1]}"
                    else:
                        field = str(loc[0])
                    self.errors[field] = error["msg"]
            return False

    def _show_summary(self) -> None:
        """Display Rich table with current values and validation status."""
        table = Table(title="Summary", show_header=True)
        table.add_column("Field", style="cyan")
        table.add_column("Value")
        table.add_column("Status", justify="center")

        for field_name in self._expanded_fields:
            value = self.values.get(field_name)
            error = self.errors.get(field_name)

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
        """Re-prompt for a specific field value."""
        current = self.values.get(field_name)
        self.values[field_name] = self._prompt_field(field_name, current)
        self._validate()

    def _get_expanded_group_fields(self, group_fields: list[str]) -> list[str]:
        """Expand field group to include nested subfields."""
        result = []
        for field_name in group_fields:
            if field_name in self._nested_models:
                nested_class = self._nested_models[field_name]
                for subfield in nested_class.model_fields:
                    result.append(f"{field_name}_{subfield}")
            else:
                result.append(field_name)
        return result

    def run(self) -> dict[str, Any] | None:
        """Run interactive form loop until save or cancel.

        Returns:
            Validated dict ready for API call, or None if cancelled.
        """
        console.print()
        console.print(Panel(f"[bold]{self.title}[/bold]", expand=False))
        console.print()

        if self.field_groups:
            for group_name, group_fields in self.field_groups.items():
                expanded_group = self._get_expanded_group_fields(group_fields)
                if expanded_group:
                    console.print(f"\n[bold yellow]{group_name}[/bold yellow]")
                    for field_name in expanded_group:
                        if field_name in self._expanded_fields:
                            self.values[field_name] = self._prompt_field(field_name)
        else:
            for field_name in self._expanded_fields:
                self.values[field_name] = self._prompt_field(field_name)

        is_valid = self._validate()

        while True:
            console.print()
            self._show_summary()
            console.print()

            if is_valid:
                action = Prompt.ask(
                    "[bold]Action[/bold]", choices=["save", "edit", "cancel"], default="save"
                )
            else:
                console.print("[red]Please fix the errors above[/red]")
                action = Prompt.ask(
                    "[bold]Action[/bold]", choices=["edit", "cancel"], default="edit"
                )

            match action:
                case "save" if is_valid:
                    collected = self._collect_nested_values()
                    filtered = {k: v for k, v in collected.items() if v is not None}
                    return self.model(**filtered).model_dump(mode="json", exclude_none=True)

                case "edit":
                    field_choices = {str(i + 1): f for i, f in enumerate(self._expanded_fields)}
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


def _create_model_from_method(method: Callable) -> type[BaseModel]:
    """Create Pydantic model dynamically from method signature for validation."""
    sig = inspect.signature(method)

    try:
        hints = get_type_hints(method)
    except Exception:
        hints = {}

    fields = {}
    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue

        annotation = hints.get(param_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            annotation = Any

        if param.default is inspect.Parameter.empty:
            fields[param_name] = (annotation, ...)
        else:
            fields[param_name] = (annotation, param.default)

    model_name = f"{method.__name__.title().replace('_', '')}Form"
    return create_model(model_name, **fields)


def create_form(
    endpoint: BaseEndpoint,
    method_name: str,
    title: str | None = None,
    field_groups: dict[str, list[str]] | None = None,
) -> DynamicForm:
    """Create interactive form from endpoint method via introspection.

    Args:
        endpoint: Endpoint instance with the target method.
        method_name: Method name to create form for (e.g., "add").
        title: Optional custom title (default: "{Endpoint} - {Method}").
        field_groups: Optional dict grouping fields for organized display.

    Returns:
        DynamicForm ready for run().

    Example:
        ::

            endpoint = TenantEndpoint(table)
            form = create_form(
                endpoint, "add",
                title="Create Tenant",
                field_groups={"Basic": ["id", "name"], "Settings": ["active"]}
            )
            data = form.run()
    """
    method = getattr(endpoint, method_name)
    model = _create_model_from_method(method)

    if title is None:
        title = f"{endpoint.name.title()} - {method_name.replace('_', ' ').title()}"

    return DynamicForm(
        title=title,
        method=method,
        model=model,
        field_groups=field_groups,
    )


# Module-level references for auto-save forms (set by REPL)
_proxy = None
_dispatcher = None


def set_proxy(proxy: Any, dispatcher: Any = None) -> None:
    """Configure proxy client and dispatcher for auto-save forms.

    Must be called before using new_tenant(), new_account(), new_message().

    Args:
        proxy: MailProxyClient instance for API calls.
        dispatcher: EndpointDispatcher for form schema generation.
    """
    global _proxy, _dispatcher
    _proxy = proxy
    _dispatcher = dispatcher


def new_tenant() -> dict[str, Any] | None:
    """Interactive form to create and save a tenant. Requires set_proxy() first."""
    if not _dispatcher:
        console.print("[red]Error:[/red] No dispatcher configured. Use set_proxy() first.")
        return None

    endpoint = _dispatcher.get_endpoint("tenants")
    form = create_form(
        endpoint,
        "add",
        title="Create New Tenant",
        field_groups={
            "Basic Info": ["tenant_id", "name", "active"],
            "Authentication": ["client_auth"],
            "Endpoints": ["client_base_url", "client_sync_path", "client_attachment_path"],
            "Rate Limits": ["rate_limits"],
        },
    )
    data = form.run()

    if data and _proxy:
        try:
            _proxy.tenants.add(data)
            console.print(f"[green]✓[/green] Tenant '{data.get('tenant_id', 'unknown')}' saved")
        except Exception as e:
            console.print(f"[red]Error saving tenant:[/red] {e}")
    return data


def new_account() -> dict[str, Any] | None:
    """Interactive form to create and save an SMTP account. Requires set_proxy() first."""
    if not _dispatcher:
        console.print("[red]Error:[/red] No dispatcher configured. Use set_proxy() first.")
        return None

    endpoint = _dispatcher.get_endpoint("accounts")
    form = create_form(
        endpoint,
        "add",
        title="Create New SMTP Account",
        field_groups={
            "Identity": ["account_id", "tenant_id"],
            "SMTP Server": ["host", "port", "use_tls"],
            "Authentication": ["user", "password"],
            "Settings": ["batch_size", "ttl"],
            "Rate Limits": [
                "limit_per_minute",
                "limit_per_hour",
                "limit_per_day",
                "limit_behavior",
            ],
        },
    )
    data = form.run()

    if data and _proxy:
        try:
            _proxy.accounts.add(data)
            console.print(f"[green]✓[/green] Account '{data.get('account_id', 'unknown')}' saved")
        except Exception as e:
            console.print(f"[red]Error saving account:[/red] {e}")
    return data


def new_message() -> dict[str, Any] | None:
    """Interactive form to create and queue a message. Requires set_proxy() first."""
    if not _dispatcher:
        console.print("[red]Error:[/red] No dispatcher configured. Use set_proxy() first.")
        return None

    endpoint = _dispatcher.get_endpoint("messages")
    form = create_form(
        endpoint,
        "add",
        title="Create New Message",
        field_groups={
            "Identity": ["message_id", "account_id"],
            "Addressing": ["from_addr", "to", "cc", "bcc", "reply_to", "return_path"],
            "Content": ["subject", "body", "content_type"],
            "Settings": ["priority", "deferred_ts"],
        },
    )
    data = form.run()

    if data and _proxy:
        try:
            _proxy.messages.add([data])
            console.print("[green]✓[/green] Message queued")
        except Exception as e:
            console.print(f"[red]Error queuing message:[/red] {e}")
    return data


__all__ = [
    "DynamicForm",
    "create_form",
    "set_proxy",
    "new_tenant",
    "new_account",
    "new_message",
]
