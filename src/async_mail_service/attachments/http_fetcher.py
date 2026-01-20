"""HTTP attachment fetcher with batching support.

This module provides a fetcher for attachments served via HTTP endpoints.
It supports batching multiple requests to the same server into a single
POST request with multipart response for efficiency.

Supported path formats:
- "@params" - POST to default endpoint with params as body
- "@[url]params" - POST to specific URL with params as body

Example:
    Fetching attachments via HTTP::

        fetcher = HttpFetcher(
            default_endpoint="https://api.example.com/attachments",
            auth_config={"method": "bearer", "token": "secret"}
        )

        # Single fetch
        content = await fetcher.fetch("doc_id=123&version=2")

        # Batch fetch (more efficient)
        results = await fetcher.fetch_batch([
            {"storage_path": "@doc_id=123"},
            {"storage_path": "@doc_id=456"},
        ])
"""

from __future__ import annotations

import base64
import re
from typing import Any, Dict, List, Optional, Tuple

import aiohttp


class HttpFetcher:
    """Fetcher for HTTP-served attachments with batching support.

    Supports authentication via bearer token or basic auth.
    Can batch multiple requests to the same server for efficiency.

    Attributes:
        _default_endpoint: Default URL for requests without explicit server.
        _auth_config: Authentication configuration dictionary.
    """

    def __init__(
        self,
        default_endpoint: Optional[str] = None,
        auth_config: Optional[Dict[str, str]] = None,
    ):
        """Initialize the HTTP fetcher.

        Args:
            default_endpoint: Default URL for requests without explicit server.
            auth_config: Authentication configuration with keys:
                - method: "none", "bearer", or "basic"
                - token: Bearer token (for method="bearer")
                - user: Username (for method="basic")
                - password: Password (for method="basic")
        """
        self._default_endpoint = default_endpoint
        self._auth_config = auth_config or {}

    def _parse_path(self, path: str) -> Tuple[str, str]:
        """Parse HTTP path into server URL and params.

        Args:
            path: Path in format "[server]params" or just "params".

        Returns:
            Tuple of (server_url, params).

        Raises:
            ValueError: If path format is invalid or no endpoint available.
        """
        if path.startswith("["):
            # Explicit server: [https://example.com/api]params
            match = re.match(r'\[([^\]]+)\](.*)', path)
            if not match:
                raise ValueError(f"Invalid HTTP path format: {path}")
            return match.group(1), match.group(2)

        # Use default endpoint
        if not self._default_endpoint:
            raise ValueError(
                "No default endpoint configured and path doesn't specify one"
            )
        return self._default_endpoint, path

    def _get_auth_headers(
        self, auth_override: Optional[Dict[str, str]] = None
    ) -> Dict[str, str]:
        """Build authentication headers based on config.

        Args:
            auth_override: Optional auth config to use instead of default.

        Returns:
            Dictionary of HTTP headers for authentication.
        """
        auth_config = auth_override if auth_override is not None else self._auth_config
        method = auth_config.get("method", "none")

        if method == "bearer":
            token = auth_config.get("token", "")
            return {"Authorization": f"Bearer {token}"}

        if method == "basic":
            user = auth_config.get("user", "")
            password = auth_config.get("password", "")
            credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
            return {"Authorization": f"Basic {credentials}"}

        return {}

    async def fetch(
        self, path: str, auth_override: Optional[Dict[str, str]] = None
    ) -> bytes:
        """Fetch a single attachment via HTTP.

        Args:
            path: HTTP path (without the "@" prefix).
            auth_override: Optional auth config to use instead of default.

        Returns:
            Binary content of the attachment.

        Raises:
            ValueError: If path is invalid.
            aiohttp.ClientError: If the HTTP request fails.
        """
        server_url, params = self._parse_path(path)
        headers = self._get_auth_headers(auth_override)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                server_url,
                data=params,
                headers=headers,
            ) as response:
                response.raise_for_status()
                return await response.read()

    async def fetch_batch(
        self,
        attachments: List[Dict[str, Any]],
    ) -> Dict[str, bytes]:
        """Fetch multiple attachments, batching by server.

        Groups attachments by server URL and sends batched requests
        where possible. The server should respond with multipart/mixed
        content containing all requested files.

        Args:
            attachments: List of attachment dicts with "storage_path" key.
                Paths should include the "@" prefix.

        Returns:
            Dictionary mapping original storage_path to content bytes.

        Raises:
            aiohttp.ClientError: If an HTTP request fails.
        """
        results: Dict[str, bytes] = {}

        # Group by server
        by_server: Dict[str, List[Tuple[str, str]]] = {}
        for att in attachments:
            storage_path = att.get("storage_path", "")
            if not storage_path.startswith("@"):
                continue

            path = storage_path[1:]  # Remove "@" prefix
            try:
                server_url, params = self._parse_path(path)
                by_server.setdefault(server_url, []).append((storage_path, params))
            except ValueError:
                continue

        headers = self._get_auth_headers()

        async with aiohttp.ClientSession() as session:
            for server_url, items in by_server.items():
                if len(items) == 1:
                    # Single item - use simple fetch
                    storage_path, params = items[0]
                    async with session.post(
                        server_url,
                        data=params,
                        headers=headers,
                    ) as response:
                        response.raise_for_status()
                        results[storage_path] = await response.read()
                else:
                    # Multiple items - try batch request
                    params_list = [params for _, params in items]
                    try:
                        batch_results = await self._fetch_batch_from_server(
                            session, server_url, params_list, headers
                        )
                        for (storage_path, _), content in zip(items, batch_results):
                            results[storage_path] = content
                    except Exception:
                        # Fallback to individual requests if batch fails
                        for storage_path, params in items:
                            async with session.post(
                                server_url,
                                data=params,
                                headers=headers,
                            ) as response:
                                response.raise_for_status()
                                results[storage_path] = await response.read()

        return results

    async def _fetch_batch_from_server(
        self,
        session: aiohttp.ClientSession,
        server_url: str,
        params_list: List[str],
        headers: Dict[str, str],
    ) -> List[bytes]:
        """Send a batch request and parse multipart response.

        Args:
            session: aiohttp client session.
            server_url: Server URL to POST to.
            params_list: List of params strings to request.
            headers: HTTP headers including auth.

        Returns:
            List of content bytes in same order as params_list.
        """
        async with session.post(
            server_url,
            json={"attachments": params_list},
            headers={**headers, "Content-Type": "application/json"},
        ) as response:
            response.raise_for_status()

            content_type = response.headers.get("Content-Type", "")

            if "multipart/mixed" in content_type:
                # Parse multipart response
                return await self._parse_multipart_response(response)
            elif "application/json" in content_type:
                # JSON response with base64-encoded contents
                data = await response.json()
                return [
                    base64.b64decode(item.get("content", ""))
                    for item in data.get("attachments", [])
                ]
            else:
                # Single binary response (shouldn't happen for batch)
                return [await response.read()]

    async def _parse_multipart_response(
        self,
        response: aiohttp.ClientResponse,
    ) -> List[bytes]:
        """Parse a multipart/mixed response into content list.

        Args:
            response: aiohttp response with multipart content.

        Returns:
            List of content bytes from each part.
        """
        results = []
        reader = aiohttp.MultipartReader.from_response(response)

        async for part in reader:
            content = await part.read()
            results.append(content)

        return results

    @property
    def default_endpoint(self) -> Optional[str]:
        """The configured default endpoint URL."""
        return self._default_endpoint
