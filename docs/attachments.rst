
Attachments
===========

This guide covers all aspects of handling email attachments in genro-mail-proxy:
fetching from various sources, caching for deduplication, and handling large files.

Attachment specification
------------------------

Each attachment in a message requires at minimum a ``filename`` and ``storage_path``:

.. code-block:: json

   {
     "attachments": [
       {
         "filename": "report.pdf",
         "storage_path": "doc_id=123"
       }
     ]
   }

Full attachment fields:

.. list-table::
   :header-rows: 1
   :widths: 20 15 65

   * - Field
     - Required
     - Description
   * - filename
     - Yes
     - Display name for the attachment (max 255 chars)
   * - storage_path
     - Yes
     - Content location (format depends on fetch_mode)
   * - fetch_mode
     - No
     - How to retrieve content (inferred if not provided)
   * - mime_type
     - No
     - MIME type override (auto-detected from filename if not set)
   * - content_md5
     - No
     - MD5 hash for cache lookup (32-char hex string)
   * - auth
     - No
     - Authentication override for HTTP requests

Fetch modes
-----------

The ``fetch_mode`` field determines how the proxy retrieves attachment content.
If not specified, it is **inferred from the storage_path format**:

.. list-table::
   :header-rows: 1
   :widths: 15 30 55

   * - fetch_mode
     - storage_path format
     - Description
   * - endpoint
     - ``doc_id=123`` (default)
     - POST to tenant's ``client_attachment_path``
   * - http_url
     - ``https://...`` or ``http://...``
     - Direct GET from URL
   * - base64
     - ``base64:SGVsbG8=`` or raw base64
     - Inline encoded content
   * - filesystem
     - ``/var/attachments/file.pdf``
     - Read from local filesystem

**Inference rules** (when fetch_mode is omitted):

1. Starts with ``base64:`` → **base64** (prefix is stripped)
2. Starts with ``http://`` or ``https://`` → **http_url**
3. Starts with ``/`` → **filesystem**
4. Otherwise → **endpoint** (default)

endpoint mode
~~~~~~~~~~~~~

The proxy POSTs to the tenant's attachment endpoint with the ``storage_path`` value:

.. code-block:: json

   {
     "filename": "invoice.pdf",
     "storage_path": "doc_id=456&version=2",
     "fetch_mode": "endpoint"
   }

The proxy sends::

   POST {client_base_url}{client_attachment_path}
   Content-Type: application/json
   Authorization: Bearer {client_auth.token}

   {"storage_path": "doc_id=456&version=2"}

Your endpoint must return the raw file content with appropriate Content-Type.

http_url mode
~~~~~~~~~~~~~

Direct HTTP GET from the URL in ``storage_path``:

.. code-block:: json

   {
     "filename": "logo.png",
     "storage_path": "https://cdn.example.com/images/logo.png",
     "fetch_mode": "http_url"
   }

Since URLs start with ``http://`` or ``https://``, fetch_mode can be omitted:

.. code-block:: json

   {
     "filename": "logo.png",
     "storage_path": "https://cdn.example.com/images/logo.png"
   }

Use the ``auth`` field to override authentication for this specific request:

.. code-block:: json

   {
     "filename": "private.pdf",
     "storage_path": "https://api.example.com/files/123",
     "auth": {
       "method": "bearer",
       "token": "file-specific-token"
     }
   }

base64 mode
~~~~~~~~~~~

Inline base64-encoded content directly in the message:

.. code-block:: json

   {
     "filename": "small.txt",
     "storage_path": "SGVsbG8gV29ybGQh",
     "fetch_mode": "base64"
   }

Or with the ``base64:`` prefix (fetch_mode is inferred):

.. code-block:: json

   {
     "filename": "small.txt",
     "storage_path": "base64:SGVsbG8gV29ybGQh"
   }

.. warning::

   Base64 encoding increases payload size by ~33%. For files larger than a few KB,
   prefer ``endpoint`` or ``http_url`` modes to avoid bloating the message queue.

filesystem mode
~~~~~~~~~~~~~~~

Read directly from the local filesystem:

.. code-block:: json

   {
     "filename": "local-report.pdf",
     "storage_path": "/var/attachments/reports/2024/report.pdf",
     "fetch_mode": "filesystem"
   }

Since absolute paths start with ``/``, fetch_mode can be omitted:

.. code-block:: json

   {
     "filename": "local-report.pdf",
     "storage_path": "/var/attachments/reports/2024/report.pdf"
   }

The path can be:

- **Absolute**: ``/var/attachments/file.pdf``
- **Relative**: ``reports/file.pdf`` (resolved against configured ``base_dir``)

.. warning::

   Filesystem mode requires the proxy to have read access to the file path.
   For containerized deployments, ensure the volume is mounted correctly.

Caching and deduplication
-------------------------

The proxy supports MD5-based caching to avoid re-fetching identical content.
This is useful when the same attachment appears in multiple messages.

MD5 marker in filename (legacy)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Embed the MD5 hash directly in the filename:

.. code-block:: json

   {
     "filename": "report_{MD5:d41d8cd98f00b204e9800998ecf8427e}.pdf",
     "storage_path": "doc_id=123"
   }

The proxy:

