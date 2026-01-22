
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
   SQLite via aiosqlite. The database stores messages, accounts, tenants, and
   send logs. Use a persistent volume in production.

Configuration
-------------

**How do I configure the service?**
   Via ``config.ini`` file or environment variables prefixed with ``GMP_``.
   Environment variables take precedence. See :doc:`usage` for details.

**What's the difference between config.ini and environment variables?**
   They're equivalent. Use ``config.ini`` for local development and environment
   variables for Docker/Kubernetes deployments. All ``GMP_`` prefixed variables
   map to config sections (e.g., ``GMP_DB_PATH`` → ``[storage] db_path``).

**How do I secure the API?**
   Set ``api_token`` in config or ``GMP_API_TOKEN`` environment variable. All
   requests must include the ``X-API-Token`` header with this value.

**Can I run multiple instances?**
   Yes, but they must share the same SQLite database file. For high availability,
   consider using a shared volume or migrating to PostgreSQL (future feature).

Messages and Delivery
---------------------

**What happens if SMTP delivery fails?**
   The message is retried with exponential backoff. After ``max_retries`` attempts,
   it's marked as error and reported to your application via ``proxy_sync``.

**How does priority work?**
   Messages are processed in priority order (0=immediate, 1=high, 2=medium, 3=low).
   Within the same priority, older messages are sent first (FIFO).

**Can I schedule messages for future delivery?**
   Yes. Set ``deferred_ts`` to a Unix timestamp. The message won't be sent until
   that time.

**How do I know if a message was delivered?**
   The service posts delivery reports to your ``client_sync_url`` endpoint. Each
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
   - **Filesystem**: Absolute or relative paths

**How do I attach a file from my server?**
   Use an HTTP endpoint that returns the file content::

      {
        "filename": "report.pdf",
        "storage_path": "doc_id=123",
        "fetch_mode": "endpoint"
      }

   Configure the endpoint in ``[attachments] http_endpoint``.

**What's the MD5 cache marker?**
   Include ``{MD5:hash}`` in the filename to enable caching::

      report_{MD5:a1b2c3d4e5f6}.pdf

   If the content is already cached with that hash, it won't be fetched again.
   The marker is removed from the final filename.

**How do I configure attachment caching?**
   Set ``cache_disk_dir`` in the ``[attachments]`` section. Small files go to
   memory cache, large files to disk. See :doc:`usage` for all cache options.

Multi-tenancy
-------------

**What is multi-tenancy?**
   The ability to serve multiple organizations (tenants) from a single instance.
   Each tenant can have its own SMTP accounts and delivery report endpoint.

**How do I create a tenant?**
   POST to ``/tenant`` with the tenant configuration::

      {
        "id": "tenant-acme",
        "name": "ACME Corp",
        "client_sync_url": "https://acme.com/mail-reports"
      }

**Can tenants share SMTP accounts?**
   No. Each account belongs to one tenant (via ``tenant_id``). This ensures
   isolation and separate rate limiting.

**How are delivery reports routed?**
   Reports are sent to the tenant's ``client_sync_url`` if configured, otherwise
   to the global ``client_sync_url``.

Rate Limiting
-------------

**How does rate limiting work?**
   Each SMTP account can have limits per minute, hour, and day. When a limit is
   reached, messages are deferred (not rejected) and retried later.

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
   - ``asyncmail_sent_total{account_id}``: Successfully sent messages
   - ``asyncmail_errors_total{account_id}``: Failed messages
   - ``asyncmail_deferred_total{account_id}``: Rate-limited messages
   - ``asyncmail_rate_limited_total{account_id}``: Rate limit hits
   - ``asyncmail_pending_messages``: Current queue size

**How do I access metrics?**
   GET ``/metrics`` returns Prometheus exposition format. No authentication
   required for this endpoint (configure your firewall appropriately).

**How do I check service health?**
   GET ``/status`` returns ``{"ok": true}`` if the service is running.

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
   1. Verify ``client_sync_url`` is configured and reachable
   2. Check authentication (``client_sync_user``/``password`` or ``token``)
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

**Memory usage is high**
   Large attachments are held in memory. Mitigations:

   - Enable disk cache (``cache_disk_dir``)
   - Use smaller ``cache_memory_max_mb``
   - Avoid base64 for large files; use HTTP endpoints instead
