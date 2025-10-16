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
     - Trigger a one-off delivery/reporting cycle when the service runs with
       ``test_mode=True``
     - None
     - ``{"ok": true}`` or ``{"ok": false, "error": ...}``
   * - ``POST /commands/suspend`` / ``POST /commands/activate``
     - Toggle the scheduler
     - Optional JSON (unused)
     - ``{"ok": true, "active": <bool>}``
   * - ``POST /commands/rules`` / ``GET /commands/rules`` / ``PATCH /commands/rules/{id}`` / ``DELETE /commands/rules/{id}``
     - Manage scheduling rules
     - :class:`async_mail_service.api.RulePayload`
     - Updated ``rules`` list
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

† ``/commands/run-now`` is exposed purely for testing and maintenance. In
production the SMTP loop automatically drains the queue according to
``send_interval_seconds``.

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
     - Supports inline/base64, HTTP(S) URLs, or S3 references

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
