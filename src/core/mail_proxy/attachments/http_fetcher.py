# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: BSL-1.1
"""HTTP attachment fetcher.

This module provides a fetcher for attachments served via HTTP endpoints.
The fetcher sends POST requests with JSON body containing the storage_path,
or GET requests for direct URLs.

Example:
    Fetching attachments via HTTP::

        fetcher = HttpFetcher(
            default_endpoint="https://api.example.com/attachments",
            auth_config={"method": "bearer", "token": "secret"}
        )

        # Endpoint-based fetch - sends POST with {"storage_path": "vol:path/to/file.pdf"}
        content = await fetcher.fetch("vol:path/to/file.pdf")

        # Direct URL fetch - sends GET
        content = await fetcher.fetch("https://example.com/file.pdf")
"""

from __future__ import annotations

import base64
import re
import aiohttp


class HttpFetcher:
    """Fetcher for HTTP-served attachments.

    Supports authentication via bearer token or basic auth.

    Attributes:
        _default_endpoint: Default URL for requests without explicit server.
        _auth_config: Authentication configuration dictionary.
    """

    def __init__(
        self,
        default_endpoint: str | None = None,
        auth_config: dict[str, str] | None = None,
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

    def _parse_path(self, path: str) -> tuple[str, str]:
        """Parse HTTP path into server URL and params.

        Args:
            path: Path in one of these formats:
                - "[server]params" - explicit server with params
                - "http://..." or "https://..." - direct URL (returns empty params)
                - "params" - uses default endpoint

        Returns:
            Tuple of (server_url, params). Empty params means direct URL fetch (GET).

        Raises:
            ValueError: If path format is invalid or no endpoint available.
        """
        if path.startswith("["):
            # Explicit server: [https://example.com/api]params
            match = re.match(r'\[([^\]]+)\](.*)', path)
            if not match:
                raise ValueError(f"Invalid HTTP path format: {path}")
            return match.group(1), match.group(2)

        # Direct URL (http_url mode)
        if path.startswith(("http://", "https://")):
            return path, ""

        # Use default endpoint
        if not self._default_endpoint:
            raise ValueError(
                "No default endpoint configured and path doesn't specify one"
            )
        return self._default_endpoint, path

    def _get_auth_headers(
        self, auth_override: dict[str, str] | None = None
    ) -> dict[str, str]:
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
        self, path: str, auth_override: dict[str, str] | None = None
    ) -> bytes:
        """Fetch a single attachment via HTTP.

        Uses GET for direct URLs (empty params) or POST with JSON body for
        endpoint-based fetching.

        Args:
            path: HTTP path in format "[url]" for direct fetch or "[endpoint]params"
                for POST-based fetching.
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
            if not params:
                # Direct URL fetch (http_url mode) - use GET
                async with session.get(server_url, headers=headers) as response:
                    response.raise_for_status()
                    return await response.read()
            else:
                # Endpoint-based fetch - use POST with JSON body
                async with session.post(
                    server_url,
                    json={"storage_path": params},
                    headers=headers,
                ) as response:
                    response.raise_for_status()
                    return await response.read()

    @property
    def default_endpoint(self) -> str | None:
        """The configured default endpoint URL."""
        return self._default_endpoint
