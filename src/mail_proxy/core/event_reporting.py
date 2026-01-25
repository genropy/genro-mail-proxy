# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Event-based client reporting mixin for MailProxy.

This module provides the EventReporterMixin class that uses the message_events
table for delivery reporting instead of reading status fields from messages.

This is the "new" reporting system that will eventually replace ReporterMixin.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from typing import TYPE_CHECKING, Any

import aiohttp

from ..entities.tenant.schema import get_tenant_sync_url

if TYPE_CHECKING:
    from .proxy import MailProxy


class EventReporterMixin:
    """Mixin providing event-based client report loop.

    Uses message_events table instead of status fields on messages table.
    Each event (sent, error, deferred, bounce, pec_*) is reported individually.
    """

    if TYPE_CHECKING:
        _stop: asyncio.Event
        _wake_client_event: asyncio.Event
        _test_mode: bool
        _active: bool
        _run_now_tenant_id: str | None
        _smtp_batch_size: int
        _report_retention_seconds: int
        _client_sync_url: str | None
        _client_sync_token: str | None
        _client_sync_user: str | None
        _client_sync_password: str | None
        _report_delivery_callable: Any
        _log_delivery_activity: bool

    async def _client_report_loop(self: MailProxy) -> None:
        """Background coroutine that pushes delivery reports from events."""
        first_iteration = True
        fallback_interval = 300
        while not self._stop.is_set():
            if first_iteration and self._test_mode:
                await self._wait_for_client_wakeup(math.inf)
            first_iteration = False

            try:
                queued = await self._process_client_cycle()
                if queued and queued > 0:
                    self.logger.debug(
                        "Client has %d queued messages, syncing immediately", queued
                    )
                    continue
            except Exception as exc:  # pragma: no cover
                self.logger.exception("Unhandled error in client report loop: %s", exc)

            interval = math.inf if self._test_mode else fallback_interval
            await self._wait_for_client_wakeup(interval)

    async def _process_client_cycle(self: MailProxy) -> int:
        """Process one delivery report cycle using events.

        Returns:
            Total number of messages queued by all clients.
        """
        if not self._active:
            return 0

        target_tenant_id = self._run_now_tenant_id
        self._run_now_tenant_id = None
        total_queued = 0

        # Fetch unreported events instead of messages
        events = await self.db.fetch_unreported_events(self._smtp_batch_size)

        if not events:
            # Sync tenants even without events (allows them to send new messages)
            total_queued = await self._sync_tenants_without_reports(target_tenant_id)
            await self._apply_retention()
            return total_queued

        # Group events by tenant_id
        events_by_tenant: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            tenant_id = event.get("tenant_id")
            events_by_tenant[tenant_id].append(event)

        # Track acknowledged event IDs
        acked_event_ids: list[int] = []

        # Send events to each tenant's endpoint
        for tenant_id, tenant_events in events_by_tenant.items():
            # Convert events to delivery report payloads
            payloads = self._events_to_payloads(tenant_events)

            try:
                if tenant_id:
                    tenant = await self.db.get_tenant(tenant_id)
                    if tenant and get_tenant_sync_url(tenant):
                        acked, queued = await self._send_reports_to_tenant(tenant, payloads)
                        total_queued += queued
                        # Map acked message IDs back to event IDs
                        acked_event_ids.extend(
                            e["event_id"] for e in tenant_events
                            if e.get("message_id") in acked
                        )
                    elif self._client_sync_url:
                        acked, queued = await self._send_delivery_reports(payloads)
                        total_queued += queued
                        acked_event_ids.extend(
                            e["event_id"] for e in tenant_events
                            if e.get("message_id") in acked
                        )
                    else:
                        self.logger.warning(
                            "No sync URL for tenant %s, skipping %d events",
                            tenant_id, len(tenant_events)
                        )
                        continue
                else:
                    if self._client_sync_url or self._report_delivery_callable:
                        acked, queued = await self._send_delivery_reports(payloads)
                        total_queued += queued
                        acked_event_ids.extend(
                            e["event_id"] for e in tenant_events
                            if e.get("message_id") in acked
                        )
                    else:
                        self.logger.warning(
                            "No tenant and no global sync URL, skipping %d events",
                            len(tenant_events)
                        )
                        continue
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                target = tenant_id or "global"
                self.logger.warning(
                    "Client sync failed for tenant %s: %s", target, exc
                )

        # Mark acknowledged events as reported
        if acked_event_ids:
            reported_ts = self._utc_now_epoch()
            await self.db.mark_events_reported(acked_event_ids, reported_ts)

        await self._apply_retention()
        return total_queued

    def _events_to_payloads(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert event records to delivery report payloads.

        The payload format matches what clients expect from the old system.
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

    async def _sync_tenants_without_reports(
        self: MailProxy, target_tenant_id: str | None
    ) -> int:
        """Sync tenants even when there are no reports to send."""
        total_queued = 0

        if target_tenant_id:
            tenant = await self.db.get_tenant(target_tenant_id)
            if tenant and tenant.get("active") and get_tenant_sync_url(tenant):
                try:
                    _, queued = await self._send_reports_to_tenant(tenant, [])
                    total_queued += queued
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    self.logger.warning(
                        "Client sync for tenant %s not reachable: %s",
                        target_tenant_id, exc,
                    )
        else:
            tenants = await self.db.list_tenants()
            for tenant in tenants:
                if tenant.get("active") and get_tenant_sync_url(tenant):
                    try:
                        _, queued = await self._send_reports_to_tenant(tenant, [])
                        total_queued += queued
                    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                        self.logger.warning(
                            "Client sync for tenant %s not reachable: %s",
                            tenant.get("id"), exc,
                        )
            if self._client_sync_url and self._report_delivery_callable is None:
                try:
                    _, queued = await self._send_delivery_reports([])
                    total_queued += queued
                except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    self.logger.warning(
                        "Client sync endpoint %s not reachable: %s",
                        self._client_sync_url, exc,
                    )

        return total_queued

    async def _apply_retention(self: MailProxy) -> None:
        """Remove reported messages older than the configured retention period."""
        if self._report_retention_seconds <= 0:
            return
        threshold = self._utc_now_epoch() - self._report_retention_seconds
        removed = await self.db.remove_reported_before(threshold)
        if removed:
            await self._refresh_queue_gauge()

    async def _wait_for_client_wakeup(self: MailProxy, timeout: float | None) -> None:
        """Pause the client report loop until timeout or wake event."""
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


__all__ = ["EventReporterMixin"]
