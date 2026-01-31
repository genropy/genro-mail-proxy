# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Tests for cli_base: endpoint introspection and CLI command generation.

These tests verify that cli_base correctly generates Click commands from
endpoint classes, and that invoking these commands exercises the full stack
(endpoint -> table -> DB).
"""

import json

import pytest
from click.testing import CliRunner

import click

from core.mail_proxy.interface.cli_base import register_endpoint
from core.mail_proxy.proxy_base import MailProxyBase
from core.mail_proxy.proxy_config import ProxyConfig


@pytest.fixture
async def db(tmp_path):
    """Create database with schema."""
    proxy = MailProxyBase(ProxyConfig(db_path=str(tmp_path / "test.db")))
    await proxy.db.connect()
    await proxy.db.check_structure()
    yield proxy.db
    await proxy.close()


@pytest.fixture
def runner():
    """Create Click test runner."""
    return CliRunner()


# =============================================================================
# Account Endpoint Tests via cli_base
# =============================================================================

class TestAccountEndpointViaCli:
    """Test AccountEndpoint through generated CLI commands."""

    @pytest.fixture
    def cli(self, db):
        """Create CLI with account endpoint registered."""
        from core.mail_proxy.entities.account import AccountEndpoint

        accounts_table = db.table("accounts")
        endpoint = AccountEndpoint(accounts_table)

        @click.group()
        def main():
            pass

        register_endpoint(main, endpoint)
        return main

    def test_add_account_command(self, cli, runner):
        """accounts add creates account."""
        # CLI order is REVERSED from signature: port, host, tenant_id, id
        result = runner.invoke(cli, [
            "accounts", "add",
            "587", "smtp.example.com", "t1", "smtp1",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == "smtp1"
        assert data["host"] == "smtp.example.com"

    def test_add_account_with_all_options(self, cli, runner):
        """accounts add with all optional fields."""
        # CLI order: port, host, tenant_id, id
        # Optional: --user, --password, --batch-size, --ttl
        result = runner.invoke(cli, [
            "accounts", "add",
            "465", "smtp.example.com", "t1", "smtp2",
            "--user", "user@example.com",
            "--password", "secret",
            "--batch-size", "50",
            "--ttl", "600",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["user"] == "user@example.com"
        assert data["batch_size"] == 50

    def test_get_account_command(self, cli, runner):
        """accounts get returns account data."""
        # First create: port, host, tenant_id, id
        runner.invoke(cli, [
            "accounts", "add",
            "587", "smtp.example.com", "t1", "smtp1",
        ])

        # get: account_id, tenant_id (reversed from signature tenant_id, account_id)
        result = runner.invoke(cli, ["accounts", "get", "smtp1", "t1"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == "smtp1"

    def test_list_accounts_empty(self, cli, runner):
        """accounts list returns empty list."""
        result = runner.invoke(cli, ["accounts", "list", "t1"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data == []

    def test_list_accounts_returns_all(self, cli, runner):
        """accounts list returns all accounts for tenant."""
        # Create two accounts: port, host, tenant_id, id
        runner.invoke(cli, [
            "accounts", "add", "25", "a.com", "t1", "smtp1"
        ])
        runner.invoke(cli, [
            "accounts", "add", "25", "b.com", "t1", "smtp2"
        ])

        result = runner.invoke(cli, ["accounts", "list", "t1"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data) == 2

    def test_delete_account_command(self, cli, runner):
        """accounts delete removes account."""
        # Create: port, host, tenant_id, id
        runner.invoke(cli, [
            "accounts", "add", "25", "a.com", "t1", "smtp1"
        ])

        # Delete: account_id, tenant_id (reversed from signature tenant_id, account_id)
        result = runner.invoke(cli, ["accounts", "delete", "smtp1", "t1"])
        assert result.exit_code == 0, result.output

        # Verify gone
        result = runner.invoke(cli, ["accounts", "list", "t1"])
        data = json.loads(result.output)
        assert data == []


# =============================================================================
# Tenant Endpoint Tests via cli_base
# =============================================================================

class TestTenantEndpointViaCli:
    """Test TenantEndpoint through generated CLI commands."""

    @pytest.fixture
    def cli(self, db):
        """Create CLI with tenant endpoint registered."""
        from core.mail_proxy.entities.tenant import TenantEndpoint

        tenants_table = db.table("tenants")
        endpoint = TenantEndpoint(tenants_table)

        @click.group()
        def main():
            pass

        register_endpoint(main, endpoint)
        return main

    def test_add_tenant_minimal(self, cli, runner):
        """tenants add creates tenant with minimal data."""
        result = runner.invoke(cli, ["tenants", "add", "acme"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == "acme"

    def test_add_tenant_with_name(self, cli, runner):
        """tenants add with name option."""
        result = runner.invoke(cli, [
            "tenants", "add", "acme",
            "--name", "ACME Corporation"
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "ACME Corporation"

    def test_get_tenant_command(self, cli, runner):
        """tenants get returns tenant data."""
        runner.invoke(cli, ["tenants", "add", "acme", "--name", "ACME"])

        result = runner.invoke(cli, ["tenants", "get", "acme"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "ACME"

    def test_list_tenants_command(self, cli, runner):
        """tenants list returns all tenants."""
        runner.invoke(cli, ["tenants", "add", "t1"])
        runner.invoke(cli, ["tenants", "add", "t2"])

        result = runner.invoke(cli, ["tenants", "list"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data) >= 2

    def test_delete_tenant_command(self, cli, runner):
        """tenants delete removes tenant."""
        runner.invoke(cli, ["tenants", "add", "temp"])

        result = runner.invoke(cli, ["tenants", "delete", "temp"])
        assert result.exit_code == 0, result.output

    def test_update_tenant_command(self, cli, runner):
        """tenants update modifies tenant."""
        runner.invoke(cli, ["tenants", "add", "acme", "--name", "Old Name"])

        result = runner.invoke(cli, [
            "tenants", "update", "acme",
            "--name", "New Name"
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["name"] == "New Name"


# =============================================================================
# Message Endpoint Tests via cli_base
# =============================================================================

class TestMessageEndpointViaCli:
    """Test MessageEndpoint through generated CLI commands."""

    @pytest.fixture
    def cli(self, db):
        """Create CLI with all needed endpoints registered."""
        from core.mail_proxy.entities.message import MessageEndpoint
        from core.mail_proxy.entities.account import AccountEndpoint
        from core.mail_proxy.entities.tenant import TenantEndpoint

        tenants_table = db.table("tenants")
        accounts_table = db.table("accounts")
        messages_table = db.table("messages")

        @click.group()
        def main():
            pass

        register_endpoint(main, TenantEndpoint(tenants_table))
        register_endpoint(main, AccountEndpoint(accounts_table))
        register_endpoint(main, MessageEndpoint(messages_table))

        # Setup: create tenant and account
        click_runner = CliRunner()
        click_runner.invoke(main, ["tenants", "add", "t1"])
        click_runner.invoke(main, [
            "accounts", "add", "smtp1", "t1",
            "--host", "smtp.test.com", "--port", "25"
        ])

        return main

    def test_add_message_command(self, cli, runner):
        """messages add creates message."""
        # CLI order (reversed): body, subject, to, from_addr, account_id, tenant_id, id
        result = runner.invoke(cli, [
            "messages", "add",
            "Hello",  # body
            "Test",   # subject
            "recipient@test.com",  # to
            "sender@test.com",  # from_addr
            "smtp1",  # account_id
            "t1",     # tenant_id
            "msg1",   # id
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == "msg1"

    def test_get_message_command(self, cli, runner):
        """messages get returns message data."""
        # First create (reversed order)
        runner.invoke(cli, [
            "messages", "add",
            "Hi", "Test", "c@d.com", "a@b.com", "smtp1", "t1", "msg1",
        ])

        # messages get: tenant_id, message_id (reversed from signature message_id, tenant_id)
        result = runner.invoke(cli, ["messages", "get", "t1", "msg1"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["id"] == "msg1"
        assert "status" in data

    def test_list_messages_command(self, cli, runner):
        """messages list returns messages."""
        runner.invoke(cli, [
            "messages", "add",
            "Hi", "Test", "c@d.com", "a@b.com", "smtp1", "t1", "msg1",
        ])

        # messages list has all optional params (tenant_id has default None)
        result = runner.invoke(cli, ["messages", "list", "--tenant-id", "t1"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data) >= 1

    def test_count_active_command(self, cli, runner):
        """messages count-active returns count."""
        result = runner.invoke(cli, ["messages", "count-active"])
        assert result.exit_code == 0, result.output
        count = int(result.output.strip())
        assert isinstance(count, int)


# =============================================================================
# Command Generation Tests
# =============================================================================

# =============================================================================
# Instance Endpoint Tests via cli_base
# =============================================================================

class TestInstanceEndpointViaCli:
    """Test InstanceEndpoint through generated CLI commands."""

    @pytest.fixture
    def cli(self, db):
        """Create CLI with instance endpoint registered."""
        from core.mail_proxy.entities.instance import InstanceEndpoint

        instance_table = db.table("instance")
        endpoint = InstanceEndpoint(instance_table)

        @click.group()
        def main():
            pass

        register_endpoint(main, endpoint)
        return main

    def test_health_command(self, cli, runner):
        """instance health returns status ok."""
        result = runner.invoke(cli, ["instance", "health"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["status"] == "ok"

    def test_status_command(self, cli, runner):
        """instance status returns ok and active state."""
        result = runner.invoke(cli, ["instance", "status"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert "active" in data

    def test_get_command(self, cli, runner):
        """instance get returns instance configuration."""
        result = runner.invoke(cli, ["instance", "get"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_update_command(self, cli, runner):
        """instance update modifies instance configuration."""
        result = runner.invoke(cli, [
            "instance", "update",
            "--name", "test-instance",
        ])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_run_now_command(self, cli, runner):
        """instance run-now triggers dispatch."""
        result = runner.invoke(cli, ["instance", "run-now"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True

    def test_suspend_command(self, cli, runner):
        """instance suspend pauses sending for tenant."""
        result = runner.invoke(cli, ["instance", "suspend", "t1"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["tenant_id"] == "t1"

    def test_activate_command(self, cli, runner):
        """instance activate resumes sending for tenant."""
        result = runner.invoke(cli, ["instance", "activate", "t1"])
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["ok"] is True
        assert data["tenant_id"] == "t1"


# =============================================================================
# Command Generation Tests
# =============================================================================

class TestAnnotationToClickType:
    """Tests for _annotation_to_click_type() helper to cover all type conversions."""

    def test_empty_annotation_returns_str(self):
        """Empty annotation returns str type."""
        import inspect
        from core.mail_proxy.interface.cli_base import _annotation_to_click_type
        result = _annotation_to_click_type(inspect.Parameter.empty)
        assert result is str

    def test_any_annotation_returns_str(self):
        """Any annotation returns str type."""
        from typing import Any
        from core.mail_proxy.interface.cli_base import _annotation_to_click_type
        result = _annotation_to_click_type(Any)
        assert result is str

    def test_none_type_returns_str(self):
        """NoneType returns str."""
        from core.mail_proxy.interface.cli_base import _annotation_to_click_type
        result = _annotation_to_click_type(type(None))
        assert result is str

    def test_int_annotation(self):
        """int annotation returns int type."""
        from core.mail_proxy.interface.cli_base import _annotation_to_click_type
        result = _annotation_to_click_type(int)
        assert result is int

    def test_bool_annotation(self):
        """bool annotation returns bool type."""
        from core.mail_proxy.interface.cli_base import _annotation_to_click_type
        result = _annotation_to_click_type(bool)
        assert result is bool

    def test_float_annotation(self):
        """float annotation returns float type."""
        from core.mail_proxy.interface.cli_base import _annotation_to_click_type
        result = _annotation_to_click_type(float)
        assert result is float

    def test_str_annotation(self):
        """str annotation returns str type."""
        from core.mail_proxy.interface.cli_base import _annotation_to_click_type
        result = _annotation_to_click_type(str)
        assert result is str

    def test_optional_int_returns_int(self):
        """Optional[int] (int | None) returns int type."""
        from core.mail_proxy.interface.cli_base import _annotation_to_click_type
        result = _annotation_to_click_type(int | None)
        assert result is int

    def test_literal_returns_choice(self):
        """Literal annotation returns click.Choice."""
        from typing import Literal
        from core.mail_proxy.interface.cli_base import _annotation_to_click_type
        result = _annotation_to_click_type(Literal["plain", "html"])
        assert isinstance(result, click.Choice)
        assert list(result.choices) == ["plain", "html"]


class TestCommandGeneration:
    """Test that cli_base generates correct commands."""

    def test_commands_created_for_all_methods(self, db):
        """All endpoint methods become commands."""
        from core.mail_proxy.entities.account import AccountEndpoint

        accounts_table = db.table("accounts")
        endpoint = AccountEndpoint(accounts_table)

        @click.group()
        def main():
            pass

        register_endpoint(main, endpoint)

        # Check that accounts group exists
        assert "accounts" in main.commands
        accounts_group = main.commands["accounts"]

        # Check commands exist
        cmd_names = list(accounts_group.commands.keys())
        assert "add" in cmd_names
        assert "get" in cmd_names
        assert "list" in cmd_names
        assert "delete" in cmd_names

    def test_boolean_flags(self, db):
        """Boolean parameters become flags."""
        from core.mail_proxy.entities.account import AccountEndpoint

        accounts_table = db.table("accounts")
        endpoint = AccountEndpoint(accounts_table)

        @click.group()
        def main():
            pass

        register_endpoint(main, endpoint)

        add_cmd = main.commands["accounts"].commands["add"]
        param_names = [p.name for p in add_cmd.params]
        assert "use_tls" in param_names

    def test_underscore_to_hyphen(self, db):
        """Method names with underscores become hyphenated commands."""
        from core.mail_proxy.entities.message import MessageEndpoint

        messages_table = db.table("messages")
        endpoint = MessageEndpoint(messages_table)

        @click.group()
        def main():
            pass

        register_endpoint(main, endpoint)

        cmd_names = list(main.commands["messages"].commands.keys())
        # count_active → count-active
        assert "count-active" in cmd_names
