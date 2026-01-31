# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Unit tests for PecReceiver with mocked IMAP client."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from enterprise.mail_proxy.pec.receiver import PEC_ACCEPTANCE_TIMEOUT, PecReceiver


def _create_mock_imap_module(mock_client):
    """Create a mock imap module with IMAPClient."""
    mock_imap_module = MagicMock()
    mock_imap_module.IMAPClient = MagicMock(return_value=mock_client)
    return mock_imap_module


class TestPecReceiverInit:
    """Tests for PecReceiver initialization."""

    def test_init_defaults(self):
        """PecReceiver initializes with defaults."""
        db = MagicMock()

        receiver = PecReceiver(db)

        assert receiver._db is db
        assert receiver._logger is None
        assert receiver._poll_interval == 60
        assert receiver._acceptance_timeout == PEC_ACCEPTANCE_TIMEOUT
        assert receiver._running is False
        assert receiver._task is None

    def test_init_with_params(self):
        """PecReceiver initializes with custom params."""
        db = MagicMock()
        logger = MagicMock()

        receiver = PecReceiver(
            db,
            logger=logger,
            poll_interval=30,
            acceptance_timeout=1800,
        )

        assert receiver._logger is logger
        assert receiver._poll_interval == 30
        assert receiver._acceptance_timeout == 1800

    def test_pec_acceptance_timeout_constant(self):
        """PEC_ACCEPTANCE_TIMEOUT is 30 minutes."""
        assert PEC_ACCEPTANCE_TIMEOUT == 30 * 60


class TestPecReceiverLifecycle:
    """Tests for start/stop lifecycle."""

    @pytest.fixture
    def receiver(self):
        """Create receiver with mocks."""
        db = MagicMock()
        logger = MagicMock()
        return PecReceiver(db, logger=logger, poll_interval=1)

    async def test_start(self, receiver):
        """Start creates background task."""
        with patch.object(receiver, "_poll_loop", new_callable=AsyncMock):
            await receiver.start()

            assert receiver._running is True
            assert receiver._task is not None
            receiver._logger.info.assert_called_with("PecReceiver started")

            await receiver.stop()

    async def test_start_idempotent(self, receiver):
        """Start is idempotent."""
        with patch.object(receiver, "_poll_loop", new_callable=AsyncMock):
            await receiver.start()
            task1 = receiver._task

            await receiver.start()
            task2 = receiver._task

            assert task1 is task2

            await receiver.stop()

    async def test_stop(self, receiver):
        """Stop cancels task."""
        with patch.object(receiver, "_poll_loop", new_callable=AsyncMock):
            await receiver.start()
            await receiver.stop()

            assert receiver._running is False
            assert receiver._task is None
            receiver._logger.info.assert_called_with("PecReceiver stopped")

    async def test_stop_when_not_started(self, receiver):
        """Stop when not started does nothing."""
        await receiver.stop()

        assert receiver._running is False
        assert receiver._task is None


class TestPecReceiverPollLoop:
    """Tests for polling loop."""

    @pytest.fixture
    def receiver(self):
        """Create receiver with short poll interval."""
        db = MagicMock()
        logger = MagicMock()
        return PecReceiver(db, logger=logger, poll_interval=0)

    async def test_poll_loop_processes_accounts(self, receiver):
        """Poll loop processes PEC accounts and checks timeouts."""
        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                receiver._running = False

        receiver._running = True

        with patch.object(receiver, "_process_all_pec_accounts", side_effect=mock_process):
            with patch.object(receiver, "_check_pec_timeouts", new_callable=AsyncMock):
                await receiver._poll_loop()

        assert call_count == 2

    async def test_poll_loop_handles_errors(self, receiver):
        """Poll loop continues on errors."""
        call_count = 0

        async def mock_process():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Processing failed")
            if call_count >= 2:
                receiver._running = False

        receiver._running = True

        with patch.object(receiver, "_process_all_pec_accounts", side_effect=mock_process):
            with patch.object(receiver, "_check_pec_timeouts", new_callable=AsyncMock):
                await receiver._poll_loop()

        assert call_count == 2
        receiver._logger.error.assert_called_once()


