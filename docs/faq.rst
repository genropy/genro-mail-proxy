
FAQ
===

Frequently asked questions about genro-mail-proxy.

General
-------

**What is genro-mail-proxy?**
   A microservice that decouples email sending from your application. Instead of
   connecting directly to SMTP servers, your app sends messages to the proxy via
   REST API, and the proxy handles delivery with retry, rate limiting, and reporting.

**Why use a mail proxy instead of direct SMTP?**
   - **Speed**: Non-blocking async operations (32ms vs 620ms for direct SMTP)
   - **Reliability**: Messages are persisted and retried automatically
   - **Rate limiting**: Shared limits across all application instances
   - **Monitoring**: Centralized Prometheus metrics
   - **Priority queuing**: Urgent messages are sent first

**What Python versions are supported?**
   Python 3.10 and later.

**What database does it use?**
   SQLite (via aiosqlite) or PostgreSQL. The database stores messages, accounts,
   tenants, and send logs. Use a persistent volume in production.

Configuration
-------------

**How do I configure the service?**
   Use the ``mail-proxy`` CLI to configure instances, tenants, and accounts
   interactively. For Docker deployments, use environment variables prefixed
   with ``GMP_``. See :doc:`usage` for details.

**What's the CLI workflow?**
   Start an instance with ``mail-proxy start myserver``, add tenants with
   ``mail-proxy myserver tenants add``, and configure SMTP accounts with
   ``mail-proxy myserver <tenant> accounts add``. All commands support
   interactive prompts for easy setup.

**How do I secure the API?**
   The service uses a two-tier authentication model:

   - **Admin Token** (``GMP_API_TOKEN``): Full access to all endpoints. Generated
     automatically when creating an instance. View it with ``mail-proxy myserver token``.
   - **Tenant Token**: Scoped access to a tenant's own resources only. Generated
     automatically when creating a tenant via API or CLI.

   All requests must include the ``X-API-Token`` header. Admin endpoints (tenant
   management, instance config) require the admin token; tenant-scoped endpoints
   accept either token type.

**What's the difference between admin and tenant tokens?**
   The **admin token** (global) grants full access: create/delete tenants, manage
   all accounts, configure the instance, and access any tenant's messages.

   **Tenant tokens** are scoped to their own resources: they can only manage
   their own accounts, send messages, and query their own message history.
   Tenant tokens cannot access other tenants' data or perform admin operations.

   See :doc:`multi_tenancy` for the complete authentication model.

**Can I run multiple instances?**
   Yes. Each instance has its own database at ``~/.mail-proxy/<name>/``.
   Instances are independent and can run on different ports.

Messages and Delivery
---------------------

**What happens if SMTP delivery fails?**
   The message is retried with exponential backoff. After ``max_retries`` attempts,
   it's marked as error and reported to your application via ``proxy_sync``.

**How does priority work?**
   Messages are processed in priority order (0=immediate, 1=high, 2=medium, 3=low).
   Within the same priority, older messages are sent first (FIFO).
   See :doc:`priority_queuing` for use cases and examples.

**Can I schedule messages for future delivery?**
   Yes. Set ``deferred_ts`` to a Unix timestamp. The message won't be sent until
   that time.

**How do I know if a message was delivered?**
   The service posts delivery reports to your sync endpoint. Each
   report includes ``sent_ts`` (success) or ``error_ts`` + ``error`` (failure).

**What's the maximum message size?**
   There's no hard limit, but large attachments impact memory. Use HTTP endpoints
   or filesystem paths for large files instead of base64 encoding.

Attachments
-----------

**What attachment sources are supported?**
   - **base64**: Inline encoded content (``fetch_mode: "base64"``)
   - **HTTP endpoint**: POST to configured URL (``fetch_mode: "endpoint"``)
   - **HTTP URL**: Direct URL fetch (``fetch_mode: "http_url"``)
   - **Filesystem**: Absolute or relative paths (``fetch_mode: "filesystem"``)

**How do I attach a file from my server?**
   Use an HTTP endpoint that returns the file content::

      {
        "filename": "report.pdf",
        "storage_path": "doc_id=123",
        "fetch_mode": "endpoint"
      }

   For per-tenant configuration, set ``client_base_url`` and ``client_attachment_path``
   on the tenant. For global configuration, use ``GMP_ATTACHMENT_BASE_URL``.

**What's the MD5 cache marker?**
   Include ``{MD5:hash}`` in the filename to enable caching::

      report_{MD5:a1b2c3d4e5f6}.pdf

   If the content is already cached with that hash, it won't be fetched again.
   The marker is removed from the final filename.

**How do I configure attachment caching?**
   Set the ``GMP_CACHE_DISK_DIR`` environment variable or configure it when
   starting the instance. Small files go to memory cache, large files to disk.
   See :doc:`usage` for all cache options.

