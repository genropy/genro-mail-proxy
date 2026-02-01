Features
========

This document provides a comprehensive list of all features implemented in
genro-mail-proxy, organized by edition.

Editions
--------

genro-mail-proxy is available in two editions:

**Community Edition (CE)** - Apache 2.0 License
   Free for any use. Includes core functionality for message queuing, delivery,
   rate limiting, retry logic, security, and monitoring.

**Enterprise Edition (EE)** - BSL 1.1 License
   Free for testing, development, and non-production use. Production use requires
   a commercial license from Softwell S.r.l. Each version converts to Apache 2.0
   four years after its release (e.g., v0.7.0 → Apache 2.0 on 2030-01-25).

Community Edition Features
--------------------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Feature
     - Description
   * - :ref:`message-management`
     - Message queuing, listing, deletion, cleanup
   * - :ref:`attachments`
     - Multi-source fetching, caching
   * - :ref:`priority-scheduling`
     - Priority queuing, deferred delivery, batch grouping
   * - :ref:`rate-limiting`
     - Per-account sliding window rate limiting
   * - :ref:`retry-resilience`
     - Exponential backoff, error classification
   * - :ref:`smtp-connections`
     - Connection pooling, multiple accounts
   * - :ref:`delivery-reporting`
     - Delivery reports, client sync callbacks
   * - :ref:`database-persistence`
     - SQLite and PostgreSQL support
   * - :ref:`monitoring-metrics`
     - Prometheus metrics, health endpoints
   * - :ref:`concurrency-performance`
     - Parallel dispatch, concurrent attachment fetching
   * - :ref:`rest-api`
     - Complete REST API with authentication
   * - :ref:`cli-tool`
     - Command-line interface for management
   * - :ref:`configuration`
     - Environment variables, INI config files
   * - :ref:`security`
     - Credential encryption (AES-256-GCM), API authentication

Enterprise Edition Features
---------------------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Feature
     - Description
   * - :ref:`multi-tenancy`
     - Tenant isolation, per-tenant API tokens, batch suspension
   * - :ref:`bounce-detection`
     - IMAP bounce polling, DSN parsing, hard/soft classification
   * - :ref:`pec-support`
     - Italian certified email with receipt tracking
   * - :ref:`large-file-offloading`
     - Upload to S3/GCS/Azure, replace with download links

----

.. _message-management:

Message Management
------------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Message Queuing
     - Asynchronous message queuing with validation via ``POST /commands/add-messages``.
       Validates required fields (id, from, to, subject) and optional account verification.
   * - Message Listing
     - Retrieve queued messages with filters via ``GET /messages``.
       Supports ``tenant_id`` and ``active_only`` parameters.
   * - Message Deletion
     - Remove messages from queue via ``POST /commands/delete-messages``.
       Returns count of removed messages and unauthorized IDs.
   * - Message Cleanup
     - Automatic removal of reported messages beyond retention period via
       ``POST /commands/cleanup-messages``. Default retention: 7 days.

----

.. _multi-tenancy:

Multi-Tenancy
-------------

**License**: BSL 1.1

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Tenant Management
     - Full tenant isolation with dedicated SMTP accounts.
       API: ``POST/GET/PUT/DELETE /tenant``, ``GET /tenants``.
   * - Per-Tenant API Tokens
     - Dedicated API tokens per tenant with SHA-256 hashing and optional expiration.
       Tokens provide tenant-scoped access control.
   * - Batch Suspension
     - Suspend/resume message sending at tenant or batch level.
       API: ``POST /commands/suspend``, ``POST /commands/activate``.
       Supports selective batch suspension for campaign management.
   * - Tenant Sync Callback
     - HTTP callback to tenant for delivery reports.
       Configurable URL (``client_base_url + client_sync_path``) and
       authentication (bearer, basic, none).

----

.. _attachments:

