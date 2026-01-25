"""Shared pytest fixtures for mail_proxy tests."""

import asyncio
import types
from typing import Any

import pytest


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# Marker registration
def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "docker: marks tests requiring Docker")
    config.addinivalue_line("markers", "network: marks tests requiring network access")
    config.addinivalue_line("markers", "integration: marks tests as integration tests")
    config.addinivalue_line("markers", "fullstack: marks tests requiring full Docker infrastructure")
    config.addinivalue_line("markers", "db: marks tests as database integration tests")


# Dummy classes for mocking dependencies
class DummyRateLimiter:
    """Dummy rate limiter for testing."""

    async def check_and_plan(self, account):
        return (None, False)

    async def log_send(self, account_id: str):
        pass


class DummyMetrics:
    """Dummy metrics for testing."""

    def __init__(self):
        self.sent_count = 0
        self.error_count = 0
        self.deferred_count = 0

    def set_pending(self, value: int):
        pass

    def inc_sent(self, account_id: str):
        self.sent_count += 1

    def inc_error(self, account_id: str):
        self.error_count += 1

    def inc_deferred(self, account_id: str):
        self.deferred_count += 1

    def inc_rate_limited(self, account_id: str):
        pass


class DummyAttachments:
    """Dummy attachment manager for testing."""

    async def fetch(self, attachment):
        return (b"test content", attachment.get("filename", "file.txt"))

    def guess_mime(self, filename):
        return "application", "octet-stream"


class DummyPool:
    """Dummy SMTP pool for testing."""

    def __init__(self):
        self.sent: list[dict[str, Any]] = []

    async def get_connection(self, host, port, user, password, use_tls):
        return self

    async def send_message(self, message, from_addr=None, **_kwargs):
        self.sent.append({"message": message, "from": from_addr})

    async def cleanup(self):
        pass


class DummyReporter:
    """Dummy delivery reporter for testing."""

    def __init__(self):
        self.payloads: list[dict[str, Any]] = []

    async def __call__(self, payload: dict[str, Any]):
        self.payloads.append(payload)


def make_dummy_logger():
    """Create a dummy logger that swallows all messages."""
    return types.SimpleNamespace(
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        exception=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
    )


@pytest.fixture
def dummy_rate_limiter():
    """Provide a dummy rate limiter."""
    return DummyRateLimiter()


@pytest.fixture
def dummy_metrics():
    """Provide a dummy metrics instance."""
    return DummyMetrics()


@pytest.fixture
def dummy_attachments():
    """Provide a dummy attachments manager."""
    return DummyAttachments()


@pytest.fixture
def dummy_pool():
    """Provide a dummy SMTP pool."""
    return DummyPool()


@pytest.fixture
def dummy_reporter():
    """Provide a dummy delivery reporter."""
    return DummyReporter()


@pytest.fixture
def dummy_logger():
    """Provide a dummy logger."""
    return make_dummy_logger()
