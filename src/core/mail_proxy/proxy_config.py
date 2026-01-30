# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Configuration dataclasses for MailProxy.

Provides nested configuration structure for clean parameter organization:
- proxy.config.timing.send_loop_interval
- proxy.config.queue.max_enqueue_batch
- proxy.config.concurrency.max_sends
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable


@dataclass
class TimingConfig:
    """Timing and interval settings."""

    send_loop_interval: float = 0.5
    """Seconds between SMTP dispatch loop iterations."""

    attachment_timeout: int = 30
    """Timeout in seconds for fetching attachments."""

    report_retention_seconds: int = 7 * 24 * 3600
    """How long to retain reported messages (default 7 days)."""


@dataclass
class QueueConfig:
    """Queue size and batch settings."""

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
    """Concurrency limits."""

    max_sends: int = 10
    """Maximum concurrent SMTP sends globally."""

    max_per_account: int = 3
    """Maximum concurrent sends per SMTP account."""

    max_attachments: int = 3
    """Maximum concurrent attachment fetches."""


@dataclass
class ClientSyncConfig:
    """Client synchronization settings."""

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
    """Retry behavior settings."""

    max_retries: int = 3
    """Maximum retry attempts."""

    delays: tuple[int, ...] = (60, 300, 900)
    """Delay in seconds between retries (exponential backoff)."""


@dataclass
class CacheConfig:
    """Configuration for attachment cache."""

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
    """Main configuration container for MailProxy.

    Groups all configuration into logical nested structures:
    - timing: Intervals and timeouts
    - queue: Queue sizes and batch limits
    - concurrency: Parallelism limits
    - client_sync: Upstream reporting settings
    - retry: Retry behavior

    Example:
        config = ProxyConfig(
            db_path="/data/mail.db",
            timing=TimingConfig(send_loop_interval=1.0),
            concurrency=ConcurrencyConfig(max_sends=20),
        )
        proxy = MailProxy(config=config)

        # Access nested config
        interval = proxy.config.timing.send_loop_interval
    """

    db_path: str = "/data/mail_service.db"
    """SQLite database path for persistence."""

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