Attachments
-----------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Multi-Source Fetching
     - Fetch attachments from 4 sources with auto-detection:
       ``base64`` (inline), ``http_url`` (direct HTTP), ``endpoint`` (tenant API),
       ``filesystem`` (local path).
   * - Two-Tier Caching
     - Memory + disk cache with configurable TTL and max size.
       MD5-based content deduplication. Automatic cleanup of expired entries.
   * - Large File Offloading
     - Upload large attachments to cloud storage (S3, GCS, Azure via fsspec).
       Configurable per tenant with threshold, storage URL, and action
       (warn/reject/rewrite).
   * - MD5 Deduplication
     - Deduplicate attachments via MD5 marker in filename
       (``filename_{MD5:hash}.ext``) or explicit ``content_md5`` field.
   * - Custom Authentication
     - Per-attachment auth override (none, bearer, basic).
       Falls back to tenant's ``client_auth`` if not specified.
   * - Concurrent Fetching
     - Parallel attachment fetching with memory pressure limiting.
       Configurable via ``max_concurrent_attachments``.

----

.. _priority-scheduling:

Priority & Scheduling
---------------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Priority Queuing
     - 4-level priority system (0=immediate, 1=high, 2=medium, 3=low).
       FIFO ordering within same priority level.
   * - Deferred Delivery
     - Schedule future delivery via Unix timestamp (``deferred_ts``).
       Messages held until scheduled time.
   * - Batch Grouping
     - Group messages by campaign identifier (``batch_code``).
       Enables selective suspension/activation of campaigns.
   * - Immediate Dispatch
     - Manual wake-up of dispatch loop via ``POST /commands/run-now``.
       Reduces latency for urgent messages.

----

.. _rate-limiting:

Rate Limiting
-------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Sliding Window Limiting
     - Per-account rate limiting with minute/hour/day granularity.
       Based on persistent send log for accuracy.
   * - Deferral Strategy
     - Configurable behavior when limit exceeded: ``defer`` (reschedule)
       or ``reject`` (fail immediately).
   * - Multi-Instance Safe
     - Rate limits shared across instances via database-backed send log.
       Works with SQLite and PostgreSQL locking.

----

.. _retry-resilience:

Retry & Resilience
------------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Exponential Backoff
     - Automatic retry with configurable backoff delays.
       Default: 5 retries with delays (1m, 5m, 15m, 1h, 2h).
   * - Error Classification
     - SMTP errors classified as temporary (retry) or permanent (fail).
       Temporary: 421, 450, 452. Permanent: 501, 530, 550+.
   * - Max Retries
     - Configurable maximum retry attempts before permanent failure.
       Default: 5 retries.

----

.. _smtp-connections:

SMTP Connections
----------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Connection Pooling
     - Reusable SMTP connections with acquire/release semantics.
       TTL-based connection expiration (default: 300s).
   * - Multiple Accounts
     - Support for multiple SMTP accounts per tenant.
       Per-account configuration: host, port, TLS, credentials, batch size.
   * - Default Configuration
     - Default SMTP settings for messages without explicit account.
       Configurable via environment or constructor parameters.

----

.. _delivery-reporting:

Delivery & Reporting
--------------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - SMTP Delivery
     - Actual email delivery via SMTP with error handling.
       Supports plain text, HTML, CC, BCC, reply-to, custom headers.
   * - Delivery Reports
     - Detailed delivery outcome reports with status, timestamp, error info.
       Statuses: sent, deferred, error.
   * - Client Sync Callback
     - HTTP POST notification to tenant with delivery reports.
       Retry on failure with 5-minute fallback loop.
   * - Custom Report Handler
     - Override callable for custom report delivery logic.
       Use case: webhooks, event buses, custom integrations.

----

.. _bounce-detection:

Bounce Detection
----------------

**License**: BSL 1.1

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - IMAP Bounce Polling
     - Automated polling of IMAP mailbox for bounce messages.
       Configurable interval, folder, and credentials per instance.
   * - DSN Parsing (RFC 3464)
     - Full parsing of Delivery Status Notification messages.
       Extracts diagnostic codes, remote MTA info, and original recipient.
   * - X-Genro-Mail-ID Header
     - Custom header injected in outgoing emails for bounce correlation.
       Links bounce notifications back to original message ID.
   * - Hard/Soft Classification
     - Automatic classification of bounces as permanent (hard) or temporary (soft).
       Based on SMTP status codes (5xx = hard, 4xx = soft).
   * - Bounce Fields in API
     - Messages include ``bounce_type``, ``bounce_reason``, ``bounce_ts`` fields.
       Bounce info included in delivery reports to client.
   * - Instance Configuration
     - Per-instance bounce receiver configuration via API.
       Endpoints: ``GET/PUT /instance/bounce``, ``POST /instance/bounce/reload``.

----

