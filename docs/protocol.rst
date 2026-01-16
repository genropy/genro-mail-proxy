Protocols and APIs
==================

This page consolidates the information required to integrate with the async
mail service, covering both the REST command surface and the outbound
``proxy_sync`` communication channel.

Authentication and base URL
---------------------------

All REST calls are rooted at ``http://<host>:<port>`` (by default
``http://127.0.0.1:8000``). When an ``api_token`` is configured the client
**must** send the header ``X-API-Token: <value>`` or the request will be
rejected with ``401``.

Every command returns a JSON document with at least the keys ``ok`` and,
on failure, ``error``. Additional fields depend on the specific endpoint.

REST command surface
--------------------

.. list-table::
   :header-rows: 1

   * - Method & Path
     - Purpose
     - Request body
     - Response highlights
   * - ``GET /status``
     - Health probe
     - None
     - ``{"ok": true}``
   * - ``POST /commands/add-messages``
     - Queue one or more messages for delivery
     - :ref:`Message batch payload <message-batch>`
     - ``queued`` count and ``rejected`` array
* - ``POST /commands/run-now`` †
  - Wake the dispatcher/reporting loops to run a one-off cycle immediately
  - None
  - ``{"ok": true}`` or ``{"ok": false, "error": ...}``
   * - ``POST /commands/suspend`` / ``POST /commands/activate``
     - Toggle the scheduler
     - Optional JSON (unused)
     - ``{"ok": true, "active": <bool>}``
   * - ``POST /account`` / ``GET /accounts`` / ``DELETE /account/{id}``
     - Manage SMTP account definitions
     - :class:`async_mail_service.api.AccountPayload`
     - Confirmation plus account list
   * - ``POST /commands/delete-messages``
     - Remove messages from the queue
     - ``{"ids": ["msg-id", ...]}``
     - Numbers of ``removed`` and ``not_found`` entries
   * - ``GET /messages``
     - Inspect the queue
     - Query string ``active_only`` (optional)
     - Array of records mirroring the ``messages`` table
   * - ``GET /metrics``
     - Prometheus exposition endpoint
     - None
     - Text payload in Prometheus exposition format

† ``/commands/run-now`` wakes the dispatcher/reporting loops so they run
immediately, rather than waiting for the next ``send_interval_seconds`` window.
It is typically used during maintenance or tests, but is available in all modes.

.. _message-batch:

Message batch payload
---------------------

``POST /commands/add-messages`` accepts the following JSON structure:

.. code-block:: json

   {
     "messages": [
       {
         "id": "MSG-001",
         "account_id": "acc-1",
         "from": "sender@example.com",
         "to": ["dest@example.com"],
         "subject": "Hello",
         "body": "Plain text body",
         "content_type": "plain",
         "priority": 2,
         "deferred_ts": 1728470400,
         "attachments": [
           {"filename": "report.pdf", "s3": {"bucket": "docs", "key": "report.pdf"}}
         ]
       }
     ],
     "default_priority": 1
   }

Each entry mirrors :class:`async_mail_service.api.MessagePayload`. Key fields:

.. list-table::
   :header-rows: 1

   * - Field
     - Type
     - Required
     - Notes
   * - ``id``
     - ``str``
     - Yes
     - Unique identifier; duplicates are rejected
   * - ``account_id``
     - ``str``
     - No
     - SMTP account key; falls back to default account if omitted
   * - ``from``
     - ``str``
     - Yes
     - Envelope sender (also used as default ``return_path``)
   * - ``to`` / ``cc`` / ``bcc``
     - ``List[str]`` or comma-separated ``str``
     - ``to`` required
     - Recipient lists; empty sequences are rejected
   * - ``subject``
     - ``str``
     - Yes
     - MIME subject header
   * - ``body``
     - ``str``
     - Yes
     - Message body; ``content_type`` controls ``plain`` vs ``html``
   * - ``deferred_ts``
     - ``int``
     - No
     - Unix timestamp; delivery is postponed until this instant
   * - ``attachments``
     - ``List[Attachment]``
     - No
     - See :ref:`attachment-formats` for supported storage paths

