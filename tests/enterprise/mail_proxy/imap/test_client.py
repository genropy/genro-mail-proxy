# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for IMAPClient with mocked aioimaplib."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from enterprise.mail_proxy.imap.client import IMAPClient, IMAPMessage


class TestIMAPMessage:
    """Tests for IMAPMessage dataclass."""

    def test_imap_message_fields(self):
        """IMAPMessage has uid and raw fields."""
        msg = IMAPMessage(uid=123, raw=b"raw email content")

        assert msg.uid == 123
        assert msg.raw == b"raw email content"


class TestIMAPClientInit:
    """Tests for IMAPClient initialization."""

    def test_init_without_logger(self):
        """Init without logger."""
        client = IMAPClient()

        assert client._client is None
        assert client._logger is None
        assert client._uidvalidity is None

    def test_init_with_logger(self):
        """Init with logger."""
        logger = MagicMock()
        client = IMAPClient(logger=logger)

        assert client._logger is logger


class TestIMAPClientConnect:
    """Tests for connect method."""

    @pytest.fixture
    def client(self):
        return IMAPClient(logger=MagicMock())

    async def test_connect_ssl(self, client):
        """Connect with SSL."""
        mock_imap = MagicMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))

        with patch.dict("sys.modules", {"aioimaplib": MagicMock(IMAP4_SSL=MagicMock(return_value=mock_imap))}):
            import sys
            mock_aioimaplib = sys.modules["aioimaplib"]

            await client.connect(
                host="imap.test.com",
                port=993,
                user="user@test.com",
                password="secret",
                use_ssl=True,
            )

            mock_aioimaplib.IMAP4_SSL.assert_called_once()
            mock_imap.wait_hello_from_server.assert_called_once()
            mock_imap.login.assert_called_once_with("user@test.com", "secret")

    async def test_connect_no_ssl(self, client):
        """Connect without SSL."""
        mock_imap = MagicMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock(return_value=MagicMock(result="OK", lines=[]))

        with patch.dict("sys.modules", {"aioimaplib": MagicMock(IMAP4=MagicMock(return_value=mock_imap))}):
            await client.connect(
                host="imap.test.com",
                port=143,
                user="user@test.com",
                password="secret",
                use_ssl=False,
            )

    async def test_connect_login_failure(self, client):
        """Login failure raises ConnectionError."""
        mock_imap = MagicMock()
        mock_imap.wait_hello_from_server = AsyncMock()
        mock_imap.login = AsyncMock(
            return_value=MagicMock(result="NO", lines=["Invalid credentials"])
        )

        with patch.dict("sys.modules", {"aioimaplib": MagicMock(IMAP4_SSL=MagicMock(return_value=mock_imap))}):
            with pytest.raises(ConnectionError, match="login failed"):
                await client.connect(
                    host="imap.test.com",
                    port=993,
                    user="user@test.com",
                    password="wrong",
                    use_ssl=True,
                )


class TestIMAPClientSelectFolder:
    """Tests for select_folder method."""

    @pytest.fixture
    def client(self):
        client = IMAPClient(logger=MagicMock())
        client._client = MagicMock()
        return client

    async def test_select_inbox(self, client):
        """Select INBOX folder."""
        client._client.select = AsyncMock(
            return_value=MagicMock(
                result="OK",
                lines=["[UIDVALIDITY 123456] UIDs valid"],
            )
        )

        uidvalidity = await client.select_folder("INBOX")

        client._client.select.assert_called_once_with("INBOX")
        assert uidvalidity == 123456
        assert client.uidvalidity == 123456

    async def test_select_not_connected(self):
        """Select without connection raises RuntimeError."""
        client = IMAPClient()

        with pytest.raises(RuntimeError, match="Not connected"):
            await client.select_folder("INBOX")

    async def test_select_folder_failure(self, client):
        """Select failure raises RuntimeError."""
        client._client.select = AsyncMock(
            return_value=MagicMock(
                result="NO",
                lines=["Folder not found"],
            )
        )

        with pytest.raises(RuntimeError, match="Failed to select"):
            await client.select_folder("NonExistent")


class TestIMAPClientFetchSinceUID:
    """Tests for fetch_since_uid method."""

    @pytest.fixture
    def client(self):
        client = IMAPClient(logger=MagicMock())
        client._client = MagicMock()
        return client

    async def test_fetch_no_new_messages(self, client):
        """Fetch returns empty list when no new messages."""
        client._client.uid_search = AsyncMock(
            return_value=MagicMock(result="OK", lines=[""])
        )

        messages = await client.fetch_since_uid(100)

        assert messages == []

    async def test_fetch_new_messages(self, client):
        """Fetch returns messages with UID > last_uid."""
        # Mock search response
        client._client.uid_search = AsyncMock(
            return_value=MagicMock(result="OK", lines=["101 102 103"])
        )

        # Mock fetch response for each UID
        client._client.uid = AsyncMock(
            return_value=MagicMock(
                result="OK",
                lines=[
                    b"1 FETCH (UID 101 RFC822 {100}",
                    bytearray(b"From: test@test.com\r\nSubject: Test\r\n\r\nBody"),
                    b")",
                ],
            )
        )

        messages = await client.fetch_since_uid(100)

        assert len(messages) == 3
        assert all(isinstance(m, IMAPMessage) for m in messages)
        assert messages[0].uid == 101

    async def test_fetch_not_connected(self):
        """Fetch without connection raises RuntimeError."""
        client = IMAPClient()

        with pytest.raises(RuntimeError, match="Not connected"):
            await client.fetch_since_uid(0)

    async def test_fetch_search_failure(self, client):
        """Search failure returns empty list."""
        client._client.uid_search = AsyncMock(
            return_value=MagicMock(result="NO", lines=["Search failed"])
        )

        messages = await client.fetch_since_uid(0)

        assert messages == []


class TestIMAPClientClose:
    """Tests for close method."""

    async def test_close_connected(self):
        """Close disconnects from server."""
        client = IMAPClient(logger=MagicMock())
        mock_imap_client = MagicMock()
        mock_imap_client.logout = AsyncMock()
        client._client = mock_imap_client

        await client.close()

        mock_imap_client.logout.assert_called_once()
        assert client._client is None

    async def test_close_not_connected(self):
        """Close when not connected does nothing."""
        client = IMAPClient()

        await client.close()  # Should not raise

        assert client._client is None

    async def test_close_logout_error(self):
        """Close handles logout errors gracefully."""
        client = IMAPClient(logger=MagicMock())
        client._client = MagicMock()
        client._client.logout = AsyncMock(side_effect=Exception("Connection lost"))

        await client.close()  # Should not raise

        assert client._client is None


class TestIMAPClientProperties:
    """Tests for client properties."""

    def test_uidvalidity_property(self):
        """uidvalidity property returns stored value."""
        client = IMAPClient()
        client._uidvalidity = 12345

        assert client.uidvalidity == 12345

    def test_uidvalidity_none(self):
        """uidvalidity is None before selecting folder."""
        client = IMAPClient()

        assert client.uidvalidity is None
