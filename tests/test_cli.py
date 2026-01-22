"""Tests for CLI commands and helper functions."""

import json
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
from click.testing import CliRunner

from async_mail_service.cli import (
    main,
    get_persistence,
    run_async,
    print_error,
    print_success,
    _get_instance_dir,
    _get_pid_file,
    _is_instance_running,
    _write_pid_file,
    _remove_pid_file,
    _generate_api_token,
    _ensure_instance,
    _get_instance_config,
    _stop_instance,
)


# --- Helper function tests ---

class TestHelperFunctions:
    """Tests for CLI helper functions."""

    def test_get_persistence(self, tmp_path):
        """Test get_persistence creates a Persistence instance."""
        db_path = str(tmp_path / "test.db")
        persistence = get_persistence(db_path)

        from async_mail_service.persistence import Persistence
        assert isinstance(persistence, Persistence)

    def test_run_async(self):
        """Test run_async executes coroutine synchronously."""
        async def async_func():
            return 42

        result = run_async(async_func())
        assert result == 42

    def test_generate_api_token(self):
        """Test _generate_api_token generates secure tokens."""
        token1 = _generate_api_token()
        token2 = _generate_api_token()

        # Tokens should be non-empty strings
        assert isinstance(token1, str)
        assert len(token1) > 20

        # Tokens should be unique
        assert token1 != token2

    def test_get_instance_dir(self, tmp_path):
        """Test _get_instance_dir returns correct path."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            instance_dir = _get_instance_dir("myserver")

        expected = tmp_path / ".mail-proxy" / "myserver"
        assert instance_dir == expected

    def test_get_pid_file(self, tmp_path):
        """Test _get_pid_file returns correct path."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            pid_file = _get_pid_file("myserver")

        expected = tmp_path / ".mail-proxy" / "myserver" / "server.pid"
        assert pid_file == expected


class TestPidFileManagement:
    """Tests for PID file management."""

    def test_write_and_read_pid_file(self, tmp_path):
        """Test writing and reading PID file."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance directory
            instance_dir = tmp_path / ".mail-proxy" / "testserver"
            instance_dir.mkdir(parents=True)

            # Write PID file
            _write_pid_file("testserver", pid=12345, port=8000, host="0.0.0.0")

            # Verify file contents
            pid_file = instance_dir / "server.pid"
            assert pid_file.exists()

            data = json.loads(pid_file.read_text())
            assert data["pid"] == 12345
            assert data["port"] == 8000
            assert data["host"] == "0.0.0.0"
            assert "started_at" in data

    def test_remove_pid_file(self, tmp_path):
        """Test removing PID file."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance directory and PID file
            instance_dir = tmp_path / ".mail-proxy" / "testserver"
            instance_dir.mkdir(parents=True)
            pid_file = instance_dir / "server.pid"
            pid_file.write_text('{"pid": 123}')

            assert pid_file.exists()

            # Remove PID file
            _remove_pid_file("testserver")

            assert not pid_file.exists()

    def test_remove_pid_file_nonexistent(self, tmp_path):
        """Test removing nonexistent PID file doesn't raise error."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Should not raise
            _remove_pid_file("nonexistent")

    def test_is_instance_running_no_pid_file(self, tmp_path):
        """Test _is_instance_running when no PID file exists."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            is_running, pid, port = _is_instance_running("nonexistent")

        assert is_running is False
        assert pid is None
        assert port is None

    def test_is_instance_running_corrupt_pid_file(self, tmp_path):
        """Test _is_instance_running with corrupt PID file."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create corrupt PID file
            instance_dir = tmp_path / ".mail-proxy" / "testserver"
            instance_dir.mkdir(parents=True)
            pid_file = instance_dir / "server.pid"
            pid_file.write_text("not valid json")

            is_running, pid, port = _is_instance_running("testserver")

        assert is_running is False
        assert pid is None

    def test_is_instance_running_dead_process(self, tmp_path):
        """Test _is_instance_running when process is not running."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create PID file with non-existent PID
            instance_dir = tmp_path / ".mail-proxy" / "testserver"
            instance_dir.mkdir(parents=True)
            pid_file = instance_dir / "server.pid"
            pid_file.write_text(json.dumps({"pid": 999999999, "port": 8000}))

            is_running, pid, port = _is_instance_running("testserver")

        assert is_running is False

    def test_is_instance_running_live_process(self, tmp_path):
        """Test _is_instance_running when process is running."""
        current_pid = os.getpid()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create PID file with current process PID (guaranteed to be running)
            instance_dir = tmp_path / ".mail-proxy" / "testserver"
            instance_dir.mkdir(parents=True)
            pid_file = instance_dir / "server.pid"
            pid_file.write_text(json.dumps({"pid": current_pid, "port": 8000}))

            is_running, pid, port = _is_instance_running("testserver")

        assert is_running is True
        assert pid == current_pid
        assert port == 8000


