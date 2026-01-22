# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Core orchestration logic for the asynchronous mail dispatcher.

This module provides the AsyncMailCore class, the central coordinator for
the email dispatch service. It orchestrates all major subsystems including:

- Message queue management and priority-based scheduling
- SMTP delivery with connection pooling
- Per-account rate limiting
- Automatic retry with exponential backoff
- Delivery report generation and client notification
- Attachment fetching from storage backends

The core runs background loops for continuous message processing and
periodic maintenance tasks. It exposes a command-based API for external
control and integrates with Prometheus for metrics collection.

Example:
    Running the mail dispatcher::

        from async_mail_service.core import AsyncMailCore

        core = AsyncMailCore(
            db_path="/data/mail.db",
            start_active=True,
            client_sync_url="https://api.example.com/delivery-report"
        )

        await core.start()
        # Service is now processing messages

        # To stop gracefully
        await core.stop()

Attributes:
    PRIORITY_LABELS: Mapping of priority integers to human-readable labels.
    LABEL_TO_PRIORITY: Reverse mapping from labels to priority integers.
    DEFAULT_PRIORITY: Default message priority (2 = "medium").
    DEFAULT_MAX_RETRIES: Maximum retry attempts for temporary failures.
    DEFAULT_RETRY_DELAYS: Exponential backoff delay schedule in seconds.
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from email.message import EmailMessage
from typing import Any

import aiohttp
import aiosmtplib

from .attachments import AttachmentManager
from .attachments.cache import TieredCache
from .config_loader import CacheConfig, load_cache_config
from .logger import get_logger
from .models import get_tenant_attachment_url, get_tenant_sync_url
from .persistence import Persistence
from .prometheus import MailMetrics
from .rate_limit import RateLimiter
from .smtp_pool import SMTPPool

PRIORITY_LABELS = {
    0: "immediate",
    1: "high",
    2: "medium",
    3: "low",
}
LABEL_TO_PRIORITY = {label: value for value, label in PRIORITY_LABELS.items()}
DEFAULT_PRIORITY = 2

# Default retry configuration
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_DELAYS = [60, 300, 900, 3600, 7200]  # 1min, 5min, 15min, 1h, 2h


class AccountConfigurationError(RuntimeError):
    """Raised when a message is missing the information required to resolve an SMTP account."""

    def __init__(self, message: str = "Missing SMTP account configuration"):
        super().__init__(message)
        self.code = "missing_account_configuration"


def _classify_smtp_error(exc: Exception) -> tuple[bool, int | None]:
    """
    Classify an SMTP error as temporary or permanent.

    Returns:
        tuple: (is_temporary, smtp_code)
            - is_temporary: True if the error should trigger a retry
            - smtp_code: The SMTP error code if available, None otherwise
    """
    # Extract SMTP code from aiosmtplib exceptions
    smtp_code = None
    if isinstance(exc, aiosmtplib.SMTPException):
        # aiosmtplib stores code in different attributes depending on exception type
        smtp_code = getattr(exc, 'smtp_code', None) or getattr(exc, 'code', None)

    # Network/timeout errors are temporary
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return True, smtp_code

    # SMTP-specific temporary errors (4xx codes)
    if smtp_code:
        # 4xx codes are temporary failures
        if 400 <= smtp_code < 500:
            return True, smtp_code
        # 5xx codes are permanent failures
        if 500 <= smtp_code < 600:
            return False, smtp_code

    # Check error message for common temporary error patterns
    error_msg = str(exc).lower()
    temporary_patterns = [
        '421',  # Service not available
        '450',  # Mailbox unavailable
        '451',  # Local error in processing
        '452',  # Insufficient system storage
        'timeout',
        'connection refused',
        'connection reset',
        'temporarily unavailable',
        'try again',
        'throttl',  # throttled/throttling
    ]
    for pattern in temporary_patterns:
        if pattern in error_msg:
            return True, smtp_code

    # SSL/TLS configuration errors are permanent (won't fix themselves on retry)
    permanent_patterns = [
        'wrong_version_number',  # TLS/STARTTLS mismatch
        'certificate verify failed',
        'ssl handshake',
        'certificate_unknown',
        'unknown_ca',
        'certificate has expired',
        'self signed certificate',
        'authentication failed',
        'auth',  # Authentication errors (wrong credentials)
        '535',  # Authentication credentials invalid
        '534',  # Authentication mechanism too weak
        '530',  # Authentication required
    ]
    for pattern in permanent_patterns:
        if pattern in error_msg:
            return False, smtp_code

    # Default: treat unknown errors as temporary (safer for retry)
    return True, smtp_code


def _calculate_retry_delay(retry_count: int, delays: list[int] = None) -> int:
    """
    Calculate the delay in seconds before the next retry attempt.

    Args:
        retry_count: Number of previous retry attempts (0-indexed)
        delays: Optional list of delays in seconds for each retry

    Returns:
        Delay in seconds before next retry
    """
    if delays is None:
        delays = DEFAULT_RETRY_DELAYS
    if retry_count >= len(delays):
        # Use the last delay for all subsequent retries
        return delays[-1]
    return delays[retry_count]


