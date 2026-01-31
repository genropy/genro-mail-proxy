# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Configuration dataclasses for MailProxy (Community Edition).

This module defines the configuration hierarchy for genro-mail-proxy.
ProxyConfig is the single entry point for all configuration, organizing
settings into logical nested groups (timing, queue, concurrency, etc.).

Architecture:
    ProxyConfig is used by MailProxyBase (and thus MailProxy) to configure
    the service. It is CE-only: Enterprise Edition does not extend these
    dataclasses but rather stores EE-specific config in the database
    (via InstanceTable_EE for bounce detection).

Usage:
    config = ProxyConfig(
        db_path="/data/mail.db",
        timing=TimingConfig(send_loop_interval=1.0),
        concurrency=ConcurrencyConfig(max_sends=20),
    )
    proxy = MailProxy(config=config)

    # Access nested config
    interval = proxy.config.timing.send_loop_interval

Nested Config Classes:
    - TimingConfig: Intervals, timeouts, retention periods
    - QueueConfig: Queue sizes, batch limits
    - ConcurrencyConfig: Parallelism limits (sends, attachments)
    - ClientSyncConfig: Upstream reporting (URL, auth)
    - RetryConfig: Retry behavior (max attempts, delays)
    - CacheConfig: Attachment cache (memory/disk tiers)
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TimingConfig:
    """Timing and interval settings for the dispatch service."""

    send_loop_interval: float = 0.5
    """Seconds between SMTP dispatch loop iterations."""

    attachment_timeout: int = 30
    """Timeout in seconds for fetching attachments."""

    report_retention_seconds: int = 7 * 24 * 3600
    """How long to retain reported messages (default 7 days)."""


@dataclass
class QueueConfig:
    """Queue sizes and batch limits for message processing."""

    result_size: int = 1000
    """Maximum size of the delivery result queue."""

    message_size: int = 10000
    """Maximum messages to fetch per SMTP cycle."""

    put_timeout: float = 5.0
    """Timeout in seconds for queue operations."""

    max_enqueue_batch: int = 1000
    """Maximum messages allowed in single addMessages call."""


@dataclass
class ConcurrencyConfig:
    """Parallelism limits for SMTP sends and attachment fetches."""

    max_sends: int = 10
    """Maximum concurrent SMTP sends globally."""

    max_per_account: int = 3
    """Maximum concurrent sends per SMTP account."""

    max_attachments: int = 3
    """Maximum concurrent attachment fetches."""


@dataclass
class ClientSyncConfig:
    """Upstream delivery report synchronization settings."""

    url: str | None = None
    """URL for posting delivery reports to upstream service."""

    user: str | None = None
    """Username for client sync authentication."""

    password: str | None = None
    """Password for client sync authentication."""

    token: str | None = None
    """Bearer token for client sync authentication."""


@dataclass
class RetryConfig:
    """SMTP retry behavior with exponential backoff."""

    max_retries: int = 3
    """Maximum retry attempts."""

    delays: tuple[int, ...] = (60, 300, 900)
    """Delay in seconds between retries (exponential backoff)."""


@dataclass
class CacheConfig:
    """Two-tier attachment cache configuration (memory + disk)."""

    memory_max_mb: float = 50.0
    """Max memory cache size in MB."""

    memory_ttl_seconds: int = 300
    """Memory cache TTL in seconds."""

    disk_dir: str | None = None
    """Directory for disk cache. None disables disk caching."""

    disk_max_mb: float = 500.0
    """Max disk cache size in MB."""

    disk_ttl_seconds: int = 3600
    """Disk cache TTL in seconds."""

    disk_threshold_kb: float = 100.0
    """Size threshold for disk vs memory (items larger go to disk)."""

    @property
    def enabled(self) -> bool:
        """Check if caching is enabled (disk dir configured)."""
        return self.disk_dir is not None


@dataclass
class ProxyConfig:
    """Main configuration container for MailProxy (CE).

    Single entry point for all proxy configuration. Groups settings into
    logical nested structures for clean organization and access.

    This class is CE-only. Enterprise Edition stores additional config
    (bounce detection, PEC) in the database via table extensions.

    Nested Groups:
        timing: Intervals, timeouts, retention periods
        queue: Queue sizes, batch limits
        concurrency: Parallelism limits (sends, attachments)
        client_sync: Upstream reporting (URL, auth)
        retry: Retry behavior (max attempts, delays)
        cache: Attachment cache (memory/disk tiers)

    Top-Level Settings:
        db_path: SQLite/PostgreSQL database path
        instance_name: Service identifier for display
        port: Default API server port
        api_token: Optional bearer token for API auth
        default_priority: Default message priority (0-3)
        test_mode: Disable auto-processing for tests
        log_delivery_activity: Verbose delivery logging
        start_active: Start processing immediately
        report_delivery_callable: Custom delivery report handler

    Example:
        config = ProxyConfig(
            db_path="/data/mail.db",
            timing=TimingConfig(send_loop_interval=1.0),
            concurrency=ConcurrencyConfig(max_sends=20),
        )
        proxy = MailProxy(config=config)
        interval = proxy.config.timing.send_loop_interval
    """

    db_path: str = "/data/mail_service.db"
    """SQLite database path for persistence."""

    instance_name: str = "mail-proxy"
    """Instance name for display and identification."""

    port: int = 8000
    """Default port for API server."""

    api_token: str | None = None
    """API authentication token. If None, no auth required."""

    timing: TimingConfig = field(default_factory=TimingConfig)
    """Timing and interval settings."""

    queue: QueueConfig = field(default_factory=QueueConfig)
    """Queue size and batch settings."""

    concurrency: ConcurrencyConfig = field(default_factory=ConcurrencyConfig)
    """Concurrency limits."""

    client_sync: ClientSyncConfig = field(default_factory=ClientSyncConfig)
    """Client synchronization settings."""

    retry: RetryConfig = field(default_factory=RetryConfig)
    """Retry behavior settings."""

    cache: CacheConfig = field(default_factory=CacheConfig)
    """Attachment cache settings."""

    default_priority: int = 2
    """Default message priority (0=immediate, 1=high, 2=medium, 3=low)."""

    test_mode: bool = False
    """Enable test mode (disables automatic loop processing)."""

    log_delivery_activity: bool = False
    """Enable verbose delivery activity logging."""

    start_active: bool = False
    """Whether to start processing messages immediately."""

    report_delivery_callable: Callable[[dict[str, Any]], Awaitable[None]] | None = None
    """Optional async callable for custom report delivery."""


__all__ = [
    "CacheConfig",
    "ClientSyncConfig",
    "ConcurrencyConfig",
    "ProxyConfig",
    "QueueConfig",
    "RetryConfig",
    "TimingConfig",
]
