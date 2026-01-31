# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""MailProxy: runtime layer with SMTP sending, background loops, and metrics.

MailProxy extends MailProxyBase with the full runtime for email dispatch:

Components:
    - SmtpSender: Connection pool, rate limiting, dispatch loop
    - ClientReporter: Delivery report sync to upstream services
    - AttachmentManager: Fetch attachments with two-tier cache
    - Prometheus MailMetrics: Counters and gauges for monitoring

Class Hierarchy:
    MailProxyBase (proxy_base.py): config, db, tables, endpoints, api/cli
        └── MailProxy (this class): +SmtpSender, +ClientReporter, +metrics
            └── MailProxy with MailProxy_EE mixin: +bounce detection (EE)

EE Hook Methods (CE stubs, overridden by MailProxy_EE):
    - __init_proxy_ee__(): Initialize EE state (bounce_receiver, _bounce_config)
    - _start_proxy_ee(): Start bounce poller background task
    - _stop_proxy_ee(): Stop bounce poller

Command Handling:
    handle_command() is the entry point for external control. Commands are
    routed to local handlers (runtime-dependent) or EndpointDispatcher (CRUD).

    Local commands (require runtime state):
        run now, listTenantsSyncStatus, addMessages, deleteMessages,
        cleanupMessages, deleteAccount

    Delegated commands (via EndpointDispatcher):
        addTenant, getTenant, listTenants, updateTenant, deleteTenant,
        addAccount, listAccounts, getAccount, ...

Background Tasks:
    - SmtpSender.dispatch_loop: Fetch pending messages, send via SMTP
    - ClientReporter.sync_loop: Report delivery events to upstream

Usage (recommended factory):
    proxy = await MailProxy.create(db_path="/data/mail.db", start_active=True)
    await proxy.stop()

Usage (via API property):
    proxy = MailProxy(config=ProxyConfig(db_path="/data/mail.db"))
    app = proxy.api  # Lifespan calls start()/stop() automatically

Module Constants:
    PRIORITY_LABELS: {0: "immediate", 1: "high", 2: "medium", 3: "low"}
    LABEL_TO_PRIORITY: Reverse mapping
    DEFAULT_PRIORITY: 2 (medium)
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from tools.prometheus import MailMetrics

from .interface import EndpointDispatcher
from .proxy_base import MailProxyBase
from .proxy_config import ProxyConfig
from .reporting import DEFAULT_SYNC_INTERVAL, ClientReporter
from .smtp import (
    AttachmentManager,
    SmtpSender,
    TieredCache,
)
from .smtp.retry import RetryStrategy

PRIORITY_LABELS = {
    0: "immediate",
    1: "high",
    2: "medium",
    3: "low",
}
LABEL_TO_PRIORITY = {label: value for value, label in PRIORITY_LABELS.items()}
DEFAULT_PRIORITY = 2


