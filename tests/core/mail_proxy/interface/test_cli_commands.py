# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for CLI commands."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from core.mail_proxy.interface.cli_commands import (
    _run_async,
    add_connect_command,
    add_stats_command,
    add_send_command,
    add_token_command,
    add_run_now_command,
)


class MockDb:
    """Mock database for CLI tests."""

    def __init__(self):
        self._tables = {
            "tenants": MagicMock(),
            "accounts": MagicMock(),
            "messages": MagicMock(),
            "instance": MagicMock(),
        }

    def table(self, name):
        if name not in self._tables:
            self._tables[name] = MagicMock()
        return self._tables[name]


class TestRunAsync:
    """Tests for _run_async helper."""

    def test_run_async_executes_coroutine(self):
        """_run_async executes and returns coroutine result."""
        async def coro():
            return "result"

        result = _run_async(coro())
        assert result == "result"


class TestAddStatsCommand:
    """Tests for add_stats_command."""

    @pytest.fixture
    def cli_group(self):
        @click.group()
        def cli():
            pass
        return cli

    @pytest.fixture
    def mock_db(self):
        db = MockDb()
        db.table("tenants").list_all = AsyncMock(return_value=[
            {"id": "t1", "name": "Tenant 1"},
            {"id": "t2", "name": "Tenant 2"},
        ])
        db.table("accounts").list_all = AsyncMock(return_value=[
            {"id": "a1", "tenant_id": "t1"},
        ])
        db.table("messages").list_all = AsyncMock(return_value=[
            {"id": "m1", "smtp_ts": None, "error_ts": None},  # pending
            {"id": "m2", "smtp_ts": 1234567890, "error_ts": None},  # sent
            {"id": "m3", "smtp_ts": None, "error_ts": 1234567890},  # error
        ])
        return db

    def test_stats_json_output(self, cli_group, mock_db):
        """stats --json outputs valid JSON."""
        add_stats_command(cli_group, mock_db)
        runner = CliRunner()
        result = runner.invoke(cli_group, ["stats", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["tenants"] == 2
        assert data["accounts"] == 1

    def test_stats_text_output(self, cli_group, mock_db):
        """stats outputs formatted text."""
        add_stats_command(cli_group, mock_db)
        runner = CliRunner()
        result = runner.invoke(cli_group, ["stats"])
        assert result.exit_code == 0
        assert "Tenants:" in result.output
        assert "Accounts:" in result.output


class TestAddSendCommand:
    """Tests for add_send_command."""

    @pytest.fixture
    def cli_group(self):
        @click.group()
        def cli():
            pass
        return cli

    @pytest.fixture
    def mock_db(self):
        db = MockDb()
        db.table("accounts").list_all = AsyncMock(return_value=[
            {"id": "acc1", "tenant_id": "t1"},
        ])
        db.table("messages").add = AsyncMock(return_value="msg-12345")
        return db

    def test_send_with_eml_file(self, cli_group, mock_db, tmp_path):
        """send command queues email from .eml file."""
        # Create test .eml file
        eml_content = b"""From: sender@test.com
To: recipient@test.com
Subject: Test Subject

This is the body.
"""
        eml_file = tmp_path / "test.eml"
        eml_file.write_bytes(eml_content)

        add_send_command(cli_group, mock_db, "t1")
        runner = CliRunner()
        result = runner.invoke(cli_group, ["send", str(eml_file)])

        assert result.exit_code == 0
        assert "queued" in result.output.lower() or "msg-" in result.output

    def test_send_no_accounts(self, cli_group, mock_db, tmp_path):
        """send command fails with no accounts."""
        mock_db.table("accounts").list_all = AsyncMock(return_value=[])

        eml_content = b"""From: sender@test.com
To: recipient@test.com
Subject: Test

Body
"""
        eml_file = tmp_path / "test.eml"
        eml_file.write_bytes(eml_content)

        add_send_command(cli_group, mock_db, "t1")
        runner = CliRunner()
        result = runner.invoke(cli_group, ["send", str(eml_file)])

        assert result.exit_code == 1
        assert "No accounts found" in result.output

    def test_send_specific_account(self, cli_group, mock_db, tmp_path):
        """send command uses specified account."""
        eml_content = b"""From: sender@test.com
To: recipient@test.com
Subject: Test

Body
"""
        eml_file = tmp_path / "test.eml"
        eml_file.write_bytes(eml_content)

        add_send_command(cli_group, mock_db, "t1")
        runner = CliRunner()
        result = runner.invoke(cli_group, ["send", str(eml_file), "--account", "acc1"])

        assert result.exit_code == 0

    def test_send_invalid_account(self, cli_group, mock_db, tmp_path):
        """send command fails with invalid account."""
        eml_content = b"""From: sender@test.com
To: recipient@test.com
Subject: Test

Body
"""
        eml_file = tmp_path / "test.eml"
        eml_file.write_bytes(eml_content)

        add_send_command(cli_group, mock_db, "t1")
        runner = CliRunner()
        result = runner.invoke(cli_group, ["send", str(eml_file), "--account", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_send_multipart_email(self, cli_group, mock_db, tmp_path):
        """send command handles multipart .eml file."""
        # Create multipart email with both text and html
        eml_content = b"""From: sender@test.com
To: recipient@test.com
Subject: Multipart Test
MIME-Version: 1.0
Content-Type: multipart/alternative; boundary="boundary123"

--boundary123
Content-Type: text/plain; charset="utf-8"

This is plain text.
--boundary123
Content-Type: text/html; charset="utf-8"

<html><body>This is HTML.</body></html>
--boundary123--
"""
        eml_file = tmp_path / "multipart.eml"
        eml_file.write_bytes(eml_content)

        add_send_command(cli_group, mock_db, "t1")
        runner = CliRunner()
        result = runner.invoke(cli_group, ["send", str(eml_file)])

        assert result.exit_code == 0

    def test_send_html_only_email(self, cli_group, mock_db, tmp_path):
        """send command handles HTML-only .eml file."""
        eml_content = b"""From: sender@test.com
To: recipient@test.com
Subject: HTML Test
Content-Type: text/html; charset="utf-8"

<html><body>Hello HTML</body></html>
"""
        eml_file = tmp_path / "html.eml"
        eml_file.write_bytes(eml_content)

        add_send_command(cli_group, mock_db, "t1")
        runner = CliRunner()
        result = runner.invoke(cli_group, ["send", str(eml_file)])

        assert result.exit_code == 0


class TestAddTokenCommand:
    """Tests for add_token_command."""

    @pytest.fixture
    def cli_group(self):
        @click.group()
        def cli():
            pass
        return cli

    @pytest.fixture
    def mock_db(self):
        db = MockDb()
        db.table("instance").get_config = AsyncMock(return_value="existing-token")
        db.table("instance").set_config = AsyncMock()
        return db

    def test_token_shows_existing(self, cli_group, mock_db):
        """token command shows existing token."""
        add_token_command(cli_group, mock_db)
        runner = CliRunner()
        result = runner.invoke(cli_group, ["token"])

        assert result.exit_code == 0
        assert "existing-token" in result.output

    def test_token_no_token_configured(self, cli_group, mock_db):
        """token command shows message when no token."""
        mock_db.table("instance").get_config = AsyncMock(return_value=None)

        add_token_command(cli_group, mock_db)
        runner = CliRunner()
        result = runner.invoke(cli_group, ["token"])

        assert result.exit_code == 1
        assert "No API token" in result.output

    def test_token_regenerate(self, cli_group, mock_db):
        """token --regenerate creates new token."""
        add_token_command(cli_group, mock_db)
        runner = CliRunner()
        result = runner.invoke(cli_group, ["token", "--regenerate"])

        assert result.exit_code == 0
        assert "regenerated" in result.output.lower()
        mock_db.table("instance").set_config.assert_called_once()


class TestAddRunNowCommand:
    """Tests for add_run_now_command."""

    @pytest.fixture
    def cli_group(self):
        @click.group()
        def cli():
            pass
        return cli

    def test_run_now_no_url(self, cli_group):
        """run-now fails without URL."""
        add_run_now_command(
            cli_group,
            get_url=lambda: None,
            get_token=lambda: None,
        )
        runner = CliRunner()
        result = runner.invoke(cli_group, ["run-now"])

        assert result.exit_code == 1
        assert "not running" in result.output.lower() or "URL" in result.output

    def test_run_now_success(self, cli_group):
        """run-now succeeds with valid response."""
        with patch('httpx.Client') as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response

            add_run_now_command(
                cli_group,
                get_url=lambda: "http://localhost:8000",
                get_token=lambda: "token123",
            )
            runner = CliRunner()
            result = runner.invoke(cli_group, ["run-now"])

            assert result.exit_code == 0
            assert "triggered" in result.output.lower()

    def test_run_now_with_tenant(self, cli_group):
        """run-now with tenant_id shows tenant scope."""
        with patch('httpx.Client') as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": True}
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response

            add_run_now_command(
                cli_group,
                get_url=lambda: "http://localhost:8000",
                get_token=lambda: "token123",
                tenant_id="t1",
            )
            runner = CliRunner()
            result = runner.invoke(cli_group, ["run-now"])

            assert result.exit_code == 0
            assert "t1" in result.output


class TestAddConnectCommand:
    """Tests for add_connect_command."""

    @pytest.fixture
    def cli_group(self):
        @click.group()
        def cli():
            pass
        return cli

    def test_connect_no_url(self, cli_group):
        """connect fails without URL."""
        add_connect_command(
            cli_group,
            get_url=lambda: None,
            get_token=lambda: None,
            instance_name="test",
        )
        runner = CliRunner()
        result = runner.invoke(cli_group, ["connect"])

        assert result.exit_code == 1
        assert "URL" in result.output

    def test_connect_with_url_option(self, cli_group):
        """connect accepts --url option."""
        with patch('tools.http_client.connect', side_effect=Exception("Connection failed")):
            add_connect_command(
                cli_group,
                get_url=lambda: None,
                get_token=lambda: None,
                instance_name="test",
            )
            runner = CliRunner()
            result = runner.invoke(cli_group, ["connect", "--url", "http://test:8000"])

            # Should fail but try to connect to the specified URL
            assert result.exit_code == 1
            assert "failed" in result.output.lower() or "error" in result.output.lower()


class TestRunNowHttpErrors:
    """Tests for run-now HTTP error handling."""

    @pytest.fixture
    def cli_group(self):
        @click.group()
        def cli():
            pass
        return cli

    def test_run_now_http_error(self, cli_group):
        """run-now handles HTTP errors gracefully."""
        import httpx
        with patch('httpx.Client') as mock_client:
            mock_client.return_value.__enter__.return_value.post.side_effect = httpx.HTTPError("Server error")

            add_run_now_command(
                cli_group,
                get_url=lambda: "http://localhost:8000",
                get_token=lambda: "token123",
            )
            runner = CliRunner()
            result = runner.invoke(cli_group, ["run-now"])

            assert result.exit_code == 1
            assert "failed" in result.output.lower() or "error" in result.output.lower()

    def test_run_now_server_returns_not_ok(self, cli_group):
        """run-now shows error when server returns not ok."""
        with patch('httpx.Client') as mock_client:
            mock_response = MagicMock()
            mock_response.json.return_value = {"ok": False, "error": "Something went wrong"}
            mock_response.raise_for_status = MagicMock()
            mock_client.return_value.__enter__.return_value.post.return_value = mock_response

            add_run_now_command(
                cli_group,
                get_url=lambda: "http://localhost:8000",
                get_token=lambda: None,
            )
            runner = CliRunner()
            result = runner.invoke(cli_group, ["run-now"])

            assert result.exit_code == 0
            assert "error" in result.output.lower()