1. Extracts the MD5 hash from the filename
2. Checks the cache for that hash
3. If found, uses cached content (skips fetch)
4. If not found, fetches and caches with computed MD5
5. Strips the marker from the final filename → ``report.pdf``

content_md5 field (recommended)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Provide the MD5 hash as a separate field:

.. code-block:: json

   {
     "filename": "report.pdf",
     "storage_path": "doc_id=123",
     "content_md5": "d41d8cd98f00b204e9800998ecf8427e"
   }

This is cleaner than embedding in the filename and provides the same caching benefit.

.. note::

   If both ``content_md5`` and filename marker are provided, ``content_md5`` takes precedence.

Cache behavior
~~~~~~~~~~~~~~

- **Cache hit**: Content returned immediately, fetch skipped
- **Cache miss**: Content fetched, then cached using its computed MD5
- **No MD5 provided**: Content fetched and cached (for future lookups by MD5)

The cache uses a two-tier architecture:

1. **Memory tier**: Fast LRU cache (configurable size)
2. **Disk tier**: Persistent storage for larger files

Large file handling
-------------------

Attachments exceeding a size threshold can be automatically uploaded to external
storage and replaced with download links. This prevents:

- Memory exhaustion during email building
- SMTP server size limits (Gmail: 25MB, Exchange: 10MB default)
- Slow email delivery due to large payloads

Installation
~~~~~~~~~~~~

.. code-block:: bash

   # Install the appropriate storage backend:
   pip install genro-mail-proxy[enterprise-s3]    # Amazon S3 / MinIO
   pip install genro-mail-proxy[enterprise-gcs]   # Google Cloud Storage
   pip install genro-mail-proxy[enterprise-azure] # Azure Blob Storage
   pip install genro-mail-proxy[enterprise]       # All cloud backends

Configuration
~~~~~~~~~~~~~

Large file handling is configured per-tenant:

.. code-block:: json

   {
     "large_file_config": {
       "enabled": true,
       "max_size_mb": 10,
       "storage_url": "s3://my-bucket/mail-attachments",
       "file_ttl_days": 30,
       "action": "rewrite"
     }
   }

**Fields:**

.. list-table::
   :header-rows: 1
   :widths: 25 15 60

   * - Field
     - Default
     - Description
   * - enabled
     - false
     - Enable large file handling
   * - max_size_mb
     - 10.0
     - Size threshold in megabytes
   * - storage_url
     - (required)
     - fsspec URL for storage backend
   * - public_base_url
     - (optional)
     - Required for filesystem storage
   * - file_ttl_days
     - 30
     - Days before files expire
   * - lifespan_after_download_days
     - (optional)
     - Days to keep after first download
   * - action
     - warn
     - Behavior when threshold exceeded

Actions
~~~~~~~

- **warn**: Log a warning but send the attachment normally
- **reject**: Reject the message with an error
- **rewrite**: Upload to storage and replace with download link

Storage backends
~~~~~~~~~~~~~~~~

The proxy uses `fsspec <https://filesystem-spec.readthedocs.io/>`_ for storage abstraction:

**S3 / MinIO**::

   storage_url: "s3://bucket-name/path/prefix"

Requires AWS credentials via environment or IAM role.

**Google Cloud Storage**::

   storage_url: "gs://bucket-name/path/prefix"

Requires ``GOOGLE_APPLICATION_CREDENTIALS`` environment variable.

**Azure Blob Storage**::

   storage_url: "az://container-name/path/prefix"

Requires Azure credentials via environment.

**Local filesystem**::

   storage_url: "file:///var/www/downloads/attachments"
   public_base_url: "https://files.example.com/attachments"

Requires ``public_base_url`` for generating download URLs. Files must be served
by a web server (nginx, Apache, etc.).

Download URLs
~~~~~~~~~~~~~

When ``action: rewrite`` is used:

- **Cloud storage**: Presigned URLs are generated automatically (S3, GCS, Azure)
- **Local filesystem**: Signed token URLs using ``public_base_url``

The email body is modified to include download links:

.. code-block:: html

   <hr>
   <p><strong>Attachments available for download:</strong></p>
   <ul>
     <li><a href="https://...presigned-url...">large-report.pdf</a> (15.2 MB)</li>
   </ul>
   <p><em>Links expire in 30 days.</em></p>

Best practices
--------------

1. **Use endpoint mode for dynamic content**: When attachments are generated on-demand
   or require authentication, use endpoint mode with your own API.

2. **Use http_url for CDN-hosted files**: Static assets already on a CDN can be
   fetched directly without proxying through your application.

3. **Avoid base64 for large files**: Base64 bloats the message queue by 33%.
   Use endpoint or http_url instead.

4. **Enable caching for repeated attachments**: If the same file appears in multiple
   messages (e.g., company logo), provide ``content_md5`` to avoid re-fetching.

5. **Configure large_file_config for production**: Prevent memory issues by enabling
   large file handling with appropriate thresholds for your SMTP provider.

6. **Use presigned URLs for security**: Cloud storage presigned URLs expire,
   preventing unauthorized long-term access to attachments.

See also
--------

- :doc:`protocol` - Complete message format specification
- :doc:`multi_tenancy` - Tenant configuration including attachment endpoints
- :doc:`api_reference` - REST API for managing tenants and messages
