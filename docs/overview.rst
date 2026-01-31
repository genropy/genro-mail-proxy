Overview
========

This page summarises how the mail proxy service fits together and the path
followed by each message.

High-level architecture
-----------------------

The service is composed of the following building blocks:

* **MailProxy** – orchestrates scheduling, rate limiting, persistence and
  delivery.  It exposes a coroutine-based API (`handle_command`) used by the
  HTTP layer.
* **REST API** – defined in :mod:`core.mail_proxy.api_base`, built with FastAPI
  and protected by the ``X-API-Token`` header.
* **AttachmentManager** – fetches attachments from multiple sources (HTTP endpoints,
  URLs, base64, filesystem) with optional MD5-based caching.
* **MailProxyDb** – stores tenants, SMTP accounts, the unified ``messages`` table, and
  send logs in SQLite or PostgreSQL.
* **RateLimiter** – inspects send logs to determine whether a message needs to
  be deferred.
* **SMTPPool** – maintains pooled SMTP connections with acquire/release semantics
  for efficient connection reuse.
* **TieredCache** – two-level cache (memory + disk) for attachment content,
  using MD5 hash as the key for content-addressable deduplication.
* **LargeFileStorage** – optional module for uploading large attachments to
  external storage (S3, GCS, Azure, local filesystem) and replacing them with
  download links. Requires ``pip install genro-mail-proxy[large-files]``.
* **Metrics** – :class:`tools.prometheus.metrics.MailMetrics` exports
  Prometheus counters and gauges with the ``gmp_`` prefix.

.. mermaid::
   :caption: Logical architecture of genro-mail-proxy

   graph TD
     Client["REST Clients"] -->|JSON commands| API[FastAPI layer]
     API --> Core[MailProxy]
     Core --> DB[(SQLite/PostgreSQL<br/>tenants, accounts, messages)]
     Core --> RateLimiter[RateLimiter]
     RateLimiter --> DB
     Core --> Pool[SMTPPool]
     Pool --> SMTP[SMTP Server]
     Core --> Attachments[AttachmentManager]
     Attachments --> External["HTTP/Filesystem/Base64"]
     Core --> Metrics[Prometheus exporter]
     Core --> Sync["Client sync (delivery reports)"]
     Sync --> Upstream["Tenant servers"]
     Metrics --> Prometheus["Prometheus server"]

Request flow
------------

1. A client issues ``/commands/add-messages`` with one or more payloads.  The
   API dependency validates ``X-API-Token`` before dispatching to
   :meth:`MailProxy.handle_command`.
2. ``MailProxy`` validates each message (mandatory ``id``, sender, recipients,
   known account, etc.).  Accepted messages are written to the ``messages`` table
   with ``priority`` (default ``2``) and optional ``deferred_ts``; rejected ones
   are reported back with the associated reason.
3. The SMTP dispatch loop repeatedly queries ``messages`` for entries lacking
   ``sent_ts``/``error_ts`` whose ``deferred_ts`` is in the past.  Rate limiting
   can reschedule the delivery by updating ``deferred_ts``.
4. Delivery uses :mod:`aiosmtplib` via :class:`core.mail_proxy.smtp.pool.SMTPPool`
   so repeated sends within the same asyncio task can reuse the connection.
5. Delivery results are buffered in the ``messages`` table (``sent_ts`` /
   ``error_ts`` / ``error``) and streamed to API consumers through
   :meth:`MailProxy.results`.

.. mermaid::
   :caption: Message delivery sequence

   sequenceDiagram
     participant Client
     participant API as FastAPI
     participant Core as MailProxy
     participant DB as SQLite/PostgreSQL
     participant SMTP as SMTP Server

     Client->>API: POST /commands/add-messages
     API->>Core: handle_command("addMessages")
     Core->>DB: INSERT into messages
     loop Background SMTP loop
       Core->>DB: SELECT ready messages
       Core->>SMTP: send_message()
       alt Success
         Core->>DB: UPDATE sent_ts
       else Error
         Core->>DB: UPDATE error_ts / error
       end
     end
     Core->>API: results queue / delivery report
     API-->>Client: Deferred status or polling

Client synchronisation
----------------------

The client report loop periodically performs a ``POST`` using
the tenant's ``client_sync_url`` (built from ``base_url`` + ``client_sync_path``)
whenever there are rows in
``messages`` with ``sent_ts`` / ``error_ts`` / ``deferred_ts`` but no
``reported_ts``.  The body contains a ``delivery_report`` array with the
current lifecycle state for each message.  Once the upstream service confirms
reception (for example returning ``{"sent": 12, "error": 1, "deferred": 3}``)
the dispatcher stamps ``reported_ts`` and eventually purges those rows when
they age past the configured retention window.

Large file handling
-------------------

When an attachment exceeds a configured size threshold, the proxy can
automatically upload it to external storage and replace it with a download
link in the email body. This prevents memory exhaustion and SMTP size limits.

The feature is configured per-tenant via ``large_file_config``:

.. code-block:: json

   {
     "large_file_config": {
       "enabled": true,
       "max_size_mb": 10,
       "storage_url": "s3://bucket/mail-attachments",
       "action": "rewrite"
     }
   }

The ``action`` field controls behavior:

* ``warn`` – Log a warning but send the attachment normally (default)
* ``reject`` – Reject the message with an error
* ``rewrite`` – Upload to storage and replace with a download link

Storage backends are provided via `fsspec <https://filesystem-spec.readthedocs.io/>`_:

* **S3/MinIO**: ``s3://bucket/path``
* **Google Cloud Storage**: ``gs://bucket/path``
* **Azure Blob**: ``az://container/path``
* **Local filesystem**: ``file:///var/www/downloads`` (requires ``public_base_url``)

For cloud storage, presigned URLs are generated automatically. For local
filesystem, a signed token URL is generated using ``public_base_url``.

Attachment caching
------------------

The :class:`core.mail_proxy.smtp.cache.TieredCache` provides two-level
caching for attachment content:

* **Level 1 (Memory)**: Fast LRU cache with configurable TTL and size limit
* **Level 2 (Disk)**: Persistent cache for larger files

Files are stored using their MD5 hash as the key, enabling deduplication
across different storage paths. Filenames can include an MD5 marker
(``report_{MD5:abc123}.pdf``) for explicit cache lookup.

Configuration via environment variables::

  GMP_CACHE_DISK_DIR=/var/cache/attachments
  GMP_CACHE_MEMORY_MAX_MB=50
  GMP_CACHE_DISK_MAX_MB=500