class MailProxy(MailProxyBase):
    """Runtime layer: SMTP sending, background loops, metrics, command handling.

    Extends MailProxyBase with:
    - SmtpSender: SMTP connection pool, rate limiter, dispatch loop
    - ClientReporter: Delivery report sync loop
    - AttachmentManager: Fetch attachments with caching
    - MailMetrics: Prometheus counters and gauges

    Class Attributes:
        is_enterprise: True when EE modules installed (set dynamically)

    Instance Attributes (from base):
        config: ProxyConfig instance
        db: SqlDb with autodiscovered tables
        endpoints: Dict of endpoint instances

    Instance Attributes (runtime):
        smtp_sender: SmtpSender instance (pool, rate_limiter, dispatch)
        client_reporter: ClientReporter instance (sync loop)
        attachments: AttachmentManager instance
        metrics: MailMetrics instance
        logger: Logger for diagnostics

    Compatibility Properties (delegate to smtp_sender):
        pool: SMTP connection pool
        rate_limiter: Per-account rate limiter
    """

    # -------------------------------------------------------------------------
    # Class attributes
    # -------------------------------------------------------------------------

    is_enterprise: bool = False
    """True when EE modules installed. Set dynamically in mail_proxy/__init__.py."""

    # -------------------------------------------------------------------------
    # Compatibility properties (delegate to smtp_sender)
    # -------------------------------------------------------------------------

    @property
    def pool(self):
        """SMTP connection pool (delegate to smtp_sender.pool)."""
        return self.smtp_sender.pool

    @property
    def rate_limiter(self):
        """Per-account rate limiter (delegate to smtp_sender.rate_limiter)."""
        return self.smtp_sender.rate_limiter

    # -------------------------------------------------------------------------
    # EE hook methods (CE stubs, overridden by MailProxy_EE mixin)
    # -------------------------------------------------------------------------

    def __init_proxy_ee__(self) -> None:
        """Initialize EE state (bounce_receiver, _bounce_config). CE stub."""
        pass

    async def _start_proxy_ee(self) -> None:
        """Start EE background tasks (bounce poller). CE stub."""
        pass

    async def _stop_proxy_ee(self) -> None:
        """Stop EE background tasks. CE stub."""
        pass

    def __init__(
        self,
        config: ProxyConfig | None = None,
        *,
        logger: logging.Logger | None = None,
        metrics: MailMetrics | None = None,
    ):
        """Initialize the mail dispatcher with ProxyConfig.

        Args:
            config: ProxyConfig instance with all configuration.
                If None, creates default config.
            logger: Custom logger instance. If None, uses default logger.
            metrics: Prometheus metrics collector. If None, creates new instance.
        """
        import math

        cfg = config or ProxyConfig()

        # Initialize base class (config, db with autodiscovered tables, endpoints)
        MailProxyBase.__init__(self, config=cfg)

        self.default_host: str | None = None
        self.default_port: int | None = None
        self.default_user: str | None = None
        self.default_password: str | None = None
        self.default_use_tls: bool | None = False

        self.logger = logger or logging.getLogger("AsyncMailService")
        self.metrics = metrics or MailMetrics()

        # SmtpSender manages pool, rate_limiter, dispatch loop, email building
        self.smtp_sender = SmtpSender(self)
        self._queue_put_timeout = cfg.queue.put_timeout
        self._max_enqueue_batch = cfg.queue.max_enqueue_batch
        self._attachment_timeout = cfg.timing.attachment_timeout
        base_send_interval = max(0.05, float(cfg.timing.send_loop_interval))
        self._smtp_batch_size = max(1, int(cfg.queue.message_size))
        self._report_retention_seconds = cfg.timing.report_retention_seconds
        self._test_mode = bool(cfg.test_mode)

        self._stop = asyncio.Event()
        self._active = cfg.start_active

        self._send_loop_interval = math.inf if self._test_mode else base_send_interval
        self._result_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(
            maxsize=cfg.queue.result_size
        )

        # ClientReporter manages delivery report sync loop
        self.client_reporter = ClientReporter(self)

        self._client_sync_url = cfg.client_sync.url
        self._client_sync_user = cfg.client_sync.user
        self._client_sync_password = cfg.client_sync.password
        self._client_sync_token = cfg.client_sync.token
        self._report_delivery_callable = cfg.report_delivery_callable

        # Attachments and cache will be initialized in init()
        self._attachment_cache: TieredCache | None = None
        self.attachments: AttachmentManager | None = None
        priority_value, _ = self._normalise_priority(cfg.default_priority, DEFAULT_PRIORITY)
        self._default_priority = priority_value
        self._log_delivery_activity = bool(cfg.log_delivery_activity)

        self._retry_strategy = RetryStrategy(
            max_retries=cfg.retry.max_retries,
            delays=cfg.retry.delays,
        )

        self._batch_size_per_account = 50
        self._max_concurrent_sends = max(1, int(cfg.concurrency.max_sends))
        self._max_concurrent_per_account = max(1, int(cfg.concurrency.max_per_account))
        self._max_concurrent_attachments = max(1, int(cfg.concurrency.max_attachments))
        self._attachment_semaphore: asyncio.Semaphore | None = None

        # Initialize endpoint dispatcher for command routing
        self._dispatcher = EndpointDispatcher(self.db, proxy=self)

        # Initialize EE components (overridden in MailProxy_EE mixin)
        self.__init_proxy_ee__()

    @classmethod
    async def create(cls, **kwargs) -> MailProxy:
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

    # -------------------------------------------------------------------------
    # Utility methods (static)
    # -------------------------------------------------------------------------

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
        1. Initialize database schema and run migrations (via MailProxyBase.init)
        2. Load cache configuration (from env vars or config file)
        3. Initialize attachment cache (memory and disk tiers)
        4. Create the AttachmentManager
        """
        await MailProxyBase.init(self)
        await self._refresh_queue_gauge()

        # Initialize metrics for all existing accounts so they appear in /metrics
        # even before any email activity occurs
        await self._init_account_metrics()

        # Initialize attachment cache if configured
        cache_cfg = self.config.cache
        if cache_cfg.enabled:
            self._attachment_cache = TieredCache(
                memory_max_mb=cache_cfg.memory_max_mb,
                memory_ttl_seconds=cache_cfg.memory_ttl_seconds,
                disk_dir=cache_cfg.disk_dir,
                disk_max_mb=cache_cfg.disk_max_mb,
                disk_ttl_seconds=cache_cfg.disk_ttl_seconds,
                disk_threshold_kb=cache_cfg.disk_threshold_kb,
            )
            await self._attachment_cache.init()
            self.logger.info(
                f"Attachment cache initialized (memory={cache_cfg.memory_max_mb}MB, "
                f"disk={cache_cfg.disk_dir})"
            )

        # Initialize attachment manager (tenant-specific config applied per-message)
        # storage_manager=None means only absolute paths work; tenant-specific managers are
        # created in _get_attachment_manager_for_message() with tenant's storage config
        self.attachments = AttachmentManager(storage_manager=None, cache=self._attachment_cache)

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
    _LOGGED_COMMANDS = frozenset(
        {
            "addMessages",
            "deleteMessages",
            "cleanupMessages",
            "addAccount",
            "deleteAccount",
            "addTenant",
            "updateTenant",
            "deleteTenant",
            "suspend",
            "activate",
        }
    )

    # -------------------------------------------------------------------------
    # Command handling (public API)
    # -------------------------------------------------------------------------
    async def handle_command(
        self, cmd: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
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
                await self.db.table("command_log").log_command(
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
        """Internal command dispatcher.

        Commands requiring proxy runtime state are handled here directly.
        Other commands are delegated to the EndpointDispatcher.
        """
        # Commands requiring proxy runtime state
        match cmd:
            case "run now":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                self.smtp_sender.wake()
                self.client_reporter.wake(tenant_id)
                return {"ok": True}

            case "listTenantsSyncStatus":
                # Requires client_reporter._last_sync runtime state
                tenants = await self.db.table("tenants").list_all()
                now = time.time()
                result_tenants = []
                for tenant in tenants:
                    tenant_id = tenant.get("id")
                    last_sync_ts = self.client_reporter._last_sync.get(tenant_id)
                    next_sync_due = False
                    in_dnd = False
                    if last_sync_ts is not None:
                        if last_sync_ts > now:
                            in_dnd = True
                        elif (now - last_sync_ts) >= DEFAULT_SYNC_INTERVAL:
                            next_sync_due = True
                    else:
                        next_sync_due = True
                    result_tenants.append(
                        {
                            "id": tenant_id,
                            "name": tenant.get("name"),
                            "active": tenant.get("active", True),
                            "client_base_url": tenant.get("client_base_url"),
                            "last_sync_ts": last_sync_ts,
                            "next_sync_due": next_sync_due,
                            "in_dnd": in_dnd,
                        }
                    )
                return {
                    "ok": True,
                    "tenants": result_tenants,
                    "sync_interval_seconds": DEFAULT_SYNC_INTERVAL,
                }

            # Commands with proxy-specific side effects (metrics refresh, result publishing)
            case "addMessages":
                return await self._handle_add_messages(payload)

            case "deleteMessages":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                ids = payload.get("ids") if isinstance(payload, dict) else []
                removed, not_found, unauthorized = await self._delete_messages(ids or [], tenant_id)
                await self._refresh_queue_gauge()
                return {
                    "ok": True,
                    "removed": removed,
                    "not_found": not_found,
                    "unauthorized": unauthorized,
                }

            case "cleanupMessages":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                older_than = (
                    payload.get("older_than_seconds") if isinstance(payload, dict) else None
                )
                removed = await self._cleanup_reported_messages(older_than, tenant_id)
                return {"ok": True, "removed": removed}

            case "deleteAccount":
                tenant_id = payload.get("tenant_id") if isinstance(payload, dict) else None
                if not tenant_id:
                    return {"ok": False, "error": "tenant_id is required"}
                account_id = payload.get("id")
                try:
                    await self.db.table("accounts").get(tenant_id, account_id)
                except ValueError:
                    return {"ok": False, "error": "account not found or not owned by tenant"}
                await self.db.table("accounts").remove(tenant_id, account_id)
                await self._refresh_queue_gauge()
                return {"ok": True}

            case _:
                # Delegate to EndpointDispatcher for standard CRUD commands
                return await self._dispatcher.dispatch(cmd, payload)

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
            return {
                "ok": False,
                "error": f"Cannot enqueue more than {self._max_enqueue_batch} messages at once",
            }

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
                    priority, _ = self._normalise_priority(
                        item.get("priority"), default_priority_value
                    )
                    entry = {
                        "id": msg_id,
                        "tenant_id": item.get("tenant_id"),
                        "account_id": item.get("account_id"),
                        "priority": priority,
                        "payload": item,
                        "deferred_ts": None,
                        "batch_code": item.get("batch_code"),
                    }
                    inserted_items = await self.db.table("messages").insert_batch([entry])
                    if inserted_items:
                        pk = inserted_items[0]["pk"]
                        await self.db.table("message_events").add_event(
                            pk, "error", now_ts, description=reason or "validation error"
                        )
                    rejected_for_sync.append(
                        {
                            "id": msg_id,
                            "status": "error",
                            "error": reason,
                            "timestamp": self._utc_now_iso(),
                            "account": item.get("account_id"),
                        }
                    )
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
            inserted = await self.db.table("messages").insert_batch(entries)
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
        authorized_ids = await self.db.table("messages").get_ids_for_tenant(list(ids), tenant_id)

        removed = 0
        missing: list[str] = []
        unauthorized: list[str] = []

        for mid in sorted(ids):
            if mid not in authorized_ids:
                unauthorized.append(mid)
                continue
            if await self.db.table("messages").delete(mid, tenant_id):
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
            removed = await self.db.table("messages").remove_fully_reported_before_for_tenant(
                threshold, tenant_id
            )
        else:
            removed = await self.db.table("messages").remove_fully_reported_before(threshold)

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
            await self.db.table("accounts").get(tenant_id, account_id)
        except Exception:
            return False, "account not found for tenant"
        return True, None

    # -------------------------------------------------------------------------
    # Lifecycle (start/stop)
    # -------------------------------------------------------------------------

    async def start(self) -> None:
        """Start the background scheduler and maintenance tasks.

        Initializes the persistence layer and spawns background tasks for:
        - SMTP dispatch via smtp_sender
        - Client report loop: sends delivery reports to upstream services
        """
        self.logger.debug("Starting MailProxy...")
        await self.init()
        self._stop.clear()
        self.logger.debug("Starting SMTP sender...")
        await self.smtp_sender.start()
        self.logger.debug("Starting client reporter...")
        await self.client_reporter.start()
        # Start EE components (overridden in MailProxy_EE mixin)
        await self._start_proxy_ee()
        self.logger.debug("All background tasks created")

    async def stop(self) -> None:
        """Stop all background tasks gracefully.

        Signals all running loops to terminate and waits for them to complete.
        Outstanding operations are allowed to finish before returning.
        """
        self._stop.set()
        await self.smtp_sender.stop()
        await self.client_reporter.stop()
        # Stop EE components (overridden in MailProxy_EE mixin)
        await self._stop_proxy_ee()
        await self.db.adapter.close()

    # -------------------------------------------------------------------------
    # Messaging and metrics (internal)
    # -------------------------------------------------------------------------

    async def results(self):
        """Async generator that yields delivery result events.

        Yields:
            dict: Delivery event with message ID, status, timestamp, and error info.
        """
        while True:
            event = await self._result_queue.get()
            yield event

    async def _put_with_backpressure(
        self, queue: asyncio.Queue[Any], item: Any, queue_name: str
    ) -> None:
        """Push an item to a queue with timeout-based backpressure.

        Args:
            queue: Target asyncio.Queue.
            item: Item to enqueue.
            queue_name: Name for logging purposes.
        """
        try:
            await asyncio.wait_for(queue.put(item), timeout=self._queue_put_timeout)
        except asyncio.TimeoutError:  # pragma: no cover - defensive
            self.logger.error(
                "Timed out while enqueuing item into %s queue; dropping item", queue_name
            )

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
                self.logger.info(
                    "Delivery event for message %s (account=%s): %s", msg_id, account, status
                )

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
            count = await self.db.table("messages").count_active()
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
            tenants = await self.db.table("tenants").list_all()
            tenant_names = {t["id"]: t.get("name", t["id"]) for t in tenants}

            accounts = await self.db.table("accounts").list_all()
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
