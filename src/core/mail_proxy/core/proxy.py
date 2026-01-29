# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Core orchestration logic for the asynchronous mail dispatcher.

This module provides the MailProxy class, the central coordinator for
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
    Running the mail dispatcher (recommended)::

        from mail_proxy.core import MailProxy

        # Recommended: use create() for automatic initialization
        proxy = await MailProxy.create(
            db_path="/data/mail.db",
            start_active=True,
            client_sync_url="https://api.example.com/delivery-report"
        )
        # Ready to use immediately

        # To stop gracefully
        await proxy.stop()

    Alternative pattern for delayed startup::

        proxy = MailProxy(db_path="/data/mail.db")
        # ... additional setup ...
        await proxy.start()

Attributes:
    PRIORITY_LABELS: Mapping of priority integers to human-readable labels.
    LABEL_TO_PRIORITY: Reverse mapping from labels to priority integers.
    DEFAULT_PRIORITY: Default message priority (2 = "medium").
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import datetime, timezone
from typing import Any

from ..attachments import AttachmentManager
from ..attachments.cache import TieredCache
from ..config_loader import CacheConfig, load_cache_config
from ..logger import get_logger
from ..mailproxy_db import MailProxyDb
from ..prometheus import MailMetrics
from ..rate_limit import RateLimiter
from ..retry import DEFAULT_MAX_RETRIES, DEFAULT_RETRY_DELAYS, RetryStrategy
from ..smtp_pool import SMTPPool
from .bounce_mixin import BounceReceiverMixin
from .dispatcher import DispatcherMixin
from .reporting import DEFAULT_SYNC_INTERVAL, ReporterMixin

PRIORITY_LABELS = {
    0: "immediate",
    1: "high",
    2: "medium",
    3: "low",
}
LABEL_TO_PRIORITY = {label: value for value, label in PRIORITY_LABELS.items()}
DEFAULT_PRIORITY = 2


class AccountConfigurationError(RuntimeError):
    """Raised when a message is missing the information required to resolve an SMTP account."""

    def __init__(self, message: str = "Missing SMTP account configuration"):
        super().__init__(message)
        self.code = "missing_account_configuration"


class AttachmentTooLargeError(ValueError):
    """Raised when an attachment exceeds the size limit and action is 'reject'."""

    def __init__(self, filename: str, size_mb: float, max_size_mb: float):
        self.filename = filename
        self.size_mb = size_mb
        self.max_size_mb = max_size_mb
        super().__init__(
            f"Attachment '{filename}' ({size_mb:.1f} MB) exceeds limit ({max_size_mb} MB)"
        )


