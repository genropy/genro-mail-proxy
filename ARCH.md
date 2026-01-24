# Architecture Decisions

This document records key architectural decisions for genro-mail-proxy.

## ADR-001: Mixin Pattern for Core Module Organization

**Date**: 2026-01-24

**Status**: Accepted

**Context**: The original `core.py` module grew to ~1900 lines, making it difficult to navigate and maintain. The module contained three distinct responsibilities:
1. MailProxy orchestration (lifecycle, public API, command handling)
2. SMTP dispatch (message sending, retry logic, attachment handling)
3. Client reporting (delivery reports, tenant sync)

**Decision**: Split `core.py` into a `core/` package using the mixin pattern:
- `core/proxy.py` - MailProxy class with orchestration logic
- `core/dispatcher.py` - DispatcherMixin for SMTP dispatch
- `core/reporting.py` - ReporterMixin for delivery reports
- `core/__init__.py` - Re-exports for backward compatibility

**Rationale**:
- Mixins keep all functionality in a single MailProxy class (no delegation overhead)
- Each file is focused and under 500 lines
- Import `from mail_proxy.core import MailProxy` continues to work
- TYPE_CHECKING imports avoid circular dependencies

**Consequences**:
- Positive: Better code organization, easier testing of individual concerns
- Positive: Backward compatible - no API changes
- Negative: Mixins share `self` namespace (mitigated by clear naming conventions)

## ADR-002: Async-First Design with Sync Wrappers

**Date**: 2025-01-01

**Status**: Accepted

**Context**: Email delivery is inherently I/O-bound. The service needs to handle many concurrent operations efficiently.

**Decision**: Use async/await throughout with aiohttp for HTTP and aiosmtplib for SMTP.

**Rationale**:
- Single-threaded event loop handles thousands of concurrent connections
- No thread synchronization complexity
- Natural fit for rate limiting and backpressure

**Consequences**:
- Positive: High throughput with low resource usage
- Negative: All I/O operations must be async (no blocking calls)

## ADR-003: SQLite as Default, PostgreSQL for Scale

**Date**: 2025-01-01

**Status**: Accepted

**Context**: The service needs persistent storage for messages, accounts, and tenants.

**Decision**: Use SQLite (via aiosqlite) as default with PostgreSQL (via psycopg3) as optional.

**Rationale**:
- SQLite: Zero configuration, single-file deployment, sufficient for most use cases
- PostgreSQL: Required for multi-instance deployments with shared state

**Consequences**:
- Positive: Easy getting started with SQLite
- Positive: Clear upgrade path to PostgreSQL when needed
- Negative: Two code paths for database operations (mitigated by DbAdapter abstraction)

## ADR-004: Priority Queue with FIFO Within Priority

**Date**: 2025-01-01

**Status**: Accepted

**Context**: Different messages have different urgency levels.

**Decision**: Four priority levels (0=immediate, 1=high, 2=medium, 3=low) with FIFO ordering within each level.

**Rationale**:
- Simple numeric ordering in SQL: `ORDER BY priority ASC, created_at ASC`
- Clear semantics for callers
- No complex scheduling algorithms needed

**Consequences**:
- Positive: Predictable behavior
- Positive: Efficient database queries
- Negative: Low-priority messages can starve under high load (acceptable trade-off)

## ADR-005: PUSH-Based Delivery Reports

**Date**: 2025-01-01

**Status**: Accepted

**Context**: Callers need to know when messages are delivered or fail.

**Decision**: The proxy pushes delivery reports to configured HTTP endpoints rather than requiring callers to poll.

**Rationale**:
- Lower latency for delivery notifications
- No polling overhead on callers
- Enables bidirectional sync (tenant can send new messages in response)

**Consequences**:
- Positive: Real-time notifications
- Positive: Enables "smart" sync where tenant queues new messages
- Negative: Requires callers to expose an HTTP endpoint

## ADR-006: Tenant Isolation via tenant_id

**Date**: 2025-06-01

**Status**: Accepted

**Context**: Multiple organizations need to share a single proxy instance.

**Decision**: All resources (accounts, messages) are scoped by `tenant_id`. Each tenant has separate:
- SMTP accounts
- Rate limits
- Delivery report endpoints
- Authentication credentials

**Rationale**:
- Single instance serves multiple tenants (cost-effective)
- Clear data isolation
- Independent configuration per tenant

**Consequences**:
- Positive: Multi-tenancy without separate deployments
- Positive: Tenant-specific rate limiting
- Negative: Additional complexity in queries and validation

## ADR-007: Attachment Fetch Mode Auto-Detection

**Date**: 2026-01-01

**Status**: Accepted

**Context**: Attachments can come from various sources (base64, HTTP, filesystem, endpoint).

**Decision**: The `fetch_mode` field is optional. When omitted, it's inferred from `storage_path`:
1. `base64:` prefix → base64
2. `http://` or `https://` prefix → http_url
3. `/` prefix → filesystem
4. Otherwise → endpoint (default)

**Rationale**:
- Reduces boilerplate in API calls
- Clear, predictable rules
- Explicit `fetch_mode` still supported when needed

**Consequences**:
- Positive: Simpler API usage
- Positive: Backward compatible (explicit mode still works)
- Negative: Magic behavior may confuse some users (mitigated by documentation)

## ADR-008: Connection Pooling with Lazy Acquisition

**Date**: 2025-01-01

**Status**: Accepted

**Context**: SMTP connections are expensive to establish and limited by server policies.

**Decision**: SMTPPool manages connections with:
- Lazy connection creation (on first use)
- Acquire/release semantics
- Per-account pooling with configurable limits
- Automatic cleanup of idle connections

**Rationale**:
- Reuse connections across messages (faster delivery)
- Respect server connection limits
- Clean resource management

**Consequences**:
- Positive: 10-50x faster delivery for batches
- Positive: Predictable resource usage
- Negative: Pool management complexity

## ADR-009: Exponential Backoff with Jitter for Retries

**Date**: 2025-01-01

**Status**: Accepted

**Context**: Transient SMTP failures should be retried, but not overwhelm servers.

**Decision**: RetryStrategy implements exponential backoff with:
- Configurable base delay and max delay
- Random jitter to prevent thundering herd
- Classification of transient vs permanent failures

**Rationale**:
- Standard industry practice for retry logic
- Jitter prevents synchronized retry storms
- Automatic recovery from transient issues

**Consequences**:
- Positive: Graceful handling of temporary failures
- Positive: Server-friendly retry behavior
- Negative: Messages may be delayed during retry windows

## ADR-010: Prometheus Metrics for Observability

**Date**: 2025-01-01

**Status**: Accepted

**Context**: Production deployments need monitoring and alerting.

**Decision**: Export Prometheus metrics at `/metrics` endpoint:
- `gmp_sent_total` - successful deliveries by account
- `gmp_errors_total` - failed deliveries by account
- `gmp_deferred_total` - rate-limited messages
- `gmp_pending_messages` - current queue depth

**Rationale**:
- Prometheus is the de facto standard for metrics
- Labels enable per-account dashboards
- Queue depth enables autoscaling decisions

**Consequences**:
- Positive: Standard observability
- Positive: Grafana dashboards out of the box
- Negative: Metrics endpoint must be secured in production