**How do I handle large attachments?**
   Install the large-files extra: ``pip install genro-mail-proxy[large-files]``.
   Configure ``large_file_config`` on the tenant with ``storage_url`` pointing
   to S3, GCS, Azure, or a local filesystem. Set ``action: "rewrite"`` to
   automatically upload and replace with download links.

Multi-tenancy
-------------

**What is multi-tenancy?**
   The ability to serve multiple organizations (tenants) from a single instance.
   Each tenant can have its own SMTP accounts and delivery report endpoint.

**How do I create a tenant?**
   Use the CLI with interactive prompts::

      mail-proxy myserver tenants add

   Or with explicit arguments::

      mail-proxy myserver tenants add acme \
        --name "ACME Corp" \
        --base-url "https://acme.com" \
        --sync-path "/mail-proxy/sync"

   When a tenant is created, an API key is automatically generated and displayed
   **once**. Store it securely - it cannot be retrieved later. If lost, you can
   regenerate it with ``POST /tenant/{id}/api-key`` (requires admin token).

**How do I get a tenant's API key?**
   The API key is shown only once when creating the tenant. If you lose it,
   regenerate a new one using the admin token::

      curl -X POST https://mailproxy/tenant/acme/api-key \
        -H "X-API-Token: $ADMIN_TOKEN"

   The response contains the new ``api_key``. The old key is invalidated immediately.

**Can tenants share SMTP accounts?**
   No. Each account belongs to one tenant (via ``tenant_id``). This ensures
   isolation and separate rate limiting.

**How are delivery reports routed?**
   Reports are sent to the tenant's sync endpoint (``client_base_url`` + ``client_sync_path``)
   if configured, otherwise to the global ``GMP_CLIENT_SYNC_URL``.

Rate Limiting
-------------

**How does rate limiting work?**
   Each SMTP account can have limits per minute, hour, and day. When a limit is
   reached, messages are deferred (not rejected) and retried later.
   See :doc:`rate_limiting` for details.

**What happens when rate limited?**
   The message stays in queue with a ``deferred_ts`` timestamp. It will be
   retried when the rate limit window passes.

**Can I disable rate limiting?**
   Yes. Don't set any ``limit_per_*`` fields when creating the account, or set
   them to 0.

**Are rate limits per-instance or global?**
   Global. All instances sharing the same database see the same send counts.

Monitoring
----------

**What metrics are available?**
   - ``gmp_sent_total{account_id}``: Successfully sent messages
   - ``gmp_errors_total{account_id}``: Failed messages
   - ``gmp_deferred_total{account_id}``: Rate-limited messages
   - ``gmp_rate_limited_total{account_id}``: Rate limit hits
   - ``gmp_pending_messages``: Current queue size

   See :doc:`monitoring` for Prometheus configuration and Grafana dashboards.

**How do I access metrics?**
   GET ``/metrics`` returns Prometheus exposition format. No authentication
   required for this endpoint (configure your firewall appropriately).

**How do I check service health?**
   GET ``/health`` returns ``{"ok": true}`` if the service is running.
   GET ``/status`` (requires auth) returns detailed status including scheduler state.

Troubleshooting
---------------

**Messages aren't being sent**
   1. Check ``/status`` endpoint is responding
   2. Verify ``scheduler_active`` is true (or call ``/commands/activate``)
   3. Check SMTP account credentials with ``GET /accounts``
   4. Look for errors in logs or ``GET /messages``

**Rate limiting is too aggressive**
   Increase ``limit_per_minute``, ``limit_per_hour``, or ``limit_per_day`` on
   the SMTP account. Or add more accounts to distribute the load.

**Delivery reports aren't arriving**
   1. Verify ``client_base_url`` and ``client_sync_path`` are configured (or global ``GMP_CLIENT_SYNC_URL``)
   2. Check authentication in ``client_auth`` (bearer token or basic auth)
   3. Ensure your endpoint returns HTTP 200 with valid JSON

**Attachments fail to fetch**
   1. Check ``fetch_mode`` matches the ``storage_path`` format
   2. Verify HTTP endpoint is reachable (for ``endpoint`` mode)
   3. Check filesystem permissions (for absolute/relative paths)
   4. Increase ``attachment_timeout`` if fetches are timing out

**Database is locked**
   SQLite doesn't handle high concurrency well. Options:

   - Reduce ``batch_size_per_account``
   - Increase ``send_loop_interval``
   - Use a single instance instead of multiple
   - Switch to PostgreSQL: ``pip install genro-mail-proxy[postgresql]``

**Memory usage is high**
   Large attachments are held in memory. Mitigations:

   - Enable disk cache (``cache_disk_dir``)
   - Use smaller ``cache_memory_max_mb``
   - Avoid base64 for large files; use HTTP endpoints instead