class TestPecReceiverProcessAllAccounts:
    """Tests for _process_all_pec_accounts."""

    @pytest.fixture
    def receiver(self):
        """Create receiver."""
        db = MagicMock()
        logger = MagicMock()
        return PecReceiver(db, logger=logger)

    async def test_no_pec_accounts(self, receiver):
        """No PEC accounts returns early."""
        mock_accounts_table = MagicMock()
        mock_accounts_table.list_pec_accounts = AsyncMock(return_value=[])
        receiver._db.table.return_value = mock_accounts_table

        with patch.object(receiver, "_process_account", new_callable=AsyncMock) as mock_process:
            await receiver._process_all_pec_accounts()

        mock_process.assert_not_called()

    async def test_processes_each_account(self, receiver):
        """Each PEC account is processed."""
        accounts = [
            {"id": "acc1", "tenant_id": "t1"},
            {"id": "acc2", "tenant_id": "t2"},
        ]

        mock_accounts_table = MagicMock()
        mock_accounts_table.list_pec_accounts = AsyncMock(return_value=accounts)
        receiver._db.table.return_value = mock_accounts_table

        with patch.object(receiver, "_process_account", new_callable=AsyncMock) as mock_process:
            await receiver._process_all_pec_accounts()

        assert mock_process.call_count == 2
        mock_process.assert_any_call(accounts[0])
        mock_process.assert_any_call(accounts[1])

    async def test_continues_on_account_error(self, receiver):
        """Error in one account doesn't stop others."""
        accounts = [
            {"id": "acc1", "tenant_id": "t1"},
            {"id": "acc2", "tenant_id": "t2"},
        ]

        mock_accounts_table = MagicMock()
        mock_accounts_table.list_pec_accounts = AsyncMock(return_value=accounts)
        receiver._db.table.return_value = mock_accounts_table

        call_count = 0

        async def mock_process(account):
            nonlocal call_count
            call_count += 1
            if account["id"] == "acc1":
                raise Exception("Account error")

        with patch.object(receiver, "_process_account", side_effect=mock_process):
            await receiver._process_all_pec_accounts()

        assert call_count == 2
        receiver._logger.error.assert_called_once()


class TestPecReceiverProcessAccount:
    """Tests for _process_account."""

    @pytest.fixture
    def receiver(self):
        """Create receiver."""
        db = MagicMock()
        logger = MagicMock()
        return PecReceiver(db, logger=logger)

    async def test_skips_account_without_imap_config(self, receiver):
        """Account without IMAP config is skipped."""
        account = {
            "id": "acc1",
            "tenant_id": "t1",
            # Missing imap_host, imap_user, imap_password
        }

        await receiver._process_account(account)

        receiver._logger.warning.assert_called_once()
        assert "missing IMAP configuration" in str(receiver._logger.warning.call_args)

    async def test_processes_pec_receipts(self, receiver):
        """PEC receipts are processed and events created."""
        account = {
            "id": "pec-acc",
            "tenant_id": "t1",
            "imap_host": "imap.pec.it",
            "imap_port": 993,
            "imap_user": "user@pec.it",
            "imap_password": "secret",
            "imap_folder": "INBOX",
            "imap_last_uid": 0,
            "imap_uidvalidity": None,
        }

        mock_message = MagicMock()
        mock_message.uid = 100
        mock_message.raw = b"pec receipt"

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.select_folder = AsyncMock(return_value=12345)
        mock_client.fetch_since_uid = AsyncMock(return_value=[mock_message])
        mock_client.close = AsyncMock()

        mock_receipt_info = MagicMock()
        mock_receipt_info.receipt_type = "accettazione"
        mock_receipt_info.original_message_id = "msg-001"
        mock_receipt_info.timestamp = "2025-01-20T10:00:00"
        mock_receipt_info.recipient = "dest@pec.it"
        mock_receipt_info.error_reason = None

        mock_accounts_table = MagicMock()
        mock_accounts_table.update_imap_sync_state = AsyncMock()
        receiver._db.table.return_value = mock_accounts_table

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            with patch.object(receiver._parser, "parse", return_value=mock_receipt_info):
                with patch.object(receiver, "_handle_receipt", new_callable=AsyncMock) as mock_handle:
                    await receiver._process_account(account)

        mock_handle.assert_called_once_with(mock_receipt_info)
        mock_accounts_table.update_imap_sync_state.assert_called_once_with(
            "t1", "pec-acc", last_uid=100, uidvalidity=12345
        )

    async def test_uidvalidity_change_resets_last_uid(self, receiver):
        """UIDVALIDITY change resets last_uid."""
        account = {
            "id": "pec-acc",
            "tenant_id": "t1",
            "imap_host": "imap.pec.it",
            "imap_port": 993,
            "imap_user": "user@pec.it",
            "imap_password": "secret",
            "imap_folder": "INBOX",
            "imap_last_uid": 500,
            "imap_uidvalidity": 12345,  # Old value
        }

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.select_folder = AsyncMock(return_value=99999)  # New UIDVALIDITY
        mock_client.fetch_since_uid = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        mock_accounts_table = MagicMock()
        mock_accounts_table.update_imap_sync_state = AsyncMock()
        receiver._db.table.return_value = mock_accounts_table

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            await receiver._process_account(account)

        receiver._logger.warning.assert_called()
        # fetch_since_uid should be called with 0 (reset)
        mock_client.fetch_since_uid.assert_called_once_with(0)

    async def test_no_new_messages_updates_uidvalidity_only(self, receiver):
        """No new messages still updates UIDVALIDITY if changed."""
        account = {
            "id": "pec-acc",
            "tenant_id": "t1",
            "imap_host": "imap.pec.it",
            "imap_port": 993,
            "imap_user": "user@pec.it",
            "imap_password": "secret",
            "imap_folder": "INBOX",
            "imap_last_uid": 50,
            "imap_uidvalidity": None,  # First sync
        }

        mock_client = MagicMock()
        mock_client.connect = AsyncMock()
        mock_client.select_folder = AsyncMock(return_value=12345)
        mock_client.fetch_since_uid = AsyncMock(return_value=[])
        mock_client.close = AsyncMock()

        mock_accounts_table = MagicMock()
        mock_accounts_table.update_imap_sync_state = AsyncMock()
        receiver._db.table.return_value = mock_accounts_table

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            await receiver._process_account(account)

        # Should update sync state even with no messages (for uidvalidity)
        mock_accounts_table.update_imap_sync_state.assert_called_once()