.. _database-persistence:

Database & Persistence
----------------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - SQLite Support
     - Embedded database for development and single-instance deployments.
       Zero configuration required.
   * - PostgreSQL Support
     - Production-grade database for high concurrency and clustering.
       Connection string via ``GMP_DB_PATH``.
   * - Message State Tracking
     - Complete state tracking: pending, deferred, sent, error.
       Timestamps: created_at, sent_ts, error_ts, reported_ts.

----

.. _monitoring-metrics:

Monitoring & Metrics
--------------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Prometheus Metrics
     - Standard Prometheus metrics via ``GET /metrics``.
       Counters: sent, errors, deferred, rate_limited. Gauge: pending.
   * - Health Endpoint
     - Service health check via ``GET /health``.
       Returns service status and version.
   * - Delivery Logging
     - Verbose delivery activity logging when enabled.
       Configurable via ``log_delivery_activity`` parameter.

----

.. _concurrency-performance:

Concurrency & Performance
-------------------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Parallel Dispatch
     - Concurrent message processing with semaphore limiting.
       Configurable: ``max_concurrent_sends``, ``max_concurrent_per_account``.
   * - Attachment Concurrency
     - Parallel attachment fetching with memory pressure control.
       Default: 3 concurrent fetches.
   * - Batch Processing
     - Efficient message batching per account.
       Configurable: ``batch_size_per_account``, ``message_queue_size``.

----

.. _rest-api:

REST API
--------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - FastAPI Backend
     - Modern async REST API with automatic OpenAPI documentation.
       All operations exposed via HTTP endpoints.
   * - Token Authentication
     - API authentication via ``X-API-Token`` header.
       Supports global token and per-tenant tokens.
   * - Validation Errors
     - Detailed validation error reporting with field-level details.
       HTTP 422 responses with actionable error messages.

----

.. _cli-tool:

CLI Tool
--------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Instance Management
     - List, start, stop proxy instances.
       Commands: ``mail-proxy list``, ``mail-proxy start <name>``.
   * - Interactive Mode
     - Prompted input for complex operations (tenant add, account add).
       Secure credential entry without command-line exposure.

----

.. _configuration:

Configuration
-------------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Environment Variables
     - Configuration via ``GMP_`` prefixed environment variables.
       Key vars: ``GMP_API_TOKEN``, ``GMP_DB_PATH``, ``GMP_ATTACHMENT_CACHE_*``.
   * - INI Config Files
     - File-based configuration for cache and advanced settings.
       Section: ``[attachment_cache]`` for memory/disk cache tuning.
   * - Start Active Mode
     - Option to start with scheduler immediately active.
       Parameter: ``start_active=True`` or ``GMP_START_ACTIVE=1``.

----

.. _security:

Security
--------

**License**: Apache 2.0

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Credential Encryption
     - SMTP passwords encrypted at rest using AES-256-GCM.
       Key loaded from ``MAIL_PROXY_ENCRYPTION_KEY`` env var or ``/run/secrets/encryption_key``.
   * - API Token Authentication
     - All endpoints protected by ``X-API-Token`` header.
       Tokens stored as SHA-256 hashes.
   * - TLS Support
     - SMTP connections support STARTTLS and direct SSL/TLS.
       Configurable per account.

See :doc:`security` for detailed configuration and best practices.

----

.. _pec-support:

PEC Support
-----------

**License**: BSL 1.1 (Enterprise Edition)

Italian certified email (Posta Elettronica Certificata) with receipt tracking.
See :doc:`pec` for details.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Receipt Tracking
     - Automatic parsing of PEC acceptance and delivery receipts.
   * - IMAP Polling
     - Dedicated IMAP polling for PEC mailboxes.
   * - Status Correlation
     - Link receipts back to original messages via custom headers.

----

.. _large-file-offloading:

Large File Offloading
---------------------

**License**: BSL 1.1 (Enterprise Edition)

Upload large attachments to cloud storage and replace with download links.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Feature
     - Description
   * - Cloud Storage
     - Support for S3, Google Cloud Storage, Azure Blob via fsspec.
   * - Size Threshold
     - Configurable maximum attachment size before offloading.
   * - Actions
     - ``warn`` (log only), ``reject`` (fail message), ``rewrite`` (upload and link).
   * - Per-Tenant Config
     - Each tenant can have different storage backends and thresholds.
