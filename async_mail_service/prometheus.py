"""Prometheus metrics exposed by the mail dispatcher."""

from prometheus_client import Counter, Gauge, CollectorRegistry, generate_latest

class MailMetrics:
    """Wrapper around the Prometheus registry used by the service."""

    def __init__(self, registry: CollectorRegistry | None = None):
        """Create counters and gauges inside the provided registry."""
        self.registry = registry or CollectorRegistry()
        self.sent = Counter("gmp_sent_total", "Total sent emails", ["account_id"], registry=self.registry)
        self.errors = Counter("gmp_errors_total", "Total send errors", ["account_id"], registry=self.registry)
        self.deferred = Counter("gmp_deferred_total", "Total deferred emails", ["account_id"], registry=self.registry)
        self.rate_limited = Counter("gmp_rate_limited_total", "Total rate limited occurrences", ["account_id"], registry=self.registry)
        self.pending = Gauge("gmp_pending_messages", "Current pending messages", registry=self.registry)

    def inc_sent(self, account_id: str):
        """Increase the ``sent`` counter for the given account."""
        self.sent.labels(account_id=account_id or "default").inc()

    def inc_error(self, account_id: str):
        """Increase the ``errors`` counter for the given account."""
        self.errors.labels(account_id=account_id or "default").inc()

    def inc_deferred(self, account_id: str):
        """Increase the ``deferred`` counter for the given account."""
        self.deferred.labels(account_id=account_id or "default").inc()

    def inc_rate_limited(self, account_id: str):
        """Increase the ``rate_limited`` counter for the given account."""
        self.rate_limited.labels(account_id=account_id or "default").inc()

    def set_pending(self, value: int):
        """Update the gauge tracking pending messages."""
        self.pending.set(value)

    def generate_latest(self) -> bytes:
        """Return the latest metrics snapshot in Prometheus text format."""
        return generate_latest(self.registry)