class TestPecReceiverHandleReceipt:
    """Tests for _handle_receipt."""

    @pytest.fixture
    def receiver(self):
        """Create receiver."""
        db = MagicMock()
        logger = MagicMock()
        return PecReceiver(db, logger=logger)

    async def test_handle_accettazione(self, receiver):
        """Accettazione creates pec_acceptance event."""
        receipt_info = MagicMock()
        receipt_info.original_message_id = "msg-001"
        receipt_info.receipt_type = "accettazione"
        receipt_info.timestamp = "2025-01-20T10:00:00"
        receipt_info.recipient = None
        receipt_info.error_reason = None

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()
        receiver._db.table.return_value = mock_events_table

        await receiver._handle_receipt(receipt_info)

        mock_events_table.add_event.assert_called_once()
        call_args = mock_events_table.add_event.call_args
        assert call_args.kwargs["message_pk"] == "msg-001"
        assert call_args.kwargs["event_type"] == "pec_acceptance"
        assert call_args.kwargs["description"] == "PEC accettazione"

    async def test_handle_consegna(self, receiver):
        """Consegna creates pec_delivery event."""
        receipt_info = MagicMock()
        receipt_info.original_message_id = "msg-002"
        receipt_info.receipt_type = "consegna"
        receipt_info.timestamp = "2025-01-20T11:00:00"
        receipt_info.recipient = "dest@pec.it"
        receipt_info.error_reason = None

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()
        receiver._db.table.return_value = mock_events_table

        await receiver._handle_receipt(receipt_info)

        call_args = mock_events_table.add_event.call_args
        assert call_args.kwargs["event_type"] == "pec_delivery"
        assert call_args.kwargs["metadata"]["recipient"] == "dest@pec.it"

    async def test_handle_mancata_consegna(self, receiver):
        """Mancata consegna creates pec_error event and clears PEC flag."""
        receipt_info = MagicMock()
        receipt_info.original_message_id = "msg-003"
        receipt_info.receipt_type = "mancata_consegna"
        receipt_info.timestamp = "2025-01-20T12:00:00"
        receipt_info.recipient = "invalid@pec.it"
        receipt_info.error_reason = "Casella inesistente"

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()

        mock_messages_table = MagicMock()
        mock_messages_table.clear_pec_flag = AsyncMock()

        def table_side_effect(name):
            if name == "message_events":
                return mock_events_table
            elif name == "messages":
                return mock_messages_table
            return MagicMock()

        receiver._db.table = MagicMock(side_effect=table_side_effect)

        await receiver._handle_receipt(receipt_info)

        # Event created
        call_args = mock_events_table.add_event.call_args
        assert call_args.kwargs["event_type"] == "pec_error"
        assert call_args.kwargs["metadata"]["error_reason"] == "Casella inesistente"

        # PEC flag cleared
        mock_messages_table.clear_pec_flag.assert_called_once_with("msg-003")

    async def test_handle_non_accettazione(self, receiver):
        """Non accettazione creates pec_error event and clears PEC flag."""
        receipt_info = MagicMock()
        receipt_info.original_message_id = "msg-004"
        receipt_info.receipt_type = "non_accettazione"
        receipt_info.timestamp = "2025-01-20T13:00:00"
        receipt_info.recipient = None
        receipt_info.error_reason = "Messaggio rifiutato"

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()

        mock_messages_table = MagicMock()
        mock_messages_table.clear_pec_flag = AsyncMock()

        def table_side_effect(name):
            if name == "message_events":
                return mock_events_table
            elif name == "messages":
                return mock_messages_table
            return MagicMock()

        receiver._db.table = MagicMock(side_effect=table_side_effect)

        await receiver._handle_receipt(receipt_info)

        call_args = mock_events_table.add_event.call_args
        assert call_args.kwargs["event_type"] == "pec_error"
        mock_messages_table.clear_pec_flag.assert_called_once_with("msg-004")

    async def test_handle_presa_in_carico(self, receiver):
        """Presa in carico creates pec_acceptance event."""
        receipt_info = MagicMock()
        receipt_info.original_message_id = "msg-005"
        receipt_info.receipt_type = "presa_in_carico"
        receipt_info.timestamp = None
        receipt_info.recipient = None
        receipt_info.error_reason = None

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()
        receiver._db.table.return_value = mock_events_table

        await receiver._handle_receipt(receipt_info)

        call_args = mock_events_table.add_event.call_args
        assert call_args.kwargs["event_type"] == "pec_acceptance"


