from prometheus_client import Counter, Gauge, CollectorRegistry, generate_latest

class MailMetrics:
    def __init__(self, registry: CollectorRegistry | None = None):
        self.registry = registry or CollectorRegistry()
        self.sent = Counter("asyncmail_sent_total", "Total sent emails", ["account_id"], registry=self.registry)
        self.errors = Counter("asyncmail_errors_total", "Total send errors", ["account_id"], registry=self.registry)
        self.deferred = Counter("asyncmail_deferred_total", "Total deferred emails", ["account_id"], registry=self.registry)
        self.rate_limited = Counter("asyncmail_rate_limited_total", "Total rate limited occurrences", ["account_id"], registry=self.registry)
        self.pending = Gauge("asyncmail_pending_messages", "Current pending messages", registry=self.registry)

    def inc_sent(self, account_id: str): self.sent.labels(account_id=account_id or "default").inc()
    def inc_error(self, account_id: str): self.errors.labels(account_id=account_id or "default").inc()
    def inc_deferred(self, account_id: str): self.deferred.labels(account_id=account_id or "default").inc()
    def inc_rate_limited(self, account_id: str): self.rate_limited.labels(account_id=account_id or "default").inc()
    def set_pending(self, value: int): self.pending.set(value)
    def generate_latest(self) -> bytes: return generate_latest(self.registry)
