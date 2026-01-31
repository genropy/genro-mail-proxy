# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for BounceReceiver with mocked IMAP client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from enterprise.mail_proxy.bounce.receiver import BounceConfig, BounceReceiver


class TestBounceConfig:
    """Tests for BounceConfig dataclass."""

    def test_config_defaults(self):
        """BounceConfig has sensible defaults."""
        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="bounces@test.com",
            password="secret",
        )

        assert config.host == "imap.test.com"
        assert config.port == 993
        assert config.user == "bounces@test.com"
        assert config.password == "secret"
        assert config.use_ssl is True
        assert config.folder == "INBOX"
        assert config.poll_interval == 60

    def test_config_custom_values(self):
        """BounceConfig accepts custom values."""
        config = BounceConfig(
            host="mail.example.com",
            port=143,
            user="user",
            password="pass",
            use_ssl=False,
            folder="Bounces",
            poll_interval=30,
        )

        assert config.use_ssl is False
        assert config.folder == "Bounces"
        assert config.poll_interval == 30


class TestBounceReceiverInit:
    """Tests for BounceReceiver initialization."""

    def test_init(self):
        """BounceReceiver initializes correctly."""
        proxy = MagicMock()
        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="user",
            password="pass",
        )

        receiver = BounceReceiver(proxy, config)

        assert receiver.proxy is proxy
        assert receiver.config is config
        assert receiver._running is False
        assert receiver._task is None
        assert receiver._last_uid == 0
        assert receiver._uidvalidity is None

    def test_properties(self):
        """BounceReceiver properties delegate to proxy."""
        proxy = MagicMock()
        proxy.db = MagicMock()
        proxy.logger = MagicMock()

        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="user",
            password="pass",
        )
        receiver = BounceReceiver(proxy, config)

        assert receiver.db is proxy.db
        assert receiver.logger is proxy.logger


class TestBounceReceiverLifecycle:
    """Tests for start/stop lifecycle."""

    @pytest.fixture
    def receiver(self):
        """Create receiver with mocked proxy."""
        proxy = MagicMock()
        proxy.logger = MagicMock()
        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="user",
            password="pass",
            poll_interval=1,
        )
        return BounceReceiver(proxy, config)

    async def test_start(self, receiver):
        """Start creates background task."""
        with patch.object(receiver, "_poll_loop", new_callable=AsyncMock):
            await receiver.start()

            assert receiver._running is True
            assert receiver._task is not None
            receiver.logger.info.assert_called()

            await receiver.stop()

    async def test_start_idempotent(self, receiver):
        """Start is idempotent."""
        with patch.object(receiver, "_poll_loop", new_callable=AsyncMock):
            await receiver.start()
            task1 = receiver._task

            await receiver.start()
            task2 = receiver._task

            # Should be same task
            assert task1 is task2

            await receiver.stop()

    async def test_stop(self, receiver):
        """Stop cancels task and resets state."""
        with patch.object(receiver, "_poll_loop", new_callable=AsyncMock):
            await receiver.start()
            await receiver.stop()

            assert receiver._running is False
            assert receiver._task is None
            receiver.logger.info.assert_called()

    async def test_stop_when_not_started(self, receiver):
        """Stop when not started does nothing."""
        await receiver.stop()

        assert receiver._running is False
        assert receiver._task is None


def _create_mock_imap_module(mock_client):
    """Create a mock imap module with IMAPClient."""
    mock_imap_module = MagicMock()
    mock_imap_module.IMAPClient = MagicMock(return_value=mock_client)
    return mock_imap_module


