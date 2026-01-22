# Copyright 2025 Softwell S.r.l. - SPDX-License-Identifier: Apache-2.0
"""Prometheus metrics for monitoring the mail dispatcher.

This module defines the Prometheus counters and gauges used to track email
dispatch operations. All metrics use the ``gmp_`` prefix (genro-mail-proxy).

Metrics exposed:
    - ``gmp_sent_total``: Counter of successfully sent emails per account.
    - ``gmp_errors_total``: Counter of send errors per account.
    - ``gmp_deferred_total``: Counter of deferred emails per account.
    - ``gmp_rate_limited_total``: Counter of rate limit hits per account.
    - ``gmp_pending_messages``: Gauge of messages currently in queue.

Example:
    Accessing metrics via the REST API::

        GET /metrics

    Returns Prometheus text format suitable for scraping.
"""

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest


class MailMetrics:
    """Prometheus metrics collector for the mail dispatcher.

    Encapsulates all Prometheus counters and gauges used to monitor email
    dispatch operations. Each metric is labeled by ``account_id`` to enable
    per-account monitoring and alerting.

    Attributes:
        registry: The Prometheus CollectorRegistry holding all metrics.
        sent: Counter tracking successfully sent emails.
        errors: Counter tracking permanent send failures.
        deferred: Counter tracking temporarily deferred messages.
        rate_limited: Counter tracking rate limit enforcement events.
        pending: Gauge showing current queue depth.
    """

    def __init__(self, registry: CollectorRegistry | None = None):
        """Initialize metrics with an optional custom registry.

        Args:
            registry: Optional Prometheus CollectorRegistry. If not provided,
                a new registry is created. Use a custom registry for testing
                or when multiple metric sets are needed.
        """
        self.registry = registry or CollectorRegistry()
        self.sent = Counter(
            "gmp_sent_total",
            "Total sent emails",
            ["account_id"],
            registry=self.registry,
        )
        self.errors = Counter(
            "gmp_errors_total",
            "Total send errors",
            ["account_id"],
            registry=self.registry,
        )
        self.deferred = Counter(
            "gmp_deferred_total",
            "Total deferred emails",
            ["account_id"],
            registry=self.registry,
        )
        self.rate_limited = Counter(
            "gmp_rate_limited_total",
            "Total rate limited occurrences",
            ["account_id"],
            registry=self.registry,
        )
        self.pending = Gauge(
            "gmp_pending_messages",
            "Current pending messages",
            registry=self.registry,
        )

    def inc_sent(self, account_id: str) -> None:
        """Increment the sent counter for an account.

        Args:
            account_id: The SMTP account identifier. Falls back to "default"
                if empty or None.
        """
        self.sent.labels(account_id=account_id or "default").inc()

    def inc_error(self, account_id: str) -> None:
        """Increment the error counter for an account.

        Args:
            account_id: The SMTP account identifier. Falls back to "default"
                if empty or None.
        """
        self.errors.labels(account_id=account_id or "default").inc()

    def inc_deferred(self, account_id: str) -> None:
        """Increment the deferred counter for an account.

        Args:
            account_id: The SMTP account identifier. Falls back to "default"
                if empty or None.
        """
        self.deferred.labels(account_id=account_id or "default").inc()

    def inc_rate_limited(self, account_id: str) -> None:
        """Increment the rate-limited counter for an account.

        Args:
            account_id: The SMTP account identifier. Falls back to "default"
                if empty or None.
        """
        self.rate_limited.labels(account_id=account_id or "default").inc()

    def set_pending(self, value: int) -> None:
        """Set the pending messages gauge to a specific value.

        Args:
            value: The current number of messages awaiting delivery.
        """
        self.pending.set(value)

    def generate_latest(self) -> bytes:
        """Export all metrics in Prometheus text exposition format.

        Returns:
            Byte string containing all metrics in Prometheus format,
            suitable for HTTP response to a Prometheus scraper.
        """
        return generate_latest(self.registry)
