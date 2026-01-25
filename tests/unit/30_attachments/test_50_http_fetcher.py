"""Tests for the HTTP fetcher module."""

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mail_proxy.attachments.http_fetcher import HttpFetcher


class TestParsePath:
    """Tests for path parsing."""

    def test_path_with_explicit_server(self):
        """Test parsing path with explicit server URL."""
        fetcher = HttpFetcher(default_endpoint=None)
        server, params = fetcher._parse_path("[https://api.example.com/files]doc_id=123")

        assert server == "https://api.example.com/files"
        assert params == "doc_id=123"

    def test_path_with_default_endpoint(self):
        """Test parsing path using default endpoint."""
        fetcher = HttpFetcher(default_endpoint="https://default.example.com/api")
        server, params = fetcher._parse_path("doc_id=456&version=2")

        assert server == "https://default.example.com/api"
        assert params == "doc_id=456&version=2"

    def test_path_without_endpoint_raises(self):
        """Test that path without server and no default raises error."""
        fetcher = HttpFetcher(default_endpoint=None)

        with pytest.raises(ValueError, match="No default endpoint"):
            fetcher._parse_path("doc_id=789")

    def test_invalid_server_format(self):
        """Test that malformed server bracket raises error."""
        fetcher = HttpFetcher()

        with pytest.raises(ValueError, match="Invalid HTTP path format"):
            fetcher._parse_path("[malformed")

    def test_empty_params(self):
        """Test parsing path with empty params."""
        fetcher = HttpFetcher(default_endpoint="https://api.example.com")
        server, params = fetcher._parse_path("[https://other.com]")

        assert server == "https://other.com"
        assert params == ""


class TestAuthHeaders:
    """Tests for authentication headers."""

    def test_bearer_auth(self):
        """Test bearer token authentication."""
        fetcher = HttpFetcher(
            auth_config={"method": "bearer", "token": "my-secret-token"}
        )
        headers = fetcher._get_auth_headers()

        assert headers == {"Authorization": "Bearer my-secret-token"}

    def test_basic_auth(self):
        """Test basic authentication."""
        fetcher = HttpFetcher(
            auth_config={"method": "basic", "user": "admin", "password": "secret"}
        )
        headers = fetcher._get_auth_headers()

        expected = base64.b64encode(b"admin:secret").decode()
        assert headers == {"Authorization": f"Basic {expected}"}

    def test_no_auth(self):
        """Test no authentication."""
        fetcher = HttpFetcher(auth_config={"method": "none"})
        headers = fetcher._get_auth_headers()

        assert headers == {}

    def test_default_no_auth(self):
        """Test default (no auth_config) returns empty headers."""
        fetcher = HttpFetcher()
        headers = fetcher._get_auth_headers()

        assert headers == {}


class TestFetch:
    """Tests for single fetch operation."""

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        """Test successful fetch."""
        fetcher = HttpFetcher(default_endpoint="https://api.example.com/files")
        expected_content = b"file content here"

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=expected_content)
        mock_response.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_session.post = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=None),
        )):
            result = await fetcher.fetch("doc_id=123")

        assert result == expected_content

    @pytest.mark.asyncio
    async def test_fetch_with_explicit_server(self):
        """Test fetch with explicit server URL in path."""
        fetcher = HttpFetcher()  # No default endpoint
        expected_content = b"data"

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=expected_content)
        mock_response.raise_for_status = MagicMock()

        mock_session = MagicMock()
        mock_post_cm = MagicMock()
        mock_post_cm.__aenter__ = AsyncMock(return_value=mock_response)
        mock_post_cm.__aexit__ = AsyncMock(return_value=None)
        mock_session.post = MagicMock(return_value=mock_post_cm)

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=None),
        )):
            result = await fetcher.fetch("[https://other.com/api]id=456")

        assert result == expected_content
        # Verify the correct URL was called
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        assert call_args[0][0] == "https://other.com/api"
        # Now uses JSON body instead of form data
        assert call_args[1]["json"] == {"storage_path": "id=456"}


class TestDefaultEndpointProperty:
    """Tests for the default_endpoint property."""

    def test_returns_configured_endpoint(self):
        """Test that property returns the configured endpoint."""
        fetcher = HttpFetcher(default_endpoint="https://api.example.com")
        assert fetcher.default_endpoint == "https://api.example.com"

    def test_returns_none_when_not_configured(self):
        """Test that property returns None when not configured."""
        fetcher = HttpFetcher()
        assert fetcher.default_endpoint is None


class TestIntegrationScenarios:
    """Integration-style tests for common scenarios."""

    @pytest.mark.asyncio
    async def test_fetch_with_bearer_auth(self):
        """Test fetch includes bearer auth header."""
        fetcher = HttpFetcher(
            default_endpoint="https://api.example.com",
            auth_config={"method": "bearer", "token": "test-token-123"},
        )

        captured_headers = {}

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=b"data")
        mock_response.raise_for_status = MagicMock()

        mock_session = MagicMock()

        def capture_post(url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            return mock_cm

        mock_session.post = capture_post

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=None),
        )):
            await fetcher.fetch("doc_id=123")

        assert "Authorization" in captured_headers
        assert captured_headers["Authorization"] == "Bearer test-token-123"

    @pytest.mark.asyncio
    async def test_fetch_with_basic_auth(self):
        """Test fetch includes basic auth header."""
        fetcher = HttpFetcher(
            default_endpoint="https://api.example.com",
            auth_config={"method": "basic", "user": "myuser", "password": "mypass"},
        )

        captured_headers = {}

        mock_response = AsyncMock()
        mock_response.read = AsyncMock(return_value=b"data")
        mock_response.raise_for_status = MagicMock()

        mock_session = MagicMock()

        def capture_post(url, **kwargs):
            captured_headers.update(kwargs.get("headers", {}))
            mock_cm = MagicMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_response)
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            return mock_cm

        mock_session.post = capture_post

        with patch("aiohttp.ClientSession", return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_session),
            __aexit__=AsyncMock(return_value=None),
        )):
            await fetcher.fetch("doc_id=123")

        expected_creds = base64.b64encode(b"myuser:mypass").decode()
        assert "Authorization" in captured_headers
        assert captured_headers["Authorization"] == f"Basic {expected_creds}"
