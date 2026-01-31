# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Client Reporter - delivery report synchronization with upstream clients.

This module provides ClientReporter, the component responsible for:
- Fetching unreported delivery events from message_events table
- Sending delivery reports to tenant-specific or global sync URLs
- Handling "do not disturb" schedules via next_sync_after
- Managing report retention cleanup

ClientReporter is instantiated by MailProxy and accessed via proxy.client_reporter.

Example:
    # ClientReporter is created by MailProxy
    proxy = MailProxy(db_path="mail.db")
    await proxy.start()  # Starts client_reporter automatically

    # Direct access if needed
    proxy.client_reporter.wake()  # Trigger immediate report cycle
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import aiohttp

from ..entities.tenant import get_tenant_sync_url

DEFAULT_SYNC_INTERVAL = 300  # 5 minutes

if TYPE_CHECKING:
    from ..proxy import MailProxy


class ClientReporter:
    """Delivery report synchronization with upstream clients.

    Manages the background loop that:
    - Fetches unreported events from message_events table
    - Groups events by tenant and sends to appropriate sync URLs
    - Handles authentication (bearer token, basic auth)
    - Respects "do not disturb" schedules from client responses
    - Cleans up old reported messages based on retention policy

    Attributes:
        proxy: Parent MailProxy instance for accessing db, config, etc.
    """

    def __init__(self, proxy: MailProxy) -> None:
        """Initialize ClientReporter.

        Args:
            proxy: Parent MailProxy instance.
        """
        self.proxy = proxy

        # Background task handle
        self._task: asyncio.Task | None = None

        # Control events
        self._stop = asyncio.Event()
        self._wake_event = asyncio.Event()

        # Sync state per tenant
        self._last_sync: dict[str, float] = {}
        self._run_now_tenant_id: str | None = None

    # ----------------------------------------------------------------- properties
    @property
    def db(self):
        """Database access via proxy."""
        return self.proxy.db

    @property
    def logger(self):
        """Logger via proxy."""
        return self.proxy.logger

    @property
    def metrics(self):
        """Prometheus metrics via proxy."""
        return self.proxy.metrics

    @property
    def _test_mode(self) -> bool:
        """Test mode flag via proxy."""
        return self.proxy._test_mode

    @property
    def _active(self) -> bool:
        """Active flag via proxy."""
        return self.proxy._active

    @property
    def _smtp_batch_size(self) -> int:
        """Batch size via proxy."""
        return self.proxy._smtp_batch_size

    @property
    def _report_retention_seconds(self) -> int:
        """Report retention via proxy."""
        return self.proxy._report_retention_seconds

    @property
    def _client_sync_url(self) -> str | None:
        """Global sync URL via proxy."""
        return self.proxy._client_sync_url

    @property
    def _client_sync_token(self) -> str | None:
        """Global sync token via proxy."""
        return self.proxy._client_sync_token

    @property
    def _client_sync_user(self) -> str | None:
        """Global sync user via proxy."""
        return self.proxy._client_sync_user

    @property
    def _client_sync_password(self) -> str | None:
        """Global sync password via proxy."""
        return self.proxy._client_sync_password

    @property
    def _report_delivery_callable(self):
        """Report delivery callable via proxy."""
        return self.proxy._report_delivery_callable

    @property
    def _log_delivery_activity(self) -> bool:
        """Log delivery activity flag via proxy."""
        return self.proxy._log_delivery_activity

    # ----------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        """Start the background report loop."""
        self._stop.clear()
        self.logger.debug("Starting ClientReporter loop...")
        self._task = asyncio.create_task(self._report_loop(), name="client-report-loop")

    async def stop(self) -> None:
        """Stop the background report loop gracefully."""
        self._stop.set()
        self._wake_event.set()
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    def wake(self, tenant_id: str | None = None) -> None:
        """Wake the report loop for immediate processing.

        Args:
            tenant_id: If provided, only sync this tenant. Otherwise sync all.
        """
        if tenant_id:
            self._last_sync[tenant_id] = 0
            self._run_now_tenant_id = tenant_id
        self._wake_event.set()

    # ----------------------------------------------------------------- report loop
    async def _report_loop(self) -> None:
        """Background loop that pushes delivery reports.

        Optimization: When client returns queued > 0, loops immediately to fetch
        more messages. Uses a 5-minute fallback timeout otherwise.
        """
        first_iteration = True
        fallback_interval = 300  # 5 minutes fallback
        while not self._stop.is_set():
            if first_iteration and self._test_mode:
                await self._wait_for_wakeup(math.inf)
            first_iteration = False

            try:
                queued = await self._process_cycle()

                # If client has queued messages, sync again immediately
                if queued and queued > 0:
                    self.logger.debug("Client has %d queued messages, syncing immediately", queued)
                    continue  # Loop immediately without waiting

            except Exception as exc:  # pragma: no cover - defensive
                self.logger.exception("Unhandled error in client report loop: %s", exc)

            # No queued messages - wait for wake event or fallback interval
            interval = math.inf if self._test_mode else fallback_interval
            await self._wait_for_wakeup(interval)

    async def _process_cycle(self) -> int:
        """Process one delivery report cycle.

        Logic:
        1. Fetch unreported events and group by tenant
        2. Call tenants WITH events (always)
        3. Call tenants WITHOUT events if sync interval exceeded
        4. Parse next_sync_after from responses for "do not disturb"

        Returns:
            Total number of messages queued by all clients.
        """
        if not self._active:
            return 0

        target_tenant_id = self._run_now_tenant_id
        self._run_now_tenant_id = None
        total_queued = 0
        now = time.time()
        sync_interval = DEFAULT_SYNC_INTERVAL

        # Fetch unreported events
        events = await self.db.table("message_events").fetch_unreported(self._smtp_batch_size)

        # Group events by tenant_id
        events_by_tenant: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            tenant_id = event.get("tenant_id")
            events_by_tenant[tenant_id].append(event)

        # Track acknowledged event IDs and called tenants
        acked_event_ids: list[int] = []
        called_tenant_ids: set[str] = set()

        # 1. Call tenants WITH events
        for tenant_id, tenant_events in events_by_tenant.items():
            payloads = self._events_to_payloads(tenant_events)

            if tenant_id is None:
                # Handle global sync URL case
                if self._client_sync_url or self._report_delivery_callable:
                    try:
                        acked, queued, next_sync = await self._send_delivery_reports(payloads)
                        total_queued += queued
                        acked_event_ids.extend(
                            e["event_id"] for e in tenant_events if e.get("message_id") in acked
                        )
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        self.logger.warning("Client sync failed: %s", exc)
                continue

            called_tenant_ids.add(tenant_id)
            tenant = await self.db.table("tenants").get(tenant_id)
            if not tenant:
                continue

            try:
                if get_tenant_sync_url(tenant):
                    acked, queued, next_sync = await self._send_reports_to_tenant(tenant, payloads)
                    total_queued += queued
                    self._last_sync[tenant_id] = next_sync if next_sync else now
                    acked_event_ids.extend(
                        e["event_id"] for e in tenant_events if e.get("message_id") in acked
                    )
                elif self._client_sync_url:
                    acked, queued, next_sync = await self._send_delivery_reports(payloads)
                    total_queued += queued
                    self._last_sync[tenant_id] = next_sync if next_sync else now
                    acked_event_ids.extend(
                        e["event_id"] for e in tenant_events if e.get("message_id") in acked
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                self.logger.warning("Client sync failed for tenant %s: %s", tenant_id, exc)

        # 2. Call tenants WITHOUT events if sync interval exceeded
        tenants = await self.db.table("tenants").list_all()
        for tenant in tenants:
            tenant_id = tenant.get("id")
            if not tenant_id or not tenant.get("active"):
                continue
            if tenant_id in called_tenant_ids:
                continue  # Already called above

            # Check if target_tenant_id filter applies
            if target_tenant_id and tenant_id != target_tenant_id:
                continue

            # Check sync interval (also handles DND with future timestamp)
            last = self._last_sync.get(tenant_id, 0)
            if (now - last) < sync_interval:
                continue  # Not time yet

            sync_url = get_tenant_sync_url(tenant)
            if not sync_url:
                continue

            try:
                _, queued, next_sync = await self._send_reports_to_tenant(tenant, [])
                total_queued += queued
                self._last_sync[tenant_id] = next_sync if next_sync else now
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                self.logger.warning("Client sync for tenant %s not reachable: %s", tenant_id, exc)

        # Mark acknowledged events as reported
        if acked_event_ids:
            reported_ts = self._utc_now_epoch()
            await self.db.table("message_events").mark_reported(acked_event_ids, reported_ts)

        await self._apply_retention()
        return total_queued

    def _events_to_payloads(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert event records to delivery report payloads.

        The payload format matches what clients expect.
        """
        payloads: list[dict[str, Any]] = []

        for event in events:
            event_type = event.get("event_type")
            msg_id = event.get("message_id")
            event_ts = event.get("event_ts")
            description = event.get("description")
            metadata = event.get("metadata") or {}

            payload: dict[str, Any] = {"id": msg_id}

            if event_type == "sent":
                payload["sent_ts"] = event_ts
            elif event_type == "error":
                payload["error_ts"] = event_ts
                payload["error"] = description
            elif event_type == "deferred":
                payload["deferred_ts"] = event_ts
                payload["deferred_reason"] = description
            elif event_type == "bounce":
                payload["bounce_ts"] = event_ts
                payload["bounce_type"] = metadata.get("bounce_type")
                payload["bounce_code"] = metadata.get("bounce_code")
                payload["bounce_reason"] = description
            elif event_type.startswith("pec_"):
                # PEC events: pec_acceptance, pec_delivery, pec_error
                payload["pec_event"] = event_type
                payload["pec_ts"] = event_ts
                if description:
                    payload["pec_details"] = description

            payloads.append(payload)

        return payloads

    async def _apply_retention(self) -> None:
        """Remove messages with all events reported older than retention period."""
        if self._report_retention_seconds <= 0:
            return
        threshold = self._utc_now_epoch() - self._report_retention_seconds
        removed = await self.db.table("messages").remove_fully_reported_before(threshold)
        if removed:
            await self.proxy._refresh_queue_gauge()

    async def _wait_for_wakeup(self, timeout: float | None) -> None:
        """Pause the report loop until timeout or wake event."""
        if self._stop.is_set():
            return
        if timeout is None:
            await self._wake_event.wait()
            self._wake_event.clear()
            return
        timeout = float(timeout)
        if math.isinf(timeout):
            await self._wake_event.wait()
            self._wake_event.clear()
            return
        timeout = max(0.0, timeout)
        if timeout == 0:
            await asyncio.sleep(0)
            return
        try:
            await asyncio.wait_for(self._wake_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return
        self._wake_event.clear()

    # ----------------------------------------------------------------- HTTP sending
    async def _send_delivery_reports(
        self, payloads: list[dict[str, Any]]
    ) -> tuple[list[str], int, float | None]:
        """Send delivery report payloads to the configured proxy or callback.

        Returns:
            Tuple of (message IDs processed, queued count, next_sync_after timestamp or None).
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
            return [p["id"] for p in payloads if p.get("id")], 0, None

        if not self._client_sync_url:
            if payloads:
                raise RuntimeError("Client sync URL is not configured")
            return [], 0, None

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

        async with (
            aiohttp.ClientSession() as session,
            session.post(
                self._client_sync_url,
                json={"delivery_report": payloads},
                auth=auth,
                headers=headers or None,
            ) as resp,
        ):
            resp.raise_for_status()
            processed_ids: list[str] = [p["id"] for p in payloads]
            error_ids: list[str] = []
            not_found_ids: list[str] = []
            is_ok = False
            queued_count = 0
            next_sync_after: float | None = None
            try:
                response_data = await resp.json()
                is_ok = response_data.get("ok", False)
                error_ids = response_data.get("error", [])
                not_found_ids = response_data.get("not_found", [])
                queued_count = response_data.get("queued", 0)
                raw_next_sync = response_data.get("next_sync_after")
                if raw_next_sync is not None:
                    next_sync_after = float(raw_next_sync)
            except Exception:
                self.logger.warning("Client sync returned non-JSON response")

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
        return processed_ids, queued_count, next_sync_after

    async def _send_reports_to_tenant(
        self, tenant: dict[str, Any], payloads: list[dict[str, Any]]
    ) -> tuple[list[str], int, float | None]:
        """Send delivery report payloads to a tenant-specific endpoint.

        Args:
            tenant: Tenant configuration dict with client_base_url and client_auth.
            payloads: List of delivery report payloads to send.

        Returns:
            Tuple of (message IDs acknowledged, queued count, next_sync_after timestamp or None).

        Raises:
            aiohttp.ClientError: If the HTTP request fails.
            asyncio.TimeoutError: If the request times out.
        """
        sync_url = get_tenant_sync_url(tenant)
        if not sync_url:
            raise RuntimeError(f"Tenant {tenant.get('id')} has no sync URL configured")

        # Build authentication from tenant config
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

        async with (
            aiohttp.ClientSession() as session,
            session.post(
                sync_url,
                json={"delivery_report": payloads},
                auth=auth,
                headers=headers or None,
            ) as resp,
        ):
            resp.raise_for_status()
            processed_ids: list[str] = [p["id"] for p in payloads]
            error_ids: list[str] = []
            not_found_ids: list[str] = []
            is_ok = False
            queued_count = 0
            next_sync_after: float | None = None
            try:
                response_data = await resp.json()
                is_ok = response_data.get("ok", False)
                error_ids = response_data.get("error", [])
                not_found_ids = response_data.get("not_found", [])
                queued_count = response_data.get("queued", 0)
                raw_next_sync = response_data.get("next_sync_after")
                if raw_next_sync is not None:
                    next_sync_after = float(raw_next_sync)
            except Exception as e:
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
        return processed_ids, queued_count, next_sync_after

    # ----------------------------------------------------------------- utilities
    @staticmethod
    def _utc_now_epoch() -> int:
        """Return the current UTC timestamp as seconds since Unix epoch."""
        return int(datetime.now(timezone.utc).timestamp())


__all__ = ["ClientReporter", "DEFAULT_SYNC_INTERVAL"]