class TestPecReceiverCheckTimeouts:
    """Tests for _check_pec_timeouts."""

    @pytest.fixture
    def receiver(self):
        """Create receiver."""
        db = MagicMock()
        logger = MagicMock()
        return PecReceiver(db, logger=logger, acceptance_timeout=1800)

    async def test_no_timed_out_messages(self, receiver):
        """No timed out messages does nothing."""
        mock_messages_table = MagicMock()
        mock_messages_table.get_pec_without_acceptance = AsyncMock(return_value=[])
        receiver._db.table.return_value = mock_messages_table

        await receiver._check_pec_timeouts()

        # No events should be created
        receiver._logger.info.assert_not_called()

    async def test_timeout_clears_pec_flag_and_creates_event(self, receiver):
        """Timed out message clears PEC flag and creates timeout event."""
        timed_out = [{"pk": "msg-timeout-001"}]

        mock_messages_table = MagicMock()
        mock_messages_table.get_pec_without_acceptance = AsyncMock(return_value=timed_out)
        mock_messages_table.clear_pec_flag = AsyncMock()

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()

        def table_side_effect(name):
            if name == "messages":
                return mock_messages_table
            elif name == "message_events":
                return mock_events_table
            return MagicMock()

        receiver._db.table = MagicMock(side_effect=table_side_effect)

        await receiver._check_pec_timeouts()

        # PEC flag cleared
        mock_messages_table.clear_pec_flag.assert_called_once_with("msg-timeout-001")

        # Timeout event created
        mock_events_table.add_event.assert_called_once()
        call_args = mock_events_table.add_event.call_args
        assert call_args.kwargs["message_pk"] == "msg-timeout-001"
        assert call_args.kwargs["event_type"] == "pec_timeout"

        # Log message
        receiver._logger.info.assert_called()

    async def test_multiple_timeouts(self, receiver):
        """Multiple timed out messages are all processed."""
        timed_out = [
            {"pk": "msg-001"},
            {"pk": "msg-002"},
            {"pk": "msg-003"},
        ]

        mock_messages_table = MagicMock()
        mock_messages_table.get_pec_without_acceptance = AsyncMock(return_value=timed_out)
        mock_messages_table.clear_pec_flag = AsyncMock()

        mock_events_table = MagicMock()
        mock_events_table.add_event = AsyncMock()

        def table_side_effect(name):
            if name == "messages":
                return mock_messages_table
            elif name == "message_events":
                return mock_events_table
            return MagicMock()

        receiver._db.table = MagicMock(side_effect=table_side_effect)

        await receiver._check_pec_timeouts()

        assert mock_messages_table.clear_pec_flag.call_count == 3
        assert mock_events_table.add_event.call_count == 3


class TestPecReceiverConnectionErrors:
    """Tests for IMAP connection error handling."""

    @pytest.fixture
    def receiver(self):
        """Create receiver."""
        db = MagicMock()
        logger = MagicMock()
        return PecReceiver(db, logger=logger)

    async def test_connection_error_closes_client(self, receiver):
        """Connection error still closes client."""
        account = {
            "id": "pec-acc",
            "tenant_id": "t1",
            "imap_host": "imap.pec.it",
            "imap_port": 993,
            "imap_user": "user@pec.it",
            "imap_password": "secret",
            "imap_folder": "INBOX",
            "imap_last_uid": 0,
            "imap_uidvalidity": None,
        }

        mock_client = MagicMock()
        mock_client.connect = AsyncMock(side_effect=ConnectionError("Connection refused"))
        mock_client.close = AsyncMock()

        mock_imap = _create_mock_imap_module(mock_client)

        with patch.dict("sys.modules", {"enterprise.mail_proxy.imap": mock_imap}):
            with pytest.raises(ConnectionError):
                await receiver._process_account(account)

        mock_client.close.assert_called_once()
