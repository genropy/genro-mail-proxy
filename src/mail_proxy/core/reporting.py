# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Client reporting mixin for MailProxy.

This module provides the ReporterMixin class containing all delivery report
loop logic and client synchronization functionality.
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


class ReporterMixin:
    """Mixin providing client report loop and delivery notification methods.

    This mixin is designed to be used with MailProxy and assumes access to:
    - self.db: Database adapter
    - self.metrics: Prometheus metrics
    - self.logger: Logger instance
    - Various configuration attributes (_client_sync_url, etc.)
    """

    # Type hints for attributes provided by MailProxy
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
        """Background coroutine that pushes delivery reports.

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

    async def _process_client_cycle(self: MailProxy) -> int:
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

        reports = await self.db.fetch_reports(self._smtp_batch_size)
        if not reports:
            # Trigger sync for tenants with sync URL (even without reports)
            # This allows the tenant server to send new messages to enqueue
            if target_tenant_id:
                # Sync only the specified tenant
                tenant = await self.db.get_tenant(target_tenant_id)
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
                tenants = await self.db.list_tenants()
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
                    tenant = await self.db.get_tenant(tenant_id)
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
            await self.db.mark_reported(acked_ids, reported_ts)

        await self._apply_retention()
        return total_queued

    async def _apply_retention(self: MailProxy) -> None:
        """Remove reported messages older than the configured retention period.

        Messages that have been successfully reported to upstream services
        are deleted after the retention period expires to prevent database growth.
        """
        if self._report_retention_seconds <= 0:
            return
        threshold = self._utc_now_epoch() - self._report_retention_seconds
        removed = await self.db.remove_reported_before(threshold)
        if removed:
            await self._refresh_queue_gauge()

    async def _wait_for_client_wakeup(self: MailProxy, timeout: float | None) -> None:
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

    async def _send_delivery_reports(self: MailProxy, payloads: list[dict[str, Any]]) -> tuple[list[str], int]:
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
        async with aiohttp.ClientSession() as session, session.post(
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
        self: MailProxy, tenant: dict[str, Any], payloads: list[dict[str, Any]]
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

        async with aiohttp.ClientSession() as session, session.post(
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