class MailProxy(DispatcherMixin, ReporterMixin, BounceReceiverMixin):
    """Central orchestrator for the asynchronous mail dispatch service.

    Coordinates all aspects of email delivery including message queue
    management, SMTP connections, rate limiting, retry logic, and
    delivery reporting. Runs background loops for continuous message
    processing and maintenance.

    The core provides a command-based interface for external control,
    supporting operations like adding messages, managing SMTP accounts,
    and controlling the scheduler state.

    Attributes:
        is_enterprise: True if Enterprise Edition features are available.
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

    # Edition flag: True when Enterprise Edition modules are available.
    # This will be set dynamically in mail_proxy/__init__.py when EE is installed.
    is_enterprise: bool = True

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
        retry_strategy: RetryStrategy | None = None,
        max_retries: int | None = None,
        retry_delays: list[int] | None = None,
        max_concurrent_sends: int = 10,
        max_concurrent_per_account: int = 3,
        max_concurrent_attachments: int = 3,
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
            retry_strategy: RetryStrategy instance for configuring retry behavior.
                If provided, max_retries and retry_delays are ignored.
            max_retries: Maximum retry attempts (deprecated, use retry_strategy).
            retry_delays: Custom retry delays (deprecated, use retry_strategy).
            max_concurrent_sends: Maximum concurrent SMTP sends globally.
            max_concurrent_per_account: Maximum concurrent sends per SMTP account.
            max_concurrent_attachments: Maximum concurrent attachment fetches to
                limit memory pressure from large attachments.
        """
        import math

        self.default_host: str | None = None
        self.default_port: int | None = None
        self.default_user: str | None = None
        self.default_password: str | None = None
        self.default_use_tls: bool | None = False

        self.logger = logger or get_logger()
        self.pool = SMTPPool()
        self.db = MailProxyDb(db_path or ":memory:")
        self._config_path = config_path
        self.rate_limiter = RateLimiter(self.db)
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
        self._wake_cleanup_event = asyncio.Event()  # Wake event for cleanup loop
        self._run_now_tenant_id: str | None = None  # Tenant to sync on run-now (None = all)
        self._last_sync: dict[str, float] = {}  # tenant_id → last sync timestamp (or future for DND)
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
        # Build retry strategy from explicit param or legacy params
        if retry_strategy is not None:
            self._retry_strategy = retry_strategy
        else:
            self._retry_strategy = RetryStrategy(
                max_retries=max_retries if max_retries is not None else DEFAULT_MAX_RETRIES,
                delays=tuple(retry_delays) if retry_delays else DEFAULT_RETRY_DELAYS,
            )
        self._batch_size_per_account = max(1, int(batch_size_per_account))
        self._max_concurrent_sends = max(1, int(max_concurrent_sends))
        self._max_concurrent_per_account = max(1, int(max_concurrent_per_account))
        self._max_concurrent_attachments = max(1, int(max_concurrent_attachments))
        self._account_semaphores: dict[str, asyncio.Semaphore] = {}
        self._attachment_semaphore: asyncio.Semaphore | None = None

        # Initialize bounce receiver mixin state
        self.__init_bounce_receiver__()

    @classmethod
    async def create(cls, **kwargs) -> "MailProxy":
        """Create and initialize a MailProxy instance.

        This is the recommended way to create instances. It ensures proper
        async initialization is completed before returning a ready-to-use proxy.

        Args:
            **kwargs: All arguments accepted by MailProxy.__init__().

        Returns:
            Fully initialized MailProxy instance with background tasks running.

        Example:
            proxy = await MailProxy.create(db_path="./mail.db")
            # Ready to use immediately - no need to call start()

        Note:
            For cases requiring delayed startup, use the traditional pattern::

                proxy = MailProxy(db_path="./mail.db")
                # ... additional setup ...
                await proxy.start()
        """
        instance = cls(**kwargs)
        await instance.start()
        return instance

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
        await self.db.init_db()
        await self._refresh_queue_gauge()

        # Initialize metrics for all existing accounts so they appear in /metrics
        # even before any email activity occurs
        await self._init_account_metrics()

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

        # Initialize attachment fetch semaphore to limit memory pressure
        self._attachment_semaphore = asyncio.Semaphore(self._max_concurrent_attachments)

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

    # Commands that modify state and should be logged for audit trail
    _LOGGED_COMMANDS = frozenset({
        "addMessages", "deleteMessages", "cleanupMessages",
        "addAccount", "deleteAccount",
        "addTenant", "updateTenant", "deleteTenant",
        "suspend", "activate",
    })

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

        State-modifying commands are automatically logged to the command_log table
        for audit trail and replay capability.

        Args:
            cmd: Command name to execute.
            payload: Command-specific parameters.

        Returns:
            dict: Command result with ``ok`` status and command-specific data.
        """
        payload = payload or {}

        # Log state-modifying commands for audit trail
        should_log = cmd in self._LOGGED_COMMANDS
        tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None

        result = await self._execute_command(cmd, payload)

        # Log after execution to capture result status
        if should_log:
            try:
                ok = result.get("ok", False) if isinstance(result, dict) else False
                await self.db.log_command(
                    endpoint=cmd,
                    payload=payload,
                    tenant_id=tenant_id,
                    response_status=200 if ok else 400,
                    response_body=result,
                )
            except Exception as e:
                self.logger.warning(f"Failed to log command {cmd}: {e}")

        return result

    async def _execute_command(self, cmd: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Internal command dispatcher."""
        match cmd:
            case "run now":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if tenant_id:
                    # Tenant-specific: reset last_sync to force immediate call
                    self._last_sync[tenant_id] = 0
                    self._run_now_tenant_id = tenant_id
                # Global wake-up - processes all pending messages
                self._wake_event.set()  # Wake SMTP dispatch loop (process messages)
                self._wake_client_event.set()  # Wake client report loop
                return {"ok": True}
            case "suspend":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                batch_code = payload.get("batch_code") if isinstance(payload, dict) else None
                success = await self.db.tenants.suspend_batch(tenant_id, batch_code)
                if not success:
                    return {"ok": False, "error": "tenant not found"}
                suspended = await self.db.tenants.get_suspended_batches(tenant_id)
                pending = await self.db.count_pending_messages(tenant_id, batch_code)
                return {
                    "ok": True,
                    "tenant_id": tenant_id,
                    "batch_code": batch_code,
                    "suspended_batches": list(suspended),
                    "pending_messages": pending,
                }
            case "activate":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                batch_code = payload.get("batch_code") if isinstance(payload, dict) else None
                success = await self.db.tenants.activate_batch(tenant_id, batch_code)
                if not success:
                    return {"ok": False, "error": "tenant not found or cannot activate single batch from full suspension"}
                suspended = await self.db.tenants.get_suspended_batches(tenant_id)
                pending = await self.db.count_pending_messages(tenant_id, batch_code)
                return {
                    "ok": True,
                    "tenant_id": tenant_id,
                    "batch_code": batch_code,
                    "suspended_batches": list(suspended),
                    "pending_messages": pending,
                }
            case "addAccount":
                await self.db.add_account(payload)
                return {"ok": True}
            case "listAccounts":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                accounts = await self.db.list_accounts(tenant_id=tenant_id)
                return {"ok": True, "accounts": accounts}
            case "deleteAccount":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                account_id = payload.get("id")
                # Verify account belongs to tenant before deletion
                try:
                    await self.db.get_account(tenant_id, account_id)
                except ValueError:
                    return {"ok": False, "error": "account not found or not owned by tenant"}
                await self.db.delete_account(tenant_id, account_id)
                await self._refresh_queue_gauge()
                return {"ok": True}
            case "deleteMessages":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                ids = payload.get("ids") if isinstance(payload, dict) else []
                removed, not_found, unauthorized = await self._delete_messages(ids or [], tenant_id)
                await self._refresh_queue_gauge()
                return {"ok": True, "removed": removed, "not_found": not_found, "unauthorized": unauthorized}
            case "listMessages":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                active_only = bool(payload.get("active_only", False)) if isinstance(payload, dict) else False
                include_history = bool(payload.get("include_history", False)) if isinstance(payload, dict) else False
                messages = await self.db.list_messages(
                    tenant_id,
                    active_only=active_only,
                    include_history=include_history,
                )
                return {"ok": True, "messages": messages}
            case "addMessages":
                return await self._handle_add_messages(payload)
            case "cleanupMessages":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                older_than = payload.get("older_than_seconds") if isinstance(payload, dict) else None
                removed = await self._cleanup_reported_messages(older_than, tenant_id)
                return {"ok": True, "removed": removed}
            case "addTenant":
                api_key = await self.db.table('tenants').add(payload)
                result: dict[str, Any] = {"ok": True}
                if api_key:
                    result["api_key"] = api_key
                return result
            case "getTenant":
                tenant_id = payload.get("id")
                tenant = await self.db.table('tenants').get(tenant_id)
                if tenant:
                    return {"ok": True, **tenant}
                return {"ok": False, "error": "tenant not found"}
            case "listTenants":
                active_only = bool(payload.get("active_only", False)) if isinstance(payload, dict) else False
                tenants = await self.db.table('tenants').list_all(active_only=active_only)
                return {"ok": True, "tenants": tenants}
            case "listTenantsSyncStatus":
                tenants = await self.db.table('tenants').list_all()
                now = time.time()
                result_tenants = []
                for tenant in tenants:
                    tenant_id = tenant.get("id")
                    last_sync_ts = self._last_sync.get(tenant_id)
                    # Determine if sync is due or if tenant is in DND
                    next_sync_due = False
                    in_dnd = False
                    if last_sync_ts is not None:
                        if last_sync_ts > now:
                            # Future timestamp means DND mode
                            in_dnd = True
                        elif (now - last_sync_ts) >= DEFAULT_SYNC_INTERVAL:
                            next_sync_due = True
                    else:
                        # Never synced - due now
                        next_sync_due = True
                    result_tenants.append({
                        "id": tenant_id,
                        "name": tenant.get("name"),
                        "active": tenant.get("active", True),
                        "client_base_url": tenant.get("client_base_url"),
                        "last_sync_ts": last_sync_ts,
                        "next_sync_due": next_sync_due,
                        "in_dnd": in_dnd,
                    })
                return {
                    "ok": True,
                    "tenants": result_tenants,
                    "sync_interval_seconds": DEFAULT_SYNC_INTERVAL,
                }
            case "updateTenant":
                tenant_id = payload.pop("id", None)
                if not tenant_id:
                    return {"ok": False, "error": "tenant id required"}
                updated = await self.db.table('tenants').update_fields(tenant_id, payload)
                if updated:
                    return {"ok": True}
                return {"ok": False, "error": "tenant not found"}
            case "deleteTenant":
                tenant_id = payload.get("id")
                deleted = await self.db.table('tenants').remove(tenant_id)
                if deleted:
                    return {"ok": True}
                return {"ok": False, "error": "tenant not found"}
            case "getInstance":
                instance = await self.db.instance.get_instance()
                if instance:
                    return {"ok": True, **instance}
                return {"ok": False, "error": "instance not configured"}
            case "updateInstance":
                await self.db.instance.update_instance(payload)
                return {"ok": True}
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
                        "tenant_id": item.get("tenant_id"),
                        "account_id": item.get("account_id"),
                        "priority": priority,
                        "payload": item,
                        "deferred_ts": None,
                        "batch_code": item.get("batch_code"),
                    }
                    inserted_items = await self.db.insert_messages([entry])
                    if inserted_items:
                        pk = inserted_items[0]["pk"]
                        await self.db.mark_error(pk, now_ts, reason or "validation error")
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
        inserted: list[dict[str, str]] = []

        if validated:
            entries = [
                {
                    "id": msg["id"],
                    "tenant_id": msg["tenant_id"],  # Required for multi-tenant isolation
                    "account_id": msg.get("account_id"),
                    "priority": int(msg["priority"]),
                    "payload": msg,
                    "deferred_ts": msg.get("deferred_ts"),
                    "batch_code": msg.get("batch_code"),
                }
                for msg in validated
            ]
            inserted = await self.db.insert_messages(entries)
            # Messages not inserted were already sent (sent_ts IS NOT NULL)
            inserted_ids = {item["id"] for item in inserted}
            for msg in validated:
                if msg["id"] not in inserted_ids:
                    rejected.append({"id": msg["id"], "reason": "already sent"})

        await self._refresh_queue_gauge()

        # Notify client via proxy_sync for rejected messages
        if rejected_for_sync:
            for event in rejected_for_sync:
                await self._publish_result(event)

        queued_count = len(inserted)
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

    async def _delete_messages(
        self, message_ids: Iterable[str], tenant_id: str
    ) -> tuple[int, list[str], list[str]]:
        """Remove messages from the queue by their IDs, with tenant validation.

        Args:
            message_ids: Iterable of message IDs to delete.
            tenant_id: Tenant ID - only messages belonging to this tenant will be deleted.

        Returns:
            Tuple of (count of removed messages, list of IDs not found, list of unauthorized IDs).
        """
        ids = {mid for mid in message_ids if mid}
        if not ids:
            return 0, [], []

        # Get messages that belong to this tenant (via account relationship)
        authorized_ids = await self.db.messages.get_ids_for_tenant(list(ids), tenant_id)

        removed = 0
        missing: list[str] = []
        unauthorized: list[str] = []

        for mid in sorted(ids):
            if mid not in authorized_ids:
                unauthorized.append(mid)
                continue
            if await self.db.delete_message(mid, tenant_id):
                removed += 1
            else:
                missing.append(mid)
        return removed, missing, unauthorized

    async def _cleanup_reported_messages(
        self, older_than_seconds: int | None = None, tenant_id: str | None = None
    ) -> int:
        """Remove reported messages older than the specified threshold.

        Args:
            older_than_seconds: Remove messages reported more than this many seconds ago.
                              If None, uses the configured retention period.
            tenant_id: If provided, only cleanup messages belonging to this tenant.

        Returns:
            Number of messages removed.
        """
        if older_than_seconds is None:
            retention = self._report_retention_seconds
        else:
            retention = max(0, int(older_than_seconds))

        threshold = self._utc_now_epoch() - retention

        if tenant_id:
            removed = await self.db.remove_fully_reported_before_for_tenant(
                threshold, tenant_id
            )
        else:
            removed = await self.db.remove_fully_reported_before(threshold)

        if removed:
            await self._refresh_queue_gauge()
        return removed

    async def _validate_enqueue_payload(self, payload: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate a message payload before enqueueing.

        Checks for required fields (id, tenant_id, account_id, from, to, subject)
        and verifies that the specified SMTP account exists for the tenant.

        Args:
            payload: Message payload dict to validate.

        Returns:
            Tuple of (is_valid, error_reason). error_reason is None if valid.
        """
        msg_id = payload.get("id")
        if not msg_id:
            return False, "missing id"
        tenant_id = payload.get("tenant_id")
        if not tenant_id:
            return False, "missing tenant_id"
        account_id = payload.get("account_id")
        if not account_id:
            return False, "missing account_id"
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
        subject = payload.get("subject")
        if not subject:
            return False, "missing subject"
        # Verify account exists and belongs to tenant
        try:
            await self.db.get_account(tenant_id, account_id)
        except Exception:
            return False, "account not found for tenant"
        return True, None

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Start the background scheduler and maintenance tasks.

        Initializes the persistence layer and spawns background tasks for:
        - SMTP dispatch loop: processes queued messages
        - Client report loop: sends delivery reports to upstream services
        - Cleanup loop: maintains SMTP connection pool health (production only)
        """
        self.logger.debug("Starting MailProxy...")
        await self.init()
        self._stop.clear()
        self.logger.debug("Creating SMTP dispatch loop task...")
        self._task_smtp = asyncio.create_task(self._smtp_dispatch_loop(), name="smtp-dispatch-loop")
        self.logger.debug("Creating client report loop task...")
        self._task_client = asyncio.create_task(self._client_report_loop(), name="client-report-loop")
        if not self._test_mode:
            self.logger.debug("Creating cleanup loop task...")
            self._task_cleanup = asyncio.create_task(self._cleanup_loop(), name="smtp-cleanup-loop")
        # Start bounce receiver if configured
        await self._start_bounce_receiver()
        self.logger.debug("All background tasks created")

    async def stop(self) -> None:
        """Stop all background tasks gracefully.

        Signals all running loops to terminate and waits for them to complete.
        Outstanding operations are allowed to finish before returning.
        """
        self._stop.set()
        self._wake_event.set()
        self._wake_client_event.set()
        self._wake_cleanup_event.set()
        await asyncio.gather(
            *(task for task in [self._task_smtp, self._task_client, self._task_cleanup] if task),
            return_exceptions=True,
        )
        # Stop bounce receiver if running
        await self._stop_bounce_receiver()
        await self.db.adapter.close()

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

    async def _refresh_queue_gauge(self) -> None:
        """Update the Prometheus gauge for pending message count.

        Queries the database for active (unsent, unreported) messages
        and updates the metrics collector.
        """
        try:
            count = await self.db.count_active_messages()
        except Exception:  # pragma: no cover - defensive
            self.logger.exception("Failed to refresh queue gauge")
            return
        self.metrics.set_pending(count)

    async def _init_account_metrics(self) -> None:
        """Initialize Prometheus counters for all existing accounts.

        Prometheus counters with labels only appear in output after they have
        been incremented at least once. This method ensures metrics appear in
        /metrics output even before any email activity by initializing all
        counters for each configured SMTP account.

        Always initializes at least the "default" account to ensure basic
        metrics are visible even when no accounts are configured.
        """
        try:
            # Always initialize "default" account for basic metrics visibility
            self.metrics.init_account()  # Uses defaults for all labels
            # Also initialize pending gauge to 0
            self.metrics.set_pending(0)

            # Get all tenants to map tenant_id -> tenant_name
            tenants = await self.db.table('tenants').list_all()
            tenant_names = {t["id"]: t.get("name", t["id"]) for t in tenants}

            accounts = await self.db.list_accounts()
            for account in accounts:
                tenant_id = account.get("tenant_id", "default")
                account_id = account.get("id", "default")
                self.metrics.init_account(
                    tenant_id=tenant_id,
                    tenant_name=tenant_names.get(tenant_id, tenant_id),
                    account_id=account_id,
                    account_name=account_id,  # No separate name field for accounts
                )
            self.logger.debug("Initialized metrics for %d accounts", len(accounts) + 1)
        except Exception:  # pragma: no cover - defensive
            self.logger.exception("Failed to initialize account metrics")