class AsyncMailCore:
    """Central orchestrator for the asynchronous mail dispatch service.

    Coordinates all aspects of email delivery including message queue
    management, SMTP connections, rate limiting, retry logic, and
    delivery reporting. Runs background loops for continuous message
    processing and maintenance.

    The core provides a command-based interface for external control,
    supporting operations like adding messages, managing SMTP accounts,
    and controlling the scheduler state.

    Attributes:
        default_host: Default SMTP server hostname when no account specified.
        default_port: Default SMTP server port when no account specified.
        default_user: Default SMTP username when no account specified.
        default_password: Default SMTP password when no account specified.
        default_use_tls: Whether to use TLS for default SMTP connection.
        logger: Logger instance for diagnostic output.
        pool: SMTP connection pool for connection reuse.
        persistence: Database persistence layer for message and account storage.
        rate_limiter: Per-account rate limiting controller.
        metrics: Prometheus metrics collector.
        attachments: Attachment manager for fetching email attachments.
    """

    def __init__(
        self,
        *,
        db_path: str | None = "/data/mail_service.db",
        config_path: str | None = None,
        logger=None,
        metrics: MailMetrics | None = None,
        start_active: bool = False,
        result_queue_size: int = 1000,
        message_queue_size: int = 10000,
        queue_put_timeout: float = 5.0,
        max_enqueue_batch: int = 1000,
        attachment_timeout: int = 30,
        client_sync_url: str | None = None,
        client_sync_user: str | None = None,
        client_sync_password: str | None = None,
        client_sync_token: str | None = None,
        default_priority: int | str = DEFAULT_PRIORITY,
        report_delivery_callable: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        send_loop_interval: float = 0.5,
        report_retention_seconds: int | None = None,
        batch_size_per_account: int = 50,
        test_mode: bool = False,
        log_delivery_activity: bool = False,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delays: list[int] | None = None,
    ):
        """Initialize the mail dispatcher core with configuration options.

        Args:
            db_path: SQLite database path for persistence. Use ":memory:" for
                in-memory database. Defaults to "/data/mail_service.db".
            config_path: Optional path to INI config file for attachment settings.
            logger: Custom logger instance. If None, uses default logger.
            metrics: Prometheus metrics collector. If None, creates new instance.
            start_active: Whether to start processing messages immediately.
            result_queue_size: Maximum size of the delivery result queue.
            message_queue_size: Maximum messages to fetch per SMTP cycle.
            queue_put_timeout: Timeout in seconds for queue operations.
            max_enqueue_batch: Maximum messages allowed in single addMessages call.
            attachment_timeout: Timeout in seconds for fetching attachments.
            client_sync_url: URL for posting delivery reports to upstream service.
            client_sync_user: Username for client sync authentication.
            client_sync_password: Password for client sync authentication.
            client_sync_token: Bearer token for client sync authentication.
            default_priority: Default priority for messages without explicit priority.
            report_delivery_callable: Optional async callable for custom report delivery.
            send_loop_interval: Seconds between SMTP dispatch loop iterations.
            report_retention_seconds: How long to retain reported messages.
            batch_size_per_account: Max messages to send per account per cycle.
            test_mode: Enable test mode (disables automatic loop processing).
            log_delivery_activity: Enable verbose delivery activity logging.
            max_retries: Maximum retry attempts for temporary SMTP failures.
            retry_delays: Custom list of retry delay intervals in seconds.
        """
        self.default_host = None
        self.default_port = None
        self.default_user = None
        self.default_password = None
        self.default_use_tls = False

        self.logger = logger or get_logger()
        self.pool = SMTPPool()
        self.persistence = Persistence(db_path or ":memory:")
        self._config_path = config_path
        self.rate_limiter = RateLimiter(self.persistence)
        self.metrics = metrics or MailMetrics()
        self._queue_put_timeout = queue_put_timeout
        self._max_enqueue_batch = max_enqueue_batch
        self._attachment_timeout = attachment_timeout
        base_send_interval = max(0.05, float(send_loop_interval))
        self._smtp_batch_size = max(1, int(message_queue_size))
        self._report_retention_seconds = (
            report_retention_seconds if report_retention_seconds is not None else 7 * 24 * 3600
        )
        self._test_mode = bool(test_mode)

        self._stop = asyncio.Event()
        self._active = start_active

        self._send_loop_interval = math.inf if self._test_mode else base_send_interval
        self._wake_event = asyncio.Event()  # Wake event for SMTP dispatch loop
        self._wake_client_event = asyncio.Event()  # Wake event for client report loop
        self._run_now_tenant_id: str | None = None  # Tenant to sync on run-now (None = all)
        self._result_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=result_queue_size)
        self._task_smtp: asyncio.Task | None = None
        self._task_client: asyncio.Task | None = None
        self._task_cleanup: asyncio.Task | None = None

        self._client_sync_url = client_sync_url
        self._client_sync_user = client_sync_user
        self._client_sync_password = client_sync_password
        self._client_sync_token = client_sync_token
        self._report_delivery_callable = report_delivery_callable

        # Attachments and cache will be initialized in init()
        self._attachment_cache: TieredCache | None = None
        self._cache_config: CacheConfig | None = None
        self.attachments: AttachmentManager | None = None
        priority_value, _ = self._normalise_priority(default_priority, DEFAULT_PRIORITY)
        self._default_priority = priority_value
        self._log_delivery_activity = bool(log_delivery_activity)
        self._max_retries = max(0, int(max_retries))
        self._retry_delays = retry_delays or DEFAULT_RETRY_DELAYS
        self._batch_size_per_account = max(1, int(batch_size_per_account))

    # --------------------------------------------------------------------- utils
    @staticmethod
    def _utc_now_iso() -> str:
        """Return the current UTC timestamp as ISO-8601 string.

        Returns:
            str: ISO-8601 formatted timestamp with 'Z' suffix.
        """
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _utc_now_epoch() -> int:
        """Return the current UTC timestamp as seconds since Unix epoch.

        Returns:
            int: Unix timestamp in seconds.
        """
        return int(datetime.now(timezone.utc).timestamp())

    async def init(self) -> None:
        """Initialize persistence layer and attachment manager.

        Performs the following initialization steps:
        1. Initialize SQLite database schema
        2. Load cache configuration (from env vars or config file)
        3. Initialize attachment cache (memory and disk tiers)
        4. Create the AttachmentManager
        """
        await self.persistence.init_db()
        await self._refresh_queue_gauge()

        # Load cache configuration from environment or config file
        self._cache_config = load_cache_config(self._config_path)

        # Initialize attachment cache if configured
        if self._cache_config.enabled:
            self._attachment_cache = TieredCache(
                memory_max_mb=self._cache_config.memory_max_mb,
                memory_ttl_seconds=self._cache_config.memory_ttl_seconds,
                disk_dir=self._cache_config.disk_dir,
                disk_max_mb=self._cache_config.disk_max_mb,
                disk_ttl_seconds=self._cache_config.disk_ttl_seconds,
                disk_threshold_kb=self._cache_config.disk_threshold_kb,
            )
            await self._attachment_cache.init()
            self.logger.info(
                f"Attachment cache initialized (memory={self._cache_config.memory_max_mb}MB, "
                f"disk={self._cache_config.disk_dir})"
            )

        # Initialize attachment manager (tenant-specific config applied per-message)
        self.attachments = AttachmentManager(cache=self._attachment_cache)

    def _normalise_priority(self, value: Any, default: Any = DEFAULT_PRIORITY) -> tuple[int, str]:
        """Convert a priority value to internal numeric representation.

        Accepts integers (0-3), strings ("immediate", "high", "medium", "low"),
        or numeric strings and normalizes to (int, label) tuple.

        Args:
            value: Priority value to normalize.
            default: Fallback if value is invalid.

        Returns:
            Tuple of (priority_int, priority_label).
        """
        if isinstance(default, str):
            fallback = LABEL_TO_PRIORITY.get(default.lower(), DEFAULT_PRIORITY)
        elif isinstance(default, (int, float)):
            try:
                fallback = int(default)
            except (TypeError, ValueError):
                fallback = DEFAULT_PRIORITY
        else:
            fallback = DEFAULT_PRIORITY
        fallback = max(0, min(fallback, max(PRIORITY_LABELS)))

        if value is None:
            priority = fallback
        elif isinstance(value, str):
            key = value.lower()
            if key in LABEL_TO_PRIORITY:
                priority = LABEL_TO_PRIORITY[key]
            else:
                try:
                    priority = int(value)
                except ValueError:
                    priority = fallback
        else:
            try:
                priority = int(value)
            except (TypeError, ValueError):
                priority = fallback
        priority = max(0, min(priority, max(PRIORITY_LABELS)))
        label = PRIORITY_LABELS.get(priority, PRIORITY_LABELS[fallback])
        return priority, label

    @staticmethod
    def _summarise_addresses(value: Any) -> str:
        """Create a compact string summary of email addresses for logging.

        Args:
            value: String, list, or other iterable of email addresses.

        Returns:
            Comma-separated addresses, truncated to 200 chars if needed.
        """
        if not value:
            return "-"
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",") if part.strip()]
        elif isinstance(value, (list, tuple, set)):
            items = [str(item).strip() for item in value if item]
        else:
            items = [str(value).strip()]
        preview = ", ".join(item for item in items if item)
        if len(preview) > 200:
            return f"{preview[:197]}..."
        return preview or "-"

    # ------------------------------------------------------------------ commands
    async def handle_command(self, cmd: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Execute an external control command.

        Dispatches the command to the appropriate handler method. Supported commands:
        - ``run now``: Trigger immediate dispatch cycle
        - ``suspend``: Pause the scheduler
        - ``activate``: Resume the scheduler
        - ``addAccount``, ``listAccounts``, ``deleteAccount``: SMTP account management
        - ``addMessages``, ``deleteMessages``, ``listMessages``: Message queue management
        - ``cleanupMessages``: Remove old reported messages
        - ``addTenant``, ``getTenant``, ``listTenants``, ``updateTenant``, ``deleteTenant``: Tenant management

        Args:
            cmd: Command name to execute.
            payload: Command-specific parameters.

        Returns:
            dict: Command result with ``ok`` status and command-specific data.
        """
        payload = payload or {}
        match cmd:
            case "run now":
                # Store tenant_id for targeted sync (None = all tenants)
                self._run_now_tenant_id = payload.get("tenant_id")
                self._wake_event.set()  # Wake SMTP dispatch loop (process messages)
                self._wake_client_event.set()  # Wake client report loop (sync with tenant)
                return {"ok": True}
            case "suspend":
                self._active = False
                return {"ok": True, "active": False}
            case "activate":
                self._active = True
                return {"ok": True, "active": True}
            case "addAccount":
                await self.persistence.add_account(payload)
                return {"ok": True}
            case "listAccounts":
                accounts = await self.persistence.list_accounts()
                return {"ok": True, "accounts": accounts}
            case "deleteAccount":
                account_id = payload.get("id")
                await self.persistence.delete_account(account_id)
                await self._refresh_queue_gauge()
                return {"ok": True}
            case "deleteMessages":
                ids = payload.get("ids") if isinstance(payload, dict) else []
                removed, not_found = await self._delete_messages(ids or [])
                await self._refresh_queue_gauge()
                return {"ok": True, "removed": removed, "not_found": not_found}
            case "listMessages":
                active_only = bool(payload.get("active_only", False)) if isinstance(payload, dict) else False
                messages = await self.persistence.list_messages(active_only=active_only)
                return {"ok": True, "messages": messages}
            case "addMessages":
                return await self._handle_add_messages(payload)
            case "cleanupMessages":
                older_than = payload.get("older_than_seconds") if isinstance(payload, dict) else None
                removed = await self._cleanup_reported_messages(older_than)
                return {"ok": True, "removed": removed}
            case "addTenant":
                await self.persistence.add_tenant(payload)
                return {"ok": True}
            case "getTenant":
                tenant_id = payload.get("id")
                tenant = await self.persistence.get_tenant(tenant_id)
                if tenant:
                    return {"ok": True, **tenant}
                return {"ok": False, "error": "tenant not found"}
            case "listTenants":
                active_only = bool(payload.get("active_only", False)) if isinstance(payload, dict) else False
                tenants = await self.persistence.list_tenants(active_only=active_only)
                return {"ok": True, "tenants": tenants}
            case "updateTenant":
                tenant_id = payload.pop("id", None)
                if not tenant_id:
                    return {"ok": False, "error": "tenant id required"}
                updated = await self.persistence.update_tenant(tenant_id, payload)
                if updated:
                    return {"ok": True}
                return {"ok": False, "error": "tenant not found"}
            case "deleteTenant":
                tenant_id = payload.get("id")
                deleted = await self.persistence.delete_tenant(tenant_id)
                if deleted:
                    return {"ok": True}
                return {"ok": False, "error": "tenant not found"}
            case _:
                return {"ok": False, "error": "unknown command"}

    async def _handle_add_messages(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process the addMessages command to enqueue emails for delivery.

        Validates each message in the batch, checking required fields and account
        configuration. Invalid messages are rejected with detailed reasons and
        optionally persisted for error reporting.

        Args:
            payload: Dict with ``messages`` list and optional ``default_priority``.

        Returns:
            dict: Result with ``ok``, ``queued`` count, and ``rejected`` list.
        """
        messages = payload.get("messages") if isinstance(payload, dict) else None
        if not isinstance(messages, list):
            return {"ok": False, "error": "messages must be a list"}
        if len(messages) > self._max_enqueue_batch:
            return {"ok": False, "error": f"Cannot enqueue more than {self._max_enqueue_batch} messages at once"}

        default_priority_value = 2
        if "default_priority" in payload:
            default_priority_value, _ = self._normalise_priority(payload.get("default_priority"), 2)

        validated: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        rejected_for_sync: list[dict[str, Any]] = []  # Messages to report via proxy_sync
        now_ts = self._utc_now_epoch()

        for item in messages:
            if not isinstance(item, dict):
                rejected.append({"id": None, "reason": "invalid payload"})
                continue
            is_valid, reason = await self._validate_enqueue_payload(item)
            if not is_valid:
                msg_id = item.get("id")
                rejected.append({"id": msg_id, "reason": reason})
                if msg_id:
                    # Insert rejected message into DB with error for proxy_sync notification
                    priority, _ = self._normalise_priority(item.get("priority"), default_priority_value)
                    entry = {
                        "id": msg_id,
                        "account_id": item.get("account_id"),
                        "priority": priority,
                        "payload": item,
                        "deferred_ts": None,
                    }
                    await self.persistence.insert_messages([entry])
                    await self.persistence.mark_error(msg_id, now_ts, reason)
                    rejected_for_sync.append({
                        "id": msg_id,
                        "status": "error",
                        "error": reason,
                        "timestamp": self._utc_now_iso(),
                        "account": item.get("account_id"),
                    })
                continue

            priority, _ = self._normalise_priority(item.get("priority"), default_priority_value)
            item["priority"] = priority
            if "deferred_ts" in item and item["deferred_ts"] is None:
                item.pop("deferred_ts")
            validated.append(item)

        entries = []
        inserted = []

        if validated:
            entries = [
            {
                "id": msg["id"],
                "account_id": msg.get("account_id"),
                "priority": int(msg["priority"]),
                "payload": msg,
                "deferred_ts": msg.get("deferred_ts"),
            }
            for msg in validated
            ]
            inserted = await self.persistence.insert_messages(entries)
            # Messages not inserted were already sent (sent_ts IS NOT NULL)
            for msg in validated:
                if msg["id"] not in inserted:
                    rejected.append({"id": msg["id"], "reason": "already sent"})

        await self._refresh_queue_gauge()

        # Notify client via proxy_sync for rejected messages
        if rejected_for_sync:
            for event in rejected_for_sync:
                await self._publish_result(event)

        queued_count = len([mid for mid in inserted if mid])
        # ok is False only if ALL messages were rejected due to validation errors
        # (not for "already sent" which is a normal case)
        validation_failures = [r for r in rejected if r.get("reason") != "already sent"]
        ok = queued_count > 0 or len(validation_failures) == 0
        result: dict[str, Any] = {
            "ok": ok,
            "queued": queued_count,
            "rejected": rejected,
        }
        return result

    async def _delete_messages(self, message_ids: Iterable[str]) -> tuple[int, list[str]]:
        """Remove messages from the queue by their IDs.

        Args:
            message_ids: Iterable of message IDs to delete.

        Returns:
            Tuple of (count of removed messages, list of IDs not found).
        """
        ids = {mid for mid in message_ids if mid}
        if not ids:
            return 0, []
        removed = 0
        missing: list[str] = []
        for mid in sorted(ids):
            if await self.persistence.delete_message(mid):
                removed += 1
            else:
                missing.append(mid)
        return removed, missing

    async def _cleanup_reported_messages(self, older_than_seconds: int | None = None) -> int:
        """Remove reported messages older than the specified threshold.

        Args:
            older_than_seconds: Remove messages reported more than this many seconds ago.
                              If None, uses the configured retention period.

        Returns:
            Number of messages removed.
        """
        if older_than_seconds is None:
            retention = self._report_retention_seconds
        else:
            retention = max(0, int(older_than_seconds))

        threshold = self._utc_now_epoch() - retention
        removed = await self.persistence.remove_reported_before(threshold)
        if removed:
            await self._refresh_queue_gauge()
        return removed

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Start the background scheduler and maintenance tasks.

        Initializes the persistence layer and spawns background tasks for:
        - SMTP dispatch loop: processes queued messages
        - Client report loop: sends delivery reports to upstream services
        - Cleanup loop: maintains SMTP connection pool health (production only)
        """
        self.logger.debug("Starting AsyncMailCore...")
        await self.init()
        self._stop.clear()
        self.logger.debug("Creating SMTP dispatch loop task...")
        self._task_smtp = asyncio.create_task(self._smtp_dispatch_loop(), name="smtp-dispatch-loop")
        self.logger.debug("Creating client report loop task...")
        self._task_client = asyncio.create_task(self._client_report_loop(), name="client-report-loop")
        if not self._test_mode:
            self.logger.debug("Creating cleanup loop task...")
            self._task_cleanup = asyncio.create_task(self._cleanup_loop(), name="smtp-cleanup-loop")
        self.logger.debug("All background tasks created")

    async def stop(self) -> None:
        """Stop all background tasks gracefully.

        Signals all running loops to terminate and waits for them to complete.
        Outstanding operations are allowed to finish before returning.
        """
        self._stop.set()
        self._wake_event.set()
        self._wake_client_event.set()
        await asyncio.gather(
            *(task for task in [self._task_smtp, self._task_client, self._task_cleanup] if task),
            return_exceptions=True,
        )

    # --------------------------------------------------------------- SMTP logic
    async def _smtp_dispatch_loop(self) -> None:
        """Background loop that continuously processes queued messages.

        Runs until stop() is called, fetching ready messages from the database
        and attempting SMTP delivery. Respects scheduler active/suspended state
        and can be woken early via run-now command.
        """
        self.logger.debug("SMTP dispatch loop started")
        first_iteration = True
        while not self._stop.is_set():
            if first_iteration and self._test_mode:
                self.logger.info("First iteration in test mode, waiting for wakeup")
                await self._wait_for_wakeup(self._send_loop_interval)
            first_iteration = False
            try:
                self.logger.debug("Processing SMTP cycle...")
                processed = await self._process_smtp_cycle()
                self.logger.debug(f"SMTP cycle processed={processed}")
                # If messages were sent, trigger immediate client report sync
                if processed:
                    self.logger.debug("Messages sent, triggering immediate client report sync")
                    self._wake_client_event.set()
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.exception("Unhandled error in SMTP dispatch loop: %s", exc)
                processed = False
            if not processed:
                self.logger.debug(f"No messages processed, waiting {self._send_loop_interval}s")
                await self._wait_for_wakeup(self._send_loop_interval)

    async def _process_smtp_cycle(self) -> bool:
        """Execute one SMTP dispatch cycle.

        Fetches ready messages from the database, groups them by account,
        and processes each account's batch respecting configured limits.

        Returns:
            True if any messages were processed, False otherwise.
        """
        now_ts = self._utc_now_epoch()
        self.logger.debug(f"Fetching ready messages (now_ts={now_ts}, limit={self._smtp_batch_size})")
        batch = await self.persistence.fetch_ready_messages(limit=self._smtp_batch_size, now_ts=now_ts)
        self.logger.debug(f"Fetched {len(batch)} ready messages")
        if not batch:
            await self._refresh_queue_gauge()
            return False

        # Group messages by account_id and apply per-account batch limit
        from collections import defaultdict
        messages_by_account = defaultdict(list)
        for entry in batch:
            account_id = entry.get("message", {}).get("account_id") or "default"
            messages_by_account[account_id].append(entry)

        # Process messages respecting per-account batch size
        processed_any = False
        for account_id, account_messages in messages_by_account.items():
            # Get account-specific batch_size if available, otherwise use global default
            account_batch_size = self._batch_size_per_account
            if account_id and account_id != "default":
                try:
                    account_data = await self.persistence.get_account(account_id)
                    if account_data and account_data.get("batch_size"):
                        account_batch_size = int(account_data["batch_size"])
                except Exception:
                    pass  # Fall back to global default on any error

            # Limit messages for this account to its batch_size
            messages_to_send = account_messages[:account_batch_size]
            skipped_count = len(account_messages) - len(messages_to_send)

            if skipped_count > 0:
                self.logger.info(
                    f"Account {account_id}: processing {len(messages_to_send)} messages, "
                    f"deferring {skipped_count} messages to next cycle (batch_size={account_batch_size})"
                )

            for entry in messages_to_send:
                self.logger.debug(f"Dispatching message {entry.get('id')} for account {account_id}")
                await self._dispatch_message(entry, now_ts)
                processed_any = True

        await self._refresh_queue_gauge()
        return processed_any

    async def _dispatch_message(self, entry: dict[str, Any], now_ts: int) -> None:
        """Attempt to deliver a single message via SMTP.

        Builds the email, resolves the SMTP account, applies rate limits,
        and performs the actual send. Updates message status based on outcome.

        Args:
            entry: Message entry dict with id, message payload, and metadata.
            now_ts: Current UTC timestamp for error/sent timestamp recording.
        """
        msg_id = entry.get("id")
        message = entry.get("message") or {}
        if self._log_delivery_activity:
            recipients_preview = self._summarise_addresses(message.get("to"))
            self.logger.info(
                "Attempting delivery for message %s to %s (account=%s)",
                msg_id or "-",
                recipients_preview,
                message.get("account_id") or "default",
            )
        if msg_id:
            await self.persistence.clear_deferred(msg_id)
        try:
            email_msg, envelope_from = await self._build_email(message)
        except KeyError as exc:
            reason = f"missing {exc}"
            await self.persistence.mark_error(msg_id, now_ts, reason)
            await self._publish_result(
                {
                    "id": msg_id,
                    "status": "error",
                    "error": reason,
                    "timestamp": self._utc_now_iso(),
                    "account": message.get("account_id"),
                }
            )
            return
        except ValueError as exc:
            # Attachment fetch failure or other validation error
            reason = str(exc)
            await self.persistence.mark_error(msg_id, now_ts, reason)
            await self._publish_result(
                {
                    "id": msg_id,
                    "status": "error",
                    "error": reason,
                    "timestamp": self._utc_now_iso(),
                    "account": message.get("account_id"),
                }
            )
            return

        event = await self._send_with_limits(email_msg, envelope_from, msg_id, message)
        if event:
            await self._publish_result(event)

    async def _send_with_limits(
        self,
        msg: EmailMessage,
        envelope_from: str | None,
        msg_id: str | None,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Send an email message with rate limiting and retry logic.

        Resolves the SMTP account, checks rate limits, and attempts delivery.
        Handles temporary failures with exponential backoff retries and
        permanent failures with immediate error reporting.

        Args:
            msg: Constructed EmailMessage ready for sending.
            envelope_from: SMTP envelope sender address.
            msg_id: Message ID for tracking and status updates.
            payload: Original message payload with retry state.

        Returns:
            Event dict describing the outcome (sent/error/deferred), or None
            if the message was deferred due to rate limiting.
        """
        account_id = payload.get("account_id")
        try:
            host, port, user, password, acc = await self._resolve_account(account_id)
        except AccountConfigurationError as exc:
            error_ts = self._utc_now_epoch()
            await self.persistence.mark_error(msg_id or "", error_ts, str(exc))
            return {
                "id": msg_id,
                "status": "error",
                "error": str(exc),
                "error_code": exc.code,
                "timestamp": self._utc_now_iso(),
                "account": account_id or "default",
            }

        use_tls = acc.get("use_tls")
        use_tls = int(port) == 465 if use_tls is None else bool(use_tls)
        resolved_account_id = account_id or acc.get("id") or "default"

        deferred_until = await self.rate_limiter.check_and_plan(acc)
        if deferred_until:
            # Rate limit hit - defer message for later retry (internal scheduling).
            # This is flow control, not an error, so it won't be reported to client.
            await self.persistence.set_deferred(msg_id or "", deferred_until)
            self.metrics.inc_deferred(resolved_account_id)
            self.metrics.inc_rate_limited(resolved_account_id)
            self.logger.debug(
                "Message %s rate-limited for account %s, deferred until %s",
                msg_id,
                resolved_account_id,
                deferred_until,
            )
            return None  # No result to report, message will be retried later

        try:
            async with self.pool.connection(host, port, user, password, use_tls=use_tls) as smtp:
                envelope_sender = envelope_from or msg.get("From")
                # Wrap send_message in timeout to prevent hanging (max 30s for large attachments)
                await asyncio.wait_for(smtp.send_message(msg, sender=envelope_sender), timeout=30.0)
        except Exception as exc:
            # Classify the error as temporary or permanent
            is_temporary, smtp_code = _classify_smtp_error(exc)

            # Get current retry count from payload
            retry_count = payload.get("retry_count", 0)

            # Determine if we should retry
            should_retry = is_temporary and retry_count < self._max_retries

            if should_retry:
                # Calculate next retry timestamp
                delay = _calculate_retry_delay(retry_count, self._retry_delays)
                deferred_until = self._utc_now_epoch() + delay

                # Update payload with incremented retry count
                updated_payload = dict(payload)
                updated_payload["retry_count"] = retry_count + 1

                # Store updated payload and defer the message
                await self.persistence.update_message_payload(msg_id or "", updated_payload)
                await self.persistence.set_deferred(msg_id or "", deferred_until)
                self.metrics.inc_deferred(resolved_account_id)

                # Log the retry attempt
                error_info = f"{exc} (SMTP {smtp_code})" if smtp_code else str(exc)
                self.logger.warning(
                    "Temporary error for message %s (attempt %d/%d): %s - retrying in %ds",
                    msg_id,
                    retry_count + 1,
                    self._max_retries,
                    error_info,
                    delay,
                )

                return {
                    "id": msg_id,
                    "status": "deferred",
                    "deferred_until": deferred_until,
                    "error": error_info,
                    "retry_count": retry_count + 1,
                    "timestamp": self._utc_now_iso(),
                    "account": resolved_account_id,
                }
            else:
                # Permanent error or max retries exceeded - mark as failed
                error_ts = self._utc_now_epoch()
                error_info = f"{exc} (SMTP {smtp_code})" if smtp_code else str(exc)

                if retry_count >= self._max_retries:
                    error_info = f"Max retries ({self._max_retries}) exceeded: {error_info}"
                    self.logger.error(
                        "Message %s failed permanently after %d attempts: %s",
                        msg_id,
                        retry_count,
                        error_info,
                    )
                else:
                    self.logger.error(
                        "Message %s failed with permanent error: %s",
                        msg_id,
                        error_info,
                    )

                await self.persistence.mark_error(msg_id or "", error_ts, error_info)
                self.metrics.inc_error(resolved_account_id)

                return {
                    "id": msg_id,
                    "status": "error",
                    "error": error_info,
                    "smtp_code": smtp_code,
                    "retry_count": retry_count,
                    "timestamp": self._utc_now_iso(),
                    "account": resolved_account_id,
                }

        sent_ts = self._utc_now_epoch()
        await self.persistence.mark_sent(msg_id or "", sent_ts)
        await self.rate_limiter.log_send(resolved_account_id)
        self.metrics.inc_sent(resolved_account_id)
        return {
            "id": msg_id,
            "status": "sent",
            "timestamp": self._utc_now_iso(),
            "account": resolved_account_id,
        }

    # ----------------------------------------------------------- client reporting
    async def _client_report_loop(self) -> None:
        """
        Background coroutine that pushes delivery reports.

        Optimization: When client returns queued > 0, loops immediately to fetch
        more messages. When SMTP loop sends messages, it triggers this loop via
        _wake_client_event to reduce delivery report latency. Otherwise, uses a
        5-minute fallback timeout.
        """
        first_iteration = True
        fallback_interval = 300  # 5 minutes fallback if no immediate wake-up
        while not self._stop.is_set():
            if first_iteration and self._test_mode:
                await self._wait_for_client_wakeup(math.inf)
            first_iteration = False

            try:
                queued = await self._process_client_cycle()

                # If client has queued messages, sync again immediately
                if queued and queued > 0:
                    self.logger.debug(
                        "Client has %d queued messages, syncing immediately", queued
                    )
                    continue  # Loop immediately without waiting

            except Exception as exc:  # pragma: no cover - defensive
                self.logger.exception("Unhandled error in client report loop: %s", exc)

            # No queued messages - wait for wake event or fallback interval
            interval = math.inf if self._test_mode else fallback_interval
            await self._wait_for_client_wakeup(interval)

    async def _process_client_cycle(self) -> int:
        """Perform one delivery report cycle, routing to per-tenant endpoints.

        Returns:
            Total number of messages queued by all clients (for intelligent polling).
        """
        if not self._active:
            return 0

        # Check if run-now was triggered for a specific tenant
        target_tenant_id = self._run_now_tenant_id
        self._run_now_tenant_id = None  # Reset for next cycle

        # Track total queued messages from all clients
        total_queued = 0

        reports = await self.persistence.fetch_reports(self._smtp_batch_size)
        if not reports:
            # Trigger sync for tenants with sync URL (even without reports)
            # This allows the tenant server to send new messages to enqueue
            if target_tenant_id:
                # Sync only the specified tenant
                tenant = await self.persistence.get_tenant(target_tenant_id)
                if tenant and tenant.get("active") and get_tenant_sync_url(tenant):
                    try:
                        _, queued = await self._send_reports_to_tenant(tenant, [])
                        total_queued += queued
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        self.logger.warning(
                            "Client sync for tenant %s not reachable: %s",
                            target_tenant_id,
                            exc,
                        )
            else:
                # Sync all active tenants
                tenants = await self.persistence.list_tenants()
                for tenant in tenants:
                    if tenant.get("active") and get_tenant_sync_url(tenant):
                        try:
                            _, queued = await self._send_reports_to_tenant(tenant, [])
                            total_queued += queued
                        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                            self.logger.warning(
                                "Client sync for tenant %s not reachable: %s",
                                tenant.get("id"),
                                exc,
                            )
                # Also call global URL if configured (backward compatibility)
                if self._client_sync_url and self._report_delivery_callable is None:
                    try:
                        _, queued = await self._send_delivery_reports([])
                        total_queued += queued
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        self.logger.warning(
                            "Client sync endpoint %s not reachable: %s",
                            self._client_sync_url,
                            exc,
                        )
            await self._apply_retention()
            return total_queued

        # Group reports by tenant_id for per-tenant delivery
        # Payload minimale: solo id, sent_ts, error_ts, error
        from collections import defaultdict
        reports_by_tenant: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for item in reports:
            tenant_id = item.get("tenant_id")
            payload = {
                "id": item.get("id"),
                "sent_ts": item.get("sent_ts"),
                "error_ts": item.get("error_ts"),
                "error": item.get("error"),
            }
            reports_by_tenant[tenant_id].append(payload)

        # Track acknowledged message IDs (only mark as reported if client confirms)
        acked_ids: list[str] = []

        # Send reports to each tenant's endpoint
        for tenant_id, payloads in reports_by_tenant.items():
            try:
                if tenant_id:
                    # Get tenant configuration and send to tenant-specific endpoint
                    tenant = await self.persistence.get_tenant(tenant_id)
                    if tenant and get_tenant_sync_url(tenant):
                        acked, queued = await self._send_reports_to_tenant(tenant, payloads)
                        acked_ids.extend(acked)
                        total_queued += queued
                    elif self._client_sync_url:
                        # Fallback to global URL if tenant has no sync URL
                        acked, queued = await self._send_delivery_reports(payloads)
                        acked_ids.extend(acked)
                        total_queued += queued
                    else:
                        self.logger.warning(
                            "No sync URL for tenant %s and no global fallback, skipping %d reports",
                            tenant_id, len(payloads)
                        )
                        continue
                else:
                    # No tenant - use global URL
                    if self._client_sync_url or self._report_delivery_callable:
                        acked, queued = await self._send_delivery_reports(payloads)
                        acked_ids.extend(acked)
                        total_queued += queued
                    else:
                        self.logger.warning(
                            "No tenant and no global sync URL configured, skipping %d reports",
                            len(payloads)
                        )
                        continue
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                target = tenant_id or "global"
                self.logger.warning(
                    "Client sync delivery failed for tenant %s: %s", target, exc
                )
                # Don't mark these as reported - they'll be retried next cycle

        # Mark only acknowledged messages as reported
        if acked_ids:
            reported_ts = self._utc_now_epoch()
            await self.persistence.mark_reported(acked_ids, reported_ts)

        await self._apply_retention()
        return total_queued

    async def _apply_retention(self) -> None:
        """Remove reported messages older than the configured retention period.

        Messages that have been successfully reported to upstream services
        are deleted after the retention period expires to prevent database growth.
        """
        if self._report_retention_seconds <= 0:
            return
        threshold = self._utc_now_epoch() - self._report_retention_seconds
        removed = await self.persistence.remove_reported_before(threshold)
        if removed:
            await self._refresh_queue_gauge()

    # ---------------------------------------------------------------- housekeeping
    async def _cleanup_loop(self) -> None:
        """Background loop that maintains SMTP connection pool health.

        Periodically removes idle or expired connections from the pool
        to prevent resource leaks and connection timeouts.
        """
        while not self._stop.is_set():
            await asyncio.sleep(150)
            await self.pool.cleanup()

    async def _refresh_queue_gauge(self) -> None:
        """Update the Prometheus gauge for pending message count.

        Queries the database for active (unsent, unreported) messages
        and updates the metrics collector.
        """
        try:
            count = await self.persistence.count_active_messages()
        except Exception:  # pragma: no cover - defensive
            self.logger.exception("Failed to refresh queue gauge")
            return
        self.metrics.set_pending(count)

    async def _wait_for_wakeup(self, timeout: float | None) -> None:
        """Pause the SMTP dispatch loop until timeout or wake event.

        Args:
            timeout: Maximum seconds to wait. None or infinity waits indefinitely.
        """
        self.logger.debug(f"_wait_for_wakeup called with timeout={timeout}")
        if self._stop.is_set():
            self.logger.debug("_stop is set, returning immediately")
            return
        if timeout is None:
            self.logger.debug("Waiting indefinitely for wake event")
            await self._wake_event.wait()
            self._wake_event.clear()
            return
        timeout = float(timeout)
        if math.isinf(timeout):
            self.logger.debug("Infinite timeout, waiting for wake event")
            await self._wake_event.wait()
            self._wake_event.clear()
            return
        timeout = max(0.0, timeout)
        if timeout == 0:
            self.logger.debug("Zero timeout, yielding")
            await asyncio.sleep(0)
            return
        self.logger.debug(f"Waiting {timeout}s for wake event or timeout")
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
            self.logger.debug("Woken up by event")
        except asyncio.TimeoutError:
            self.logger.debug(f"Timeout after {timeout}s")
            return
        self._wake_event.clear()

    async def _wait_for_client_wakeup(self, timeout: float | None) -> None:
        """Pause the client report loop until timeout or wake event.

        Args:
            timeout: Maximum seconds to wait. None or infinity waits indefinitely.
        """
        if self._stop.is_set():
            return
        if timeout is None:
            await self._wake_client_event.wait()
            self._wake_client_event.clear()
            return
        timeout = float(timeout)
        if math.isinf(timeout):
            await self._wake_client_event.wait()
            self._wake_client_event.clear()
            return
        timeout = max(0.0, timeout)
        if timeout == 0:
            await asyncio.sleep(0)
            return
        try:
            await asyncio.wait_for(self._wake_client_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return
        self._wake_client_event.clear()

    # ----------------------------------------------------------------- messaging
    async def results(self):
        """Async generator that yields delivery result events.

        Yields:
            dict: Delivery event with message ID, status, timestamp, and error info.
        """
        while True:
            event = await self._result_queue.get()
            yield event

    async def _put_with_backpressure(self, queue: asyncio.Queue[Any], item: Any, queue_name: str) -> None:
        """Push an item to a queue with timeout-based backpressure.

        Args:
            queue: Target asyncio.Queue.
            item: Item to enqueue.
            queue_name: Name for logging purposes.
        """
        try:
            await asyncio.wait_for(queue.put(item), timeout=self._queue_put_timeout)
        except asyncio.TimeoutError:  # pragma: no cover - defensive
            self.logger.error("Timed out while enqueuing item into %s queue; dropping item", queue_name)

    def _log_delivery_event(self, event: dict[str, Any]) -> None:
        """Log a delivery outcome when verbose logging is enabled.

        Args:
            event: Delivery event dict with status, id, account, and error info.
        """
        if not self._log_delivery_activity:
            return
        status = (event.get("status") or "unknown").lower()
        msg_id = event.get("id") or "-"
        account = event.get("account") or event.get("account_id") or "default"

        match status:
            case "sent":
                self.logger.info("Delivery succeeded for message %s (account=%s)", msg_id, account)
            case "deferred":
                deferred_until = event.get("deferred_until")
                if isinstance(deferred_until, (int, float)):
                    deferred_repr = (
                        datetime.fromtimestamp(float(deferred_until), timezone.utc)
                        .isoformat()
                        .replace("+00:00", "Z")
                    )
                else:
                    deferred_repr = deferred_until or "-"
                self.logger.info(
                    "Delivery deferred for message %s (account=%s) until %s",
                    msg_id,
                    account,
                    deferred_repr,
                )
            case "error":
                reason = event.get("error") or event.get("error_code") or "unknown error"
                self.logger.warning(
                    "Delivery failed for message %s (account=%s): %s",
                    msg_id,
                    account,
                    reason,
                )
            case _:
                self.logger.info("Delivery event for message %s (account=%s): %s", msg_id, account, status)

    async def _publish_result(self, event: dict[str, Any]) -> None:
        """Publish a delivery event to the result queue.

        Args:
            event: Delivery event dict to publish.
        """
        self._log_delivery_event(event)
        await self._put_with_backpressure(self._result_queue, event, "result")

    # ---------------------------------------------------------- SMTP primitives
    async def _resolve_account(self, account_id: str | None) -> tuple[str, int, str | None, str | None, dict[str, Any]]:
        """Resolve SMTP connection parameters for a message.

        Args:
            account_id: Account ID to look up, or None to use defaults.

        Returns:
            Tuple of (host, port, user, password, account_dict).

        Raises:
            AccountConfigurationError: If no account found and no defaults configured.
        """
        if account_id:
            acc = await self.persistence.get_account(account_id)
            return acc["host"], int(acc["port"]), acc.get("user"), acc.get("password"), acc
        if self.default_host and self.default_port:
            return (
                self.default_host,
                int(self.default_port),
                self.default_user,
                self.default_password,
                {"id": "default", "use_tls": self.default_use_tls},
            )
        raise AccountConfigurationError()

    async def _build_email(self, data: dict[str, Any]) -> tuple[EmailMessage, str]:
        """Build an EmailMessage from a message payload.

        Constructs headers (From, To, Cc, Bcc, Subject, etc.), sets the body
        content with appropriate MIME type, and fetches/attaches any attachments.

        Args:
            data: Message payload with from, to, subject, body, attachments, etc.

        Returns:
            Tuple of (EmailMessage, envelope_sender_address).

        Raises:
            KeyError: If required fields (from, to) are missing.
            ValueError: If attachment fetching fails.
        """

        def _format_addresses(value: Any) -> str | None:
            if not value:
                return None
            if isinstance(value, str):
                items = [part.strip() for part in value.split(",") if part.strip()]
                return ", ".join(items) if items else None
            if isinstance(value, (list, tuple, set)):
                items = [str(addr).strip() for addr in value if addr]
                return ", ".join(items) if items else None
            return str(value)

        msg = EmailMessage()
        msg["From"] = data["from"]
        to_value = _format_addresses(data.get("to"))
        if not to_value:
            raise KeyError("to")
        msg["To"] = to_value
        msg["Subject"] = data["subject"]
        if cc_value := _format_addresses(data.get("cc")):
            msg["Cc"] = cc_value
        if bcc_value := _format_addresses(data.get("bcc")):
            msg["Bcc"] = bcc_value
        if reply_to := data.get("reply_to"):
            msg["Reply-To"] = reply_to
        if message_id := data.get("message_id"):
            msg["Message-ID"] = message_id
        envelope_from = data.get("return_path") or data["from"]
        subtype = "html" if data.get("content_type", "plain") == "html" else "plain"
        msg.set_content(data.get("body", ""), subtype=subtype)
        for header, value in (data.get("headers") or {}).items():
            if value is None:
                continue
            value_str = str(value)
            if header in msg:
                msg.replace_header(header, value_str)
            else:
                msg[header] = value_str

        attachments = data.get("attachments", []) or []
        if attachments:
            # Determine which attachment manager to use (tenant-specific or global)
            attachment_manager = await self._get_attachment_manager_for_message(data)
            results = await asyncio.gather(
                *[self._fetch_attachment_with_timeout(att, attachment_manager) for att in attachments],
                return_exceptions=True,
            )
            for att, result in zip(attachments, results, strict=True):
                filename = att.get("filename", "file.bin")
                if isinstance(result, Exception):
                    self.logger.error("Failed to fetch attachment %s: %s - message will not be sent", filename, result)
                    raise ValueError(f"Attachment fetch failed for {filename}: {result}")
                if result is None:
                    self.logger.error("Attachment without data (filename=%s) - message will not be sent", filename)
                    raise ValueError(f"Attachment {filename} returned no data")
                content, resolved_filename = result
                # Use explicit mime_type if provided, otherwise guess from filename
                mime_type_override = att.get("mime_type")
                if mime_type_override and "/" in mime_type_override:
                    maintype, subtype = mime_type_override.split("/", 1)
                else:
                    maintype, subtype = self.attachments.guess_mime(resolved_filename)
                msg.add_attachment(content, maintype=maintype, subtype=subtype, filename=resolved_filename)
        return msg, envelope_from

    async def _get_attachment_manager_for_message(self, data: dict[str, Any]) -> AttachmentManager:
        """Get the appropriate AttachmentManager for a message.

        If the message's account is associated with a tenant that has custom
        attachment settings (client_base_url + client_attachment_path, client_auth),
        creates a temporary AttachmentManager with that config.
        Otherwise returns the global AttachmentManager.

        Args:
            data: Message payload dictionary containing account_id.

        Returns:
            AttachmentManager configured for the message's tenant or the global one.
        """
        account_id = data.get("account_id")
        if not account_id:
            return self.attachments

        # Try to get tenant config for this account
        tenant = await self.persistence.get_tenant_for_account(account_id)
        if not tenant:
            return self.attachments

        # Check if tenant has custom attachment settings
        tenant_attachment_url = get_tenant_attachment_url(tenant)
        tenant_auth = tenant.get("client_auth")

        # If no tenant-specific settings, use global manager
        if not tenant_attachment_url and not tenant_auth:
            return self.attachments

        # Build http_auth_config from tenant's auth config
        http_auth_config = None
        if tenant_auth:
            http_auth_config = {
                "method": tenant_auth.get("method", "none"),
                "token": tenant_auth.get("token"),
                "user": tenant_auth.get("user"),
                "password": tenant_auth.get("password"),
            }

        # Create tenant-specific manager
        return AttachmentManager(
            http_endpoint=tenant_attachment_url,
            http_auth_config=http_auth_config,
            cache=self._attachment_cache,
        )

    async def _fetch_attachment_with_timeout(
        self,
        att: dict[str, Any],
        attachment_manager: AttachmentManager | None = None,
    ) -> tuple[bytes, str] | None:
        """Fetch an attachment using the configured timeout budget.

        The AttachmentManager.fetch() now returns Tuple[bytes, clean_filename]
        where clean_filename has any MD5 marker stripped.

        Args:
            att: Attachment specification dictionary.
            attachment_manager: Optional manager to use. If None, uses self.attachments.
        """
        manager = attachment_manager or self.attachments
        try:
            result = await asyncio.wait_for(manager.fetch(att), timeout=self._attachment_timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"Attachment {att.get('filename', 'file.bin')} fetch timed out") from exc
        # result is Optional[Tuple[bytes, str]] - content and clean filename
        return result

    # ------------------------------------------------------------ client bridge
    async def _send_delivery_reports(self, payloads: list[dict[str, Any]]) -> tuple[list[str], int]:
        """Send delivery report payloads to the configured proxy or callback.

        Returns:
            Tuple of (message IDs that were processed, queued message count from client).
        """
        if self._report_delivery_callable is not None:
            if self._log_delivery_activity:
                batch_size = len(payloads)
                ids_preview = ", ".join(
                    str(item.get("id")) for item in payloads[:5] if item.get("id")
                )
                if len(payloads) > 5:
                    ids_preview = f"{ids_preview}, ..." if ids_preview else "..."
                self.logger.info(
                    "Forwarding %d delivery report(s) via custom callable (ids=%s)",
                    batch_size,
                    ids_preview or "-",
                )
            for payload in payloads:
                await self._report_delivery_callable(payload)
            # When using callable, assume all IDs are processed, no queued info available
            return [p["id"] for p in payloads if p.get("id")], 0
        if not self._client_sync_url:
            if payloads:
                raise RuntimeError("Client sync URL is not configured")
            return [], 0
        headers: dict[str, str] = {}
        auth = None
        if self._client_sync_token:
            headers["Authorization"] = f"Bearer {self._client_sync_token}"
        elif self._client_sync_user:
            auth = aiohttp.BasicAuth(self._client_sync_user, self._client_sync_password or "")
        batch_size = len(payloads)
        if self._log_delivery_activity:
            ids_preview = ", ".join(str(item.get("id")) for item in payloads[:5] if item.get("id"))
            if len(payloads) > 5:
                ids_preview = f"{ids_preview}, ..." if ids_preview else "..."
            self.logger.info(
                "Posting delivery reports to client sync endpoint %s (count=%d, ids=%s)",
                self._client_sync_url,
                batch_size,
                ids_preview or "-",
            )
        else:
            self.logger.debug(
                "Posting delivery reports to client sync endpoint %s (count=%d)",
                self._client_sync_url,
                batch_size,
            )
        async with aiohttp.ClientSession() as session:
            async with session.post(
                self._client_sync_url,
                json={"delivery_report": payloads},
                auth=auth,
                headers=headers or None,
            ) as resp:
                resp.raise_for_status()
                # All IDs are marked as reported on valid JSON response
                # Response format: {"ok": true, "queued": N} or {"error": [...], "not_found": [...], "queued": N}
                processed_ids: list[str] = [p["id"] for p in payloads]
                error_ids: list[str] = []
                not_found_ids: list[str] = []
                is_ok = False
                queued_count = 0
                try:
                    response_data = await resp.json()
                    is_ok = response_data.get("ok", False)
                    error_ids = response_data.get("error", [])
                    not_found_ids = response_data.get("not_found", [])
                    queued_count = response_data.get("queued", 0)
                except Exception:
                    # No valid JSON response - still mark all as reported to avoid infinite loops
                    self.logger.warning(
                        "Client sync returned non-JSON response"
                    )

        if self._log_delivery_activity:
            if is_ok:
                self.logger.info(
                    "Client sync: all %d reports processed OK, client queued %d messages",
                    batch_size,
                    queued_count,
                )
            else:
                sent_count = batch_size - len(error_ids) - len(not_found_ids)
                self.logger.info(
                    "Client sync: sent=%d, error=%d, not_found=%d, client queued=%d",
                    sent_count,
                    len(error_ids),
                    len(not_found_ids),
                    queued_count,
                )
        else:
            self.logger.debug(
                "Delivery report batch delivered (%d reports, client queued %d)",
                batch_size,
                queued_count,
            )
        return processed_ids, queued_count

    async def _send_reports_to_tenant(
        self, tenant: dict[str, Any], payloads: list[dict[str, Any]]
    ) -> tuple[list[str], int]:
        """Send delivery report payloads to a tenant-specific endpoint.

        Args:
            tenant: Tenant configuration dict with client_base_url and client_auth.
            payloads: List of delivery report payloads to send.

        Returns:
            Tuple of (message IDs acknowledged by client, queued message count from client).

        Raises:
            aiohttp.ClientError: If the HTTP request fails.
            asyncio.TimeoutError: If the request times out.
        """
        sync_url = get_tenant_sync_url(tenant)
        if not sync_url:
            raise RuntimeError(f"Tenant {tenant.get('id')} has no sync URL configured")

        # Build authentication from tenant config (common auth for all endpoints)
        headers: dict[str, str] = {}
        auth = None
        auth_config = tenant.get("client_auth") or {}
        auth_method = auth_config.get("method", "none")

        if auth_method == "bearer":
            token = auth_config.get("token")
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif auth_method == "basic":
            user = auth_config.get("user", "")
            password = auth_config.get("password", "")
            auth = aiohttp.BasicAuth(user, password)

        tenant_id = tenant.get("id", "unknown")
        batch_size = len(payloads)

        if self._log_delivery_activity:
            ids_preview = ", ".join(str(item.get("id")) for item in payloads[:5] if item.get("id"))
            if len(payloads) > 5:
                ids_preview = f"{ids_preview}, ..." if ids_preview else "..."
            self.logger.info(
                "Posting delivery reports to tenant %s at %s (count=%d, ids=%s)",
                tenant_id,
                sync_url,
                batch_size,
                ids_preview or "-",
            )
        else:
            self.logger.debug(
                "Posting delivery reports to tenant %s at %s (count=%d)",
                tenant_id,
                sync_url,
                batch_size,
            )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                sync_url,
                json={"delivery_report": payloads},
                auth=auth,
                headers=headers or None,
            ) as resp:
                resp.raise_for_status()
                # All IDs are marked as reported on valid JSON response
                # Response format: {"ok": true, "queued": N} or {"error": [...], "not_found": [...], "queued": N}
                processed_ids: list[str] = [p["id"] for p in payloads]
                error_ids: list[str] = []
                not_found_ids: list[str] = []
                is_ok = False
                queued_count = 0
                try:
                    response_data = await resp.json()
                    is_ok = response_data.get("ok", False)
                    error_ids = response_data.get("error", [])
                    not_found_ids = response_data.get("not_found", [])
                    queued_count = response_data.get("queued", 0)
                except Exception as e:
                    # No valid JSON response - still mark all as reported to avoid infinite loops
                    response_text = await resp.text()
                    self.logger.warning(
                        "Tenant %s returned non-JSON response (error=%s, content-type=%s, body=%s)",
                        tenant_id,
                        e,
                        resp.content_type,
                        response_text[:500] if response_text else "<empty>",
                    )

        if self._log_delivery_activity:
            if is_ok:
                self.logger.info(
                    "Tenant %s: all %d reports processed OK, client queued %d messages",
                    tenant_id,
                    batch_size,
                    queued_count,
                )
            else:
                sent_count = batch_size - len(error_ids) - len(not_found_ids)
                self.logger.info(
                    "Tenant %s: sent=%d, error=%d, not_found=%d, client queued=%d",
                    tenant_id,
                    sent_count,
                    len(error_ids),
                    len(not_found_ids),
                    queued_count,
                )
        else:
            self.logger.debug(
                "Delivery report batch to tenant %s (%d reports, client queued %d)",
                tenant_id,
                batch_size,
                queued_count,
            )
        return processed_ids, queued_count

    # ------------------------------------------------------------- validations
    async def _validate_enqueue_payload(self, payload: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate a message payload before enqueueing.

        Checks for required fields (id, from, to) and verifies that the
        specified SMTP account exists if provided.

        Args:
            payload: Message payload dict to validate.

        Returns:
            Tuple of (is_valid, error_reason). error_reason is None if valid.
        """
        msg_id = payload.get("id")
        if not msg_id:
            return False, "missing id"
        payload.setdefault("priority", 2)
        sender = payload.get("from")
        if not sender:
            return False, "missing from"
        recipients = payload.get("to")
        if not recipients:
            return False, "missing to"
        if isinstance(recipients, (list, tuple, set)):
            if not any(recipients):
                return False, "missing to"
        account_id = payload.get("account_id")
        if account_id:
            try:
                await self.persistence.get_account(account_id)
            except Exception:
                return False, "account not found"
        elif not (self.default_host and self.default_port):
            return False, "missing account configuration"
        return True, None