class TestInstanceConfig:
    """Tests for instance config management (database-based)."""

    def test_ensure_instance_creates_new(self, tmp_path):
        """Test _ensure_instance creates new instance with config in DB."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            config = _ensure_instance("newserver", port=9000, host="127.0.0.1")

        # Check DB file was created
        db_path = Path(config["db_path"])
        assert db_path.exists()

        # Check config values
        assert config["name"] == "newserver"
        assert config["port"] == 9000
        assert config["host"] == "127.0.0.1"
        assert config["api_token"] is not None
        assert len(config["api_token"]) > 20  # Token should be generated

    def test_ensure_instance_existing(self, tmp_path):
        """Test _ensure_instance doesn't overwrite existing token."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create first instance
            config1 = _ensure_instance("existingserver", port=9000, host="127.0.0.1")
            original_token = config1["api_token"]

            # Call ensure again - should not overwrite token
            config2 = _ensure_instance("existingserver", port=9000, host="127.0.0.1")

        # Should still have original token
        assert config2["api_token"] == original_token

    def test_get_instance_config_existing(self, tmp_path):
        """Test _get_instance_config reads config from DB."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance first
            _ensure_instance("testserver", port=8080, host="0.0.0.0")

            # Now read config
            config = _get_instance_config("testserver")

        assert config["name"] == "testserver"
        assert config["host"] == "0.0.0.0"
        assert config["port"] == 8080
        assert config["api_token"] is not None

    def test_get_instance_config_not_found(self, tmp_path):
        """Test _get_instance_config returns None when not found."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            config = _get_instance_config("nonexistent")

        assert config is None


class TestStopInstance:
    """Tests for _stop_instance function."""

    def test_stop_instance_not_running(self, tmp_path):
        """Test stopping an instance that's not running."""
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            result = _stop_instance("nonexistent")

        assert result is False

    @patch("os.kill")
    def test_stop_instance_permission_error(self, mock_kill, tmp_path):
        """Test stopping instance with permission error."""
        mock_kill.side_effect = PermissionError("No permission")

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create PID file
            instance_dir = tmp_path / ".mail-proxy" / "testserver"
            instance_dir.mkdir(parents=True)
            pid_file = instance_dir / "server.pid"
            pid_file.write_text(json.dumps({"pid": 12345, "port": 8000}))

            # Mock _is_instance_running to return running
            with patch("async_mail_service.cli._is_instance_running", return_value=(True, 12345, 8000)):
                result = _stop_instance("testserver")

        assert result is False


# --- CLI Command Tests ---

class TestListCommand:
    """Tests for the list (instances) command."""

    def test_list_no_instances(self, tmp_path):
        """Test listing instances when none exist."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["list"])

        assert result.exit_code == 0
        assert "No instances configured" in result.output

    def test_list_instances(self, tmp_path):
        """Test listing instances."""
        runner = CliRunner()

        # Create instance with config in database
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            _ensure_instance("testserver", port=8000, host="0.0.0.0")
            result = runner.invoke(main, ["list"])

        assert result.exit_code == 0
        assert "testserver" in result.output


class TestInstanceCommands:
    """Tests for instance-level commands."""

    def test_instance_help(self, tmp_path):
        """Test instance help shows available commands."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["testserver", "--help"])

        assert result.exit_code == 0
        assert "tenants" in result.output
        assert "stats" in result.output

    def test_instance_shows_help(self, tmp_path):
        """Test instance without subcommand shows help."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            result = runner.invoke(main, ["nonexistent"])

        # Now shows help with available commands
        assert "Commands:" in result.output
        assert "tenants" in result.output


class TestTenantsCommands:
    """Tests for tenants commands with new hierarchical structure."""

    def test_tenants_list_empty(self, tmp_path):
        """Test tenants list when no tenants exist."""
        runner = CliRunner()

        # Create instance with database
        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            _ensure_instance("testserver", port=8080, host="0.0.0.0")
            result = runner.invoke(main, ["testserver", "tenants", "list"])

        assert result.exit_code == 0
        assert "No tenants found" in result.output

    def test_tenants_add_and_list(self, tmp_path):
        """Test adding and listing tenants."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance with database
            _ensure_instance("testserver", port=8080, host="0.0.0.0")

            # Add tenant with all required params to avoid interactive prompts
            result = runner.invoke(main, [
                "testserver", "tenants", "add", "test-tenant",
                "--name", "Test Tenant",
                "--base-url", "",
                "--auth-method", "none",
                "--rate-limit-hourly", "0",
                "--rate-limit-daily", "0"
            ])
            assert result.exit_code == 0

            # List tenants
            result = runner.invoke(main, ["testserver", "tenants", "list"])
            assert "test-tenant" in result.output

    def test_tenants_show(self, tmp_path):
        """Test showing tenant details."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance with database
            _ensure_instance("testserver", port=8080, host="0.0.0.0")

            # Add tenant with all required params to avoid interactive prompts
            runner.invoke(main, [
                "testserver", "tenants", "add", "show-tenant",
                "--name", "Show Test",
                "--base-url", "",
                "--auth-method", "none",
                "--rate-limit-hourly", "0",
                "--rate-limit-daily", "0"
            ])

            # Show tenant
            result = runner.invoke(main, [
                "testserver", "tenants", "show", "show-tenant"
            ])

        assert result.exit_code == 0
        assert "show-tenant" in result.output
        assert "Show Test" in result.output

    def test_tenants_delete(self, tmp_path):
        """Test deleting tenant."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance with database
            _ensure_instance("testserver", port=8080, host="0.0.0.0")

            # Add tenant with all required params to avoid interactive prompts
            runner.invoke(main, [
                "testserver", "tenants", "add", "delete-tenant",
                "--name", "Delete Test",
                "--base-url", "",
                "--auth-method", "none",
                "--rate-limit-hourly", "0",
                "--rate-limit-daily", "0"
            ])

            # Delete tenant
            result = runner.invoke(main, [
                "testserver", "tenants", "delete", "delete-tenant", "--force"
            ])
            assert result.exit_code == 0

            # Verify deleted
            result = runner.invoke(main, ["testserver", "tenants", "list"])
            assert "delete-tenant" not in result.output