class TestBounceReceiverProcessBounces:
    """Tests for _process_bounces method."""

    @pytest.fixture
    def receiver(self):
        """Create receiver with mocked proxy and db."""
        proxy = MagicMock()
        proxy.logger = MagicMock()
        proxy.db = MagicMock()
        proxy.db.table.return_value = MagicMock()

        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="user",
            password="pass",
        )
        return BounceReceiver(proxy, config)

    async def test_process_bounces_no_messages(self, receiver):
        """Process bounces when no messages in mailbox."""
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.select_folder = AsyncMock(return_value=12345)
        mock_client.fetch_since_uid = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            await receiver._process_bounces()

        mock_client.connect.assert_called_once()
        mock_client.select_folder.assert_called_once_with("INBOX")
        mock_client.fetch_since_uid.assert_called_once_with(0)
        mock_client.close.assert_called_once()

    async def test_process_bounces_with_bounce_message(self, receiver):
        """Process bounces detects and records bounce."""
        # Create mock message
        mock_message = MagicMock()
        mock_message.uid = 100
        mock_message.raw = b"bounce message content"

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.select_folder = AsyncMock(return_value=12345)
        mock_client.fetch_since_uid = AsyncMock(return_value=[mock_message])
        mock_client.close = AsyncMock()

        # Mock parser to return bounce info
        mock_bounce_info = MagicMock()
        mock_bounce_info.original_message_id = "msg-12345"
        mock_bounce_info.bounce_reason = "User unknown"
        mock_bounce_info.bounce_type = "hard"
        mock_bounce_info.bounce_code = "550"

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()
        receiver.proxy.db.table.return_value = mock_events_table

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            with patch.object(receiver._parser, "parse", return_value=mock_bounce_info):
                await receiver._process_bounces()

        # Verify event was recorded
        mock_events_table.add_event.assert_called_once()
        call_args = mock_events_table.add_event.call_args
        assert call_args[0][0] == "msg-12345"  # message_pk
        assert call_args[0][1] == "bounce"  # event_type

        # Verify last_uid was updated
        assert receiver._last_uid == 100

    async def test_process_bounces_ignores_non_bounce(self, receiver):
        """Process bounces ignores messages that aren't bounces."""
        mock_message = MagicMock()
        mock_message.uid = 100
        mock_message.raw = b"regular message"

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.select_folder = AsyncMock(return_value=12345)
        mock_client.fetch_since_uid = AsyncMock(return_value=[mock_message])
        mock_client.close = AsyncMock()

        # Mock parser to return no bounce info
        mock_bounce_info = MagicMock()
        mock_bounce_info.original_message_id = None

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()
        receiver.proxy.db.table.return_value = mock_events_table

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            with patch.object(receiver._parser, "parse", return_value=mock_bounce_info):
                await receiver._process_bounces()

        # No event should be recorded
        mock_events_table.add_event.assert_not_called()

        # But last_uid should still be updated
        assert receiver._last_uid == 100

    async def test_process_bounces_uidvalidity_change(self, receiver):
        """Process bounces resets on UIDVALIDITY change."""
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.select_folder = AsyncMock(return_value=99999)  # New UIDVALIDITY
        mock_client.fetch_since_uid = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        # Set previous UIDVALIDITY and last_uid
        receiver._uidvalidity = 12345
        receiver._last_uid = 500

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            await receiver._process_bounces()

        # Should have logged warning and reset last_uid
        receiver.logger.warning.assert_called()
        assert receiver._last_uid == 0
        assert receiver._uidvalidity == 99999

    async def test_process_bounces_multiple_messages(self, receiver):
        """Process bounces handles multiple messages."""
        mock_messages = [
            MagicMock(uid=101, raw=b"bounce 1"),
            MagicMock(uid=102, raw=b"regular msg"),
            MagicMock(uid=103, raw=b"bounce 2"),
        ]

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.select_folder = AsyncMock(return_value=12345)
        mock_client.fetch_since_uid = AsyncMock(return_value=mock_messages)
        mock_client.close = AsyncMock()

        # Return bounce info for first and third messages only
        def parse_side_effect(raw):
            info = MagicMock()
            if raw == b"bounce 1":
                info.original_message_id = "msg-001"
                info.bounce_reason = "User unknown"
                info.bounce_type = "hard"
                info.bounce_code = "550"
            elif raw == b"bounce 2":
                info.original_message_id = "msg-002"
                info.bounce_reason = "Mailbox full"
                info.bounce_type = "soft"
                info.bounce_code = "452"
            else:
                info.original_message_id = None
            return info

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()
        receiver.proxy.db.table.return_value = mock_events_table

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            with patch.object(receiver._parser, "parse", side_effect=parse_side_effect):
                await receiver._process_bounces()

        # Should have recorded 2 events
        assert mock_events_table.add_event.call_count == 2

        # last_uid should be highest
        assert receiver._last_uid == 103


class TestBounceReceiverPollLoop:
    """Tests for polling loop."""

    @pytest.fixture
    def receiver(self):
        """Create receiver with short poll interval."""
        proxy = MagicMock()
        proxy.logger = MagicMock()
        proxy.db = MagicMock()

        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="user",
            password="pass",
            poll_interval=0,  # No wait for tests
        )
        return BounceReceiver(proxy, config)

    async def test_poll_loop_processes_bounces(self, receiver):
        """Poll loop calls _process_bounces."""
        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                receiver._running = False

        receiver._running = True

        with patch.object(receiver, "_process_bounces", side_effect=mock_process):
            await receiver._poll_loop()

        assert call_count == 2

    async def test_poll_loop_handles_errors(self, receiver):
        """Poll loop continues on errors."""
        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Connection failed")
            if call_count >= 2:
                receiver._running = False

        receiver._running = True

        with patch.object(receiver, "_process_bounces", side_effect=mock_process):
            await receiver._poll_loop()

        # Should have continued after error
        assert call_count == 2
        receiver.logger.error.assert_called_once()


class TestBounceReceiverConnectionErrors:
    """Tests for connection error handling."""

    @pytest.fixture
    def receiver(self):
        """Create receiver."""
        proxy = MagicMock()
        proxy.logger = MagicMock()
        proxy.db = MagicMock()

        config = BounceConfig(
            host="imap.test.com",
            port=993,
            user="user",
            password="pass",
        )
        return BounceReceiver(proxy, config)

    async def test_connection_error(self, receiver):
        """Connection error is handled gracefully."""
        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=ConnectionError("Connection refused"))
        mock_client.close = AsyncMock()

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            # Should not raise, but we expect the exception to bubble up
            with pytest.raises(ConnectionError):
                await receiver._process_bounces()

        # close should still be called in finally
        mock_client.close.assert_called_once()

    async def test_select_folder_error(self, receiver):
        """Select folder error is handled."""
        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.select_folder = AsyncMock(side_effect=RuntimeError("Failed to select"))
        mock_client.close = AsyncMock()

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            with pytest.raises(RuntimeError):
                await receiver._process_bounces()

        mock_client.close.assert_called_once()
