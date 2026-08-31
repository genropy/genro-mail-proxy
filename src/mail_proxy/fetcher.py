# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Transport helpers to fetch pending messages and push delivery reports.

This module provides the Fetcher class for bidirectional communication with
upstream Genropy services. It handles both retrieval of pending messages from
an external queue and reporting of delivery outcomes back to the originating
system.

The transport layer supports two modes of operation:
- HTTP-based communication with a configurable base URL
- Callable-based integration for testing or custom transports

Example:
    Using the Fetcher with an HTTP endpoint::

        fetcher = Fetcher(fetch_url="https://api.example.com/mail")
        messages = await fetcher.fetch_messages()
        for msg in messages:
            # Process and send message
            await fetcher.report_delivery({
                "id": msg["id"],
                "status": "sent",
                "timestamp": "2025-01-15T10:30:00Z"
            })

    Using custom callables for testing::

        async def mock_fetch():
            return [{"id": "1", "to": "test@example.com"}]

        fetcher = Fetcher(fetch_callable=mock_fetch)
        messages = await fetcher.fetch_messages()
"""

from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

JsonDict = dict[str, Any]
FetchCallable = Callable[[], Awaitable[list[JsonDict]]]
ReportCallable = Callable[[JsonDict], Awaitable[None]]


class Fetcher:
    """HTTP client for message retrieval and delivery report submission.

    Handles communication with upstream Genropy services via REST endpoints
    or custom callables. Supports both fetching pending messages from an
    external queue and reporting delivery outcomes.

    The class is designed to be flexible for both production use (with HTTP
    endpoints) and testing scenarios (with custom callables).

    Attributes:
        fetch_url: Base URL for the upstream service API.
        fetch_callable: Optional async callable for custom message retrieval.
        report_callable: Optional async callable for custom report delivery.
    """

    def __init__(
        self,
        fetch_url: str | None = None,
        fetch_callable: FetchCallable | None = None,
        report_callable: ReportCallable | None = None,
    ):
        """Initialize the Fetcher with transport configuration.

        Args:
            fetch_url: Base URL of the upstream Genropy service. Endpoints
                for fetch-messages and delivery-report are appended to this URL.
            fetch_callable: Optional async callable that returns a list of
                message dictionaries. When provided, bypasses HTTP fetch.
            report_callable: Optional async callable that accepts a delivery
                report dictionary. When provided, bypasses HTTP reporting.
        """
        self.fetch_url = fetch_url
        self.fetch_callable = fetch_callable
        self.report_callable = report_callable

    def _endpoint(self, suffix: str) -> str | None:
        """Construct a full endpoint URL from the base URL and path suffix.

        Args:
            suffix: Path suffix to append to the base URL (e.g., "fetch-messages").

        Returns:
            Complete URL string if fetch_url is configured, None otherwise.
        """
        if not self.fetch_url:
            return None
        base = self.fetch_url.rstrip("/")
        return f"{base}/{suffix.lstrip('/')}"

    async def fetch_messages(self) -> list[JsonDict]:
        """Retrieve pending messages from the upstream service.

        If a custom fetch_callable was provided during initialization, it will
        be used instead of making an HTTP request. Otherwise, performs a GET
        request to the fetch-messages endpoint.

        Returns:
            List of message dictionaries ready for processing. Returns an
            empty list if no messages are available or if fetch_url is not
            configured.

        Raises:
            aiohttp.ClientError: If the HTTP request fails.
        """
        if self.fetch_callable is not None:
            return await self.fetch_callable()
        endpoint = self._endpoint("fetch-messages")
        if not endpoint:
            return []
        async with aiohttp.ClientSession() as session, session.get(endpoint) as resp:
            resp.raise_for_status()
            data = await resp.json()
            msgs = data.get("messages", [])
            return msgs if isinstance(msgs, list) else []

    async def report_delivery(self, payload: JsonDict) -> None:
        """Submit a delivery report to the upstream service.

        If a custom report_callable was provided during initialization, it
        will be used instead of making an HTTP request. Otherwise, performs
        a POST request to the delivery-report endpoint.

        Args:
            payload: Delivery report dictionary containing at minimum the
                message ID and delivery status. Typically includes:
                - id: Original message identifier
                - status: "sent", "error", or "deferred"
                - timestamp: ISO-8601 formatted delivery timestamp
                - error: Error message (if status is "error")

        Raises:
            aiohttp.ClientError: If the HTTP request fails.
        """
        if self.report_callable is not None:
            await self.report_callable(payload)
            return
        endpoint = self._endpoint("delivery-report")
        if not endpoint:
            return
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload) as resp:
                resp.raise_for_status()
