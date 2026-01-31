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

All counters are labeled by:
    - ``tenant_id``: Tenant identifier
    - ``tenant_name``: Human-readable tenant name
    - ``account_id``: SMTP account identifier
    - ``account_name``: Human-readable account name (defaults to account_id)

Example:
    Accessing metrics via the REST API::

        GET /metrics

    Returns Prometheus text format suitable for scraping.
"""

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    disable_created_metrics,
    generate_latest,
)

# Disable OpenMetrics _created timestamp gauges for cleaner output
disable_created_metrics()

# Label names for all counters
LABEL_NAMES = ["tenant_id", "tenant_name", "account_id", "account_name"]


class MailMetrics:
    """Prometheus metrics collector for the mail dispatcher.

    Encapsulates all Prometheus counters and gauges used to monitor email
    dispatch operations. Each metric is labeled by tenant and account info
    to enable per-tenant and per-account monitoring and alerting.

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
            LABEL_NAMES,
            registry=self.registry,
        )
        self.errors = Counter(
            "gmp_errors_total",
            "Total send errors",
            LABEL_NAMES,
            registry=self.registry,
        )
        self.deferred = Counter(
            "gmp_deferred_total",
            "Total deferred emails",
            LABEL_NAMES,
            registry=self.registry,
        )
        self.rate_limited = Counter(
            "gmp_rate_limited_total",
            "Total rate limited occurrences",
            LABEL_NAMES,
            registry=self.registry,
        )
        self.pending = Gauge(
            "gmp_pending_messages",
            "Current pending messages",
            registry=self.registry,
        )

    def _labels(
        self,
        tenant_id: str | None = None,
        tenant_name: str | None = None,
        account_id: str | None = None,
        account_name: str | None = None,
    ) -> dict[str, str]:
        """Build label dict with defaults."""
        tid = tenant_id or "default"
        aid = account_id or "default"
        return {
            "tenant_id": tid,
            "tenant_name": tenant_name or tid,
            "account_id": aid,
            "account_name": account_name or aid,
        }

    def inc_sent(
        self,
        tenant_id: str | None = None,
        tenant_name: str | None = None,
        account_id: str | None = None,
        account_name: str | None = None,
    ) -> None:
        """Increment the sent counter."""
        self.sent.labels(**self._labels(tenant_id, tenant_name, account_id, account_name)).inc()

    def inc_error(
        self,
        tenant_id: str | None = None,
        tenant_name: str | None = None,
        account_id: str | None = None,
        account_name: str | None = None,
    ) -> None:
        """Increment the error counter."""
        self.errors.labels(**self._labels(tenant_id, tenant_name, account_id, account_name)).inc()

    def inc_deferred(
        self,
        tenant_id: str | None = None,
        tenant_name: str | None = None,
        account_id: str | None = None,
        account_name: str | None = None,
    ) -> None:
        """Increment the deferred counter."""
        self.deferred.labels(**self._labels(tenant_id, tenant_name, account_id, account_name)).inc()

    def inc_rate_limited(
        self,
        tenant_id: str | None = None,
        tenant_name: str | None = None,
        account_id: str | None = None,
        account_name: str | None = None,
    ) -> None:
        """Increment the rate-limited counter."""
        self.rate_limited.labels(
            **self._labels(tenant_id, tenant_name, account_id, account_name)
        ).inc()

    def set_pending(self, value: int) -> None:
        """Set the pending messages gauge to a specific value.

        Args:
            value: The current number of messages awaiting delivery.
        """
        self.pending.set(value)

    def init_account(
        self,
        tenant_id: str | None = None,
        tenant_name: str | None = None,
        account_id: str | None = None,
        account_name: str | None = None,
    ) -> None:
        """Initialize all counters for an account with zero values.

        This ensures metrics appear in Prometheus output even before any
        actual email activity occurs. Prometheus counters with labels only
        appear in output after being incremented, so this method explicitly
        creates the label combinations with initial value 0.
        """
        labels = self._labels(tenant_id, tenant_name, account_id, account_name)
        self.sent.labels(**labels)
        self.errors.labels(**labels)
        self.deferred.labels(**labels)
        self.rate_limited.labels(**labels)

    def generate_latest(self) -> bytes:
        """Export all metrics in Prometheus text exposition format.

        Returns:
            Byte string containing all metrics in Prometheus format,
            suitable for HTTP response to a Prometheus scraper.
        """
        return generate_latest(self.registry)
