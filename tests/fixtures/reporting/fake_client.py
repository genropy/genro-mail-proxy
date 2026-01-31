# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""FakeClient: simulates a tenant client for testing the sync protocol.

Protocol flow:
1. Proxy calls FakeClient sync endpoint with delivery_report
2. FakeClient reads next batch of messages from CSV
3. FakeClient calls proxy's POST /commands/add-messages to submit batch
4. FakeClient responds with {"ok": true, "queued": N}
5. If queued > 0, proxy immediately re-calls sync
6. Repeat until queued = 0

Usage:
    async with FakeClient(csv_path, proxy_url, tenant_id, account_id) as client:
        # Client is running on client.base_url
        # Configure proxy to call client.base_url + "/sync"
        await run_proxy_reporting_cycle()
        # Check client.received_reports and client.submitted_messages
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web


class FakeClient:
    """Simulates a tenant client that receives reports and submits messages."""

    def __init__(
        self,
        csv_path: str | Path,
        proxy_url: str,
        tenant_id: str,
        account_id: str,
        batch_size: int = 10,
        api_token: str | None = None,
    ):
        """Initialize FakeClient.

        Args:
            csv_path: Path to CSV file with messages to send.
            proxy_url: Base URL of the proxy (e.g., "http://localhost:8000").
            tenant_id: Tenant identifier.
            account_id: SMTP account ID for sending.
            batch_size: Max messages per batch (default 10).
            api_token: API token for proxy authentication.
        """
        self.csv_path = Path(csv_path)
        self.proxy_url = proxy_url.rstrip("/")
        self.tenant_id = tenant_id
        self.account_id = account_id
        self.batch_size = batch_size
        self.api_token = api_token

        # State
        self._messages: list[dict[str, Any]] = []
        self._pending_indices: list[int] = []
        self.received_reports: list[dict[str, Any]] = []
        self.submitted_messages: list[dict[str, Any]] = []

        # Server
        self._app: web.Application | None = None
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.port: int = 0
        self.base_url: str = ""

        # Attachment data for endpoint mode
        self._attachment_data: dict[str, bytes] = {}

    async def __aenter__(self) -> FakeClient:
        """Start the HTTP server."""
        self._load_messages()
        await self._start_server()
        return self

    async def __aexit__(self, _exc_type: Any, _exc_val: Any, _exc_tb: Any) -> None:
        """Stop the HTTP server."""
        await self._stop_server()

    def _load_messages(self) -> None:
        """Load messages from CSV file."""
        self._messages = []
        self._pending_indices = []

        if not self.csv_path.exists():
            return

        with open(self.csv_path, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for idx, row in enumerate(reader):
                self._messages.append(row)
                self._pending_indices.append(idx)

    def register_attachment(self, storage_path: str, content: bytes) -> None:
        """Register attachment data for endpoint fetch mode.

        Args:
            storage_path: The storage_path value (e.g., "doc_id=123").
            content: Binary content to return when requested.
        """
        self._attachment_data[storage_path] = content

    def _build_message_payload(self, row: dict[str, str]) -> dict[str, Any]:
        """Convert CSV row to message payload for add-messages API."""
        msg: dict[str, Any] = {
            "id": row["id"],
            "tenant_id": self.tenant_id,
            "account_id": self.account_id,
            "from": row["from"],
            "to": [addr.strip() for addr in row["to"].split(";") if addr.strip()],
            "subject": row["subject"],
            "body": row.get("body", ""),
        }

        # Handle attachments
        filename = row.get("attachment_filename", "").strip()
        storage_path = row.get("attachment_path", "").strip()
        mode = row.get("attachment_mode", "").strip()

        if filename and storage_path:
            attachment: dict[str, Any] = {
                "filename": filename,
                "storage_path": storage_path,
            }
            if mode:
                attachment["fetch_mode"] = mode
            msg["attachments"] = [attachment]

        return msg

    def _get_next_batch(self) -> tuple[list[dict[str, Any]], int]:
        """Get next batch of messages to submit.

        Returns:
            Tuple of (messages_to_submit, remaining_count).
        """
        if not self._pending_indices:
            return [], 0

        # Get next batch
        batch_indices = self._pending_indices[:self.batch_size]
        self._pending_indices = self._pending_indices[self.batch_size:]

        messages = []
        for idx in batch_indices:
            row = self._messages[idx]
            messages.append(self._build_message_payload(row))

        return messages, len(self._pending_indices)

    async def _submit_messages_to_proxy(self, messages: list[dict[str, Any]]) -> dict:
        """Submit messages to proxy via POST /commands/add-messages."""
        url = f"{self.proxy_url}/messages/add_batch"
        headers = {"Content-Type": "application/json"}
        if self.api_token:
            headers["X-API-Token"] = self.api_token

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"messages": messages}, headers=headers) as resp:
                result = await resp.json()
                return result

    async def _handle_sync(self, request: web.Request) -> web.Response:
        """Handle sync endpoint call from proxy.

        This is the core of the bidirectional protocol:
        1. Receive delivery report
        2. Submit next batch of messages
        3. Respond with remaining count
        """
        try:
            data = await request.json()
        except Exception:
            data = {}

        # Store received reports
        reports = data.get("delivery_report", [])
        self.received_reports.extend(reports)

        # Get next batch of messages
        messages, remaining = self._get_next_batch()

        # Submit messages to proxy BEFORE responding
        if messages:
            await self._submit_messages_to_proxy(messages)
            self.submitted_messages.extend(messages)

        # Respond with queued count (remaining messages)
        # Note: queued = remaining + len(messages just submitted)
        # because proxy will process them and call us again
        queued = remaining + len(messages)

        return web.json_response({
            "ok": True,
            "queued": queued,
        })

    async def _handle_attachment(self, request: web.Request) -> web.Response:
        """Handle attachment fetch endpoint for fetch_mode=endpoint."""
        try:
            data = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        storage_path = data.get("storage_path", "")

        if storage_path in self._attachment_data:
            content = self._attachment_data[storage_path]
            return web.Response(body=content, content_type="application/octet-stream")

        return web.json_response({"error": f"Attachment not found: {storage_path}"}, status=404)

    async def _handle_health(self, _request: web.Request) -> web.Response:
        """Health check endpoint."""
        return web.json_response({"ok": True, "tenant_id": self.tenant_id})

    async def _start_server(self) -> None:
        """Start the aiohttp server."""
        self._app = web.Application()
        self._app.router.add_post("/sync", self._handle_sync)
        self._app.router.add_post("/attachments", self._handle_attachment)
        self._app.router.add_get("/health", self._handle_health)

        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        # Find available port
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()

        # Get actual port - use getattr to avoid type checker issues with AbstractServer
        server = self._site._server
        sockets = getattr(server, "sockets", None) if server else None
        if sockets:
            self.port = sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{self.port}"

    async def _stop_server(self) -> None:
        """Stop the aiohttp server."""
        if self._runner:
            await self._runner.cleanup()

    @property
    def sync_url(self) -> str:
        """URL for the sync endpoint."""
        return f"{self.base_url}/sync"

    @property
    def attachment_url(self) -> str:
        """URL for the attachment endpoint."""
        return f"{self.base_url}/attachments"

    @property
    def pending_count(self) -> int:
        """Number of messages still pending to send."""
        return len(self._pending_indices)

    @property
    def total_messages(self) -> int:
        """Total number of messages loaded from CSV."""
        return len(self._messages)

    def get_expected_statuses(self) -> dict[str, str]:
        """Get expected status for each message ID from CSV."""
        return {
            row["id"]: row.get("expected_status", "sent")
            for row in self._messages
        }


__all__ = ["FakeClient"]