class TestTenantLevelCommands:
    """Tests for tenant-level commands (accounts, messages)."""

    def test_accounts_help(self, tmp_path):
        """Test accounts help."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance with database and tenant
            _ensure_instance("testserver", port=8080, host="0.0.0.0")

            # Add tenant first with all required params
            runner.invoke(main, [
                "testserver", "tenants", "add", "test-tenant",
                "--name", "Test Tenant",
                "--base-url", "",
                "--auth-method", "none",
                "--rate-limit-hourly", "0",
                "--rate-limit-daily", "0"
            ])

            # Get accounts help
            result = runner.invoke(main, [
                "testserver", "test-tenant", "accounts", "--help"
            ])

        assert result.exit_code == 0
        assert "list" in result.output
        assert "add" in result.output

    def test_accounts_list_empty(self, tmp_path):
        """Test accounts list when empty."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance with database and tenant
            _ensure_instance("testserver", port=8080, host="0.0.0.0")

            # Add tenant first with all required params
            runner.invoke(main, [
                "testserver", "tenants", "add", "test-tenant",
                "--name", "Test Tenant",
                "--base-url", "",
                "--auth-method", "none",
                "--rate-limit-hourly", "0",
                "--rate-limit-daily", "0"
            ])

            # List accounts
            result = runner.invoke(main, [
                "testserver", "test-tenant", "accounts", "list"
            ])

        assert result.exit_code == 0
        assert "No accounts found" in result.output

    def test_accounts_add_and_list(self, tmp_path):
        """Test adding and listing accounts."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance with database
            _ensure_instance("testserver", port=8080, host="0.0.0.0")

            # Add tenant first with all required params
            runner.invoke(main, [
                "testserver", "tenants", "add", "test-tenant",
                "--name", "Test Tenant",
                "--base-url", "",
                "--auth-method", "none",
                "--rate-limit-hourly", "0",
                "--rate-limit-daily", "0"
            ])

            # Add account with all required params to avoid interactive prompts
            result = runner.invoke(main, [
                "testserver", "test-tenant", "accounts", "add", "smtp-1",
                "--host", "smtp.example.com",
                "--port", "587",
                "--user", "",
                "--tls",
                "--batch-size", "10",
                "--ttl", "300",
                "--limit-minute", "0",
                "--limit-hour", "0",
                "--limit-day", "0",
                "--limit-behavior", "defer"
            ])
            assert result.exit_code == 0

            # List accounts
            result = runner.invoke(main, [
                "testserver", "test-tenant", "accounts", "list"
            ])
            assert "smtp-1" in result.output
            assert "smtp.example.com" in result.output

    def test_messages_list_empty(self, tmp_path):
        """Test messages list when empty."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance with database and tenant
            _ensure_instance("testserver", port=8080, host="0.0.0.0")

            # Add tenant first with all required params
            runner.invoke(main, [
                "testserver", "tenants", "add", "test-tenant",
                "--name", "Test Tenant",
                "--base-url", "",
                "--auth-method", "none",
                "--rate-limit-hourly", "0",
                "--rate-limit-daily", "0"
            ])

            # List messages
            result = runner.invoke(main, [
                "testserver", "test-tenant", "messages", "list"
            ])

        assert result.exit_code == 0
        assert "No messages found" in result.output


class TestStatsCommand:
    """Tests for stats command."""

    def test_stats_empty(self, tmp_path):
        """Test stats with empty database."""
        runner = CliRunner()

        with patch("async_mail_service.cli.Path.home", return_value=tmp_path):
            # Create instance with database
            _ensure_instance("testserver", port=8080, host="0.0.0.0")

            result = runner.invoke(main, ["testserver", "stats"])

        assert result.exit_code == 0
        assert "Tenants:" in result.output
        assert "Accounts:" in result.output
        assert "Messages:" in result.output