.. _attachment-formats:

Attachment storage formats
--------------------------

Each attachment includes a ``storage_path`` field specifying where to fetch
the content. The following formats are supported:

.. list-table::
   :header-rows: 1

   * - Format
     - Example
     - Description
   * - ``base64:content``
     - ``base64:SGVsbG8=``
     - Inline base64-encoded content (always available)
   * - ``volume:path``
     - ``s3-uploads:docs/report.pdf``
     - genro-storage volume (requires genro-storage dependency)
   * - ``/absolute/path``
     - ``/tmp/attachments/file.pdf``
     - Local filesystem absolute path
   * - ``relative/path``
     - ``uploads/doc.pdf``
     - Filesystem relative to configured ``base_dir``
   * - ``@params``
     - ``@doc_id=123&version=2``
     - HTTP POST to default endpoint with params as body
   * - ``@[url]params``
     - ``@[https://api.example.com]id=456``
     - HTTP POST to specific URL with params as body

**MD5 cache marker**: Filenames can include an MD5 hash marker for cache lookup:

.. code-block:: text

   report_{MD5:a1b2c3d4e5f6}.pdf

The marker is extracted for cache lookup and removed from the final filename.
This is compatible with genro-storage and Genropy which use MD5 from S3 ETag.

Example attachment payload:

.. code-block:: json

   {
     "attachments": [
       {"filename": "report.pdf", "storage_path": "s3-uploads:documents/report.pdf"},
       {"filename": "logo.png", "storage_path": "base64:iVBORw0KGgo..."},
       {"filename": "invoice_{MD5:abc123}.pdf", "storage_path": "@doc_id=456"},
       {"filename": "local.txt", "storage_path": "/var/attachments/local.txt"}
     ]
   }

Delivery report payload
-----------------------

Once a message transitions to ``sent`` or ``error`` the dispatcher includes it
in the next delivery report. The structure matches the records returned by
``GET /messages``:

.. code-block:: json

   {
     "delivery_report": [
       {
         "id": "MSG-001",
         "account_id": "acc-1",
         "priority": 1,
         "sent_ts": 1728470500,
         "error_ts": null,
         "error": null,
         "deferred_ts": null
       }
     ]
   }

All timestamps are expressed in seconds since the Unix epoch (UTC). When both
``sent_ts`` and ``error_ts`` are ``null`` the entry represents a message that
was deferred by the rate limiter.

Client synchronisation protocol
-------------------------------

The "client report loop" sends ``POST`` requests to the configured
``client_sync_url`` (``[client]`` section in ``config.ini``). Authentication
uses either HTTP basic auth (``client_sync_user`` / ``client_sync_password``)
or a bearer token (``client_sync_token``). A typical exchange:

1. Dispatcher computes a batch of pending delivery results (respecting the
   configured batch size).
2. Dispatcher sends the JSON payload above to ``client_sync_url``.
3. Upstream service replies with an acknowledgment summarising the received
   items (for example ``{"sent": 12, "error": 1, "deferred": 3}``).
4. Dispatcher sets ``reported_ts`` on the acknowledged rows and eventually
   purges them when they exceed ``delivery_report_retention_seconds``.

.. mermaid::
   :caption: proxy_sync HTTP exchange

   sequenceDiagram
     participant Core as AsyncMailCore
     participant Upstream as Genropy / client

     Core->>Upstream: POST client_sync_url<br/>delivery_report array
     Upstream-->>Core: HTTP 200 + summary JSON
     Core->>Core: mark_reported() & retention cleanup

Error handling
--------------

* Validation failures return ``HTTP 400`` with a body similar to
  ``{"detail": {"error": "...", "rejected": [...]}}``.
* Authentication errors produce ``HTTP 401``.
* Unknown commands return ``{"ok": false, "error": "unknown command"}``.

When the upstream client responds with an error the dispatcher leaves
``reported_ts`` unset so the results are retried on the next loop.
