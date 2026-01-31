Protocols and APIs
==================

This page consolidates the information required to integrate with genro-mail-proxy,
covering both the REST command surface and the outbound
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
     - See :class:`core.mail_proxy.entities.account.endpoint.AccountEndpoint`
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
           {"filename": "report.pdf", "storage_path": "/data/docs/report.pdf", "fetch_mode": "filesystem"}
         ]
       }
     ],
     "default_priority": 1
   }

Each entry mirrors the message payload schema. Key fields:

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

Each attachment requires a ``storage_path`` field with the location or data.
The ``fetch_mode`` field is **optional** - when omitted, it is automatically
inferred from the ``storage_path`` format.

.. list-table::
   :header-rows: 1

   * - fetch_mode
     - storage_path example
     - Description
   * - ``base64``
     - ``base64:SGVsbG8=``
     - Inline base64-encoded content (requires ``base64:`` prefix for auto-detection)
   * - ``filesystem``
     - ``/tmp/attachments/file.pdf``
     - Local filesystem path (absolute or relative to ``base_dir``)
   * - ``endpoint``
     - ``doc_id=123&version=2``
     - HTTP POST to tenant's attachment endpoint with params as body
   * - ``http_url``
     - ``https://storage.example.com/file.pdf``
     - HTTP GET from external URL

**Auto-detection rules** (when ``fetch_mode`` is omitted):

1. Starts with ``base64:`` → **base64** (prefix is stripped)
2. Starts with ``http://`` or ``https://`` → **http_url**
3. Starts with ``/`` → **filesystem**
4. Otherwise → **endpoint** (default)

**MD5 cache marker**: Filenames can include an MD5 hash marker for cache lookup:

.. code-block:: text

   report_{MD5:a1b2c3d4e5f6}.pdf

The marker is extracted for cache lookup and removed from the final filename.

Example attachment payload:

.. code-block:: json

   {
     "attachments": [
       {"filename": "logo.png", "storage_path": "base64:iVBORw0KGgo..."},
       {"filename": "invoice.pdf", "storage_path": "doc_id=456"},
       {"filename": "remote.pdf", "storage_path": "https://cdn.example.com/file.pdf"},
       {"filename": "local.txt", "storage_path": "/var/attachments/local.txt"}
     ]
   }

Note: ``fetch_mode`` is omitted in the example above because it is auto-detected
from the ``storage_path`` format. You can still specify it explicitly if needed.

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

The proxy implements a **bidirectional sync protocol** that allows the client
to both receive delivery reports AND submit new messages to send. This design
enables efficient batch processing without requiring the client to poll.

The "client report loop" sends ``POST`` requests to the configured
sync endpoint (per-tenant: ``client_base_url`` + ``client_sync_path``, or global: ``GMP_CLIENT_SYNC_URL``).
Authentication uses either HTTP basic auth or a bearer token (configured
per-tenant via CLI or environment variables).

Sync request format
~~~~~~~~~~~~~~~~~~~

The proxy sends a POST request with the following JSON body:

.. code-block:: json

   {
     "delivery_report": [
       {
         "id": "MSG-001",
         "sent_ts": 1728470500
       },
       {
         "id": "MSG-002",
         "error_ts": 1728470501,
         "error": "Connection refused"
       },
       {
         "id": "MSG-003",
         "pec_event": "pec_acceptance",
         "pec_ts": 1728470502,
         "pec_details": "Accepted by provider"
       }
     ]
   }

The ``delivery_report`` array contains status updates for messages. Each entry
includes the message ``id`` plus event-specific fields:

.. list-table::
   :header-rows: 1

   * - Event Type
     - Fields
     - Description
   * - Sent
     - ``sent_ts``
     - Message delivered successfully
   * - Error
     - ``error_ts``, ``error``
     - Permanent delivery failure
   * - Deferred
     - ``deferred_ts``, ``deferred_reason``
     - Temporary failure, will retry
   * - Bounce
     - ``bounce_ts``, ``bounce_type``, ``bounce_code``, ``bounce_reason``
     - Bounce detected from DSN
   * - PEC events
     - ``pec_event``, ``pec_ts``, ``pec_details``
     - PEC acceptance/delivery/error

Sync response format
~~~~~~~~~~~~~~~~~~~~

The client **must** respond with a JSON object containing at minimum an ``ok``
field. The ``queued`` field enables the accelerated sync loop:

.. code-block:: json

   {
     "ok": true,
     "queued": 15
   }

Response fields:

.. list-table::
   :header-rows: 1

   * - Field
     - Type
     - Required
     - Description
   * - ``ok``
     - bool
     - Yes
     - ``true`` if reports were processed successfully
   * - ``queued``
     - int
     - No
     - Number of messages the client has ready to send
   * - ``next_sync_after``
     - int
     - No
     - Unix timestamp. Proxy will not sync this tenant until this time (Do Not Disturb).
   * - ``error``
     - list[str]
     - No
     - Message IDs that failed to process
   * - ``not_found``
     - list[str]
     - No
     - Message IDs not found in client database

Do Not Disturb (Sync Scheduling)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Tenants can control when the proxy calls them by including a ``next_sync_after``
field in their sync response:

.. code-block:: json

   {
     "ok": true,
     "queued": 0,
     "next_sync_after": 1706450400
   }

When ``next_sync_after`` is provided:

- The proxy will not call this tenant until the specified Unix timestamp
- Useful for serverless databases (Neon, PlanetScale) to avoid cold-start costs during idle hours
- If the tenant has pending events to report, the proxy will still call them regardless of DND
- The tenant can override DND by calling ``POST /commands/run-now`` with their tenant token

If ``next_sync_after`` is omitted, the proxy uses the current time, meaning
the tenant will be called again after the normal sync interval (5 minutes).

**Example: Night-time DND**

A tenant with a serverless database wants to avoid cold-starts between 11 PM and 7 AM:

.. code-block:: python

   from datetime import datetime, time as dtime

   def calculate_next_sync():
       now = datetime.now()
       # If between 23:00 and 07:00, set next_sync to 07:00
       if now.time() >= dtime(23, 0) or now.time() < dtime(7, 0):
           tomorrow_7am = now.replace(hour=7, minute=0, second=0, microsecond=0)
           if now.time() >= dtime(23, 0):
               tomorrow_7am += timedelta(days=1)
           return int(tomorrow_7am.timestamp())
       return None  # Use default interval

   @app.post("/proxy_sync")
   async def proxy_sync(request: Request):
       # ... process reports ...
       return {
           "ok": True,
           "queued": pending_count,
           "next_sync_after": calculate_next_sync()
       }

Tenant Starvation Prevention
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The proxy ensures all tenants are contacted periodically, even those without
pending events to report. Every 5 minutes (default sync interval), the proxy
calls each tenant's sync endpoint regardless of whether there are delivery
reports to send.

This prevents "starvation" where a tenant without events is never called while
other tenants with constant activity monopolize the sync cycles.

Accelerated sync loop
~~~~~~~~~~~~~~~~~~~~~

When the client responds with ``queued > 0``, the proxy **immediately**
initiates another sync cycle without waiting for the normal interval
(default: 5 minutes). This creates an efficient message submission flow:

1. Proxy calls sync endpoint with delivery reports
2. Client processes reports and checks its outbox
3. Client responds with ``{"ok": true, "queued": N}`` where N = pending messages
4. Client calls ``POST /commands/add-messages`` with a batch of messages
5. **If queued > 0**: Proxy immediately calls sync again (goto step 1)
6. **If queued == 0**: Proxy waits for next interval

This design allows the client to submit messages in controlled batches while
the proxy orchestrates the timing.

.. code-block:: text

   Bidirectional Sync Flow
   =======================

   ┌───────────┐                              ┌────────────────┐
   │   Proxy   │                              │     Client     │
   └─────┬─────┘                              └───────┬────────┘
         │                                            │
         │  1. POST /sync {delivery_report: [...]}    │
         │ ─────────────────────────────────────────► │
         │                                            │
         │                          Process reports,  │
         │                          check outbox      │
         │                                            │
         │  2. Response {ok: true, queued: 10}        │
         │ ◄───────────────────────────────────────── │
         │                                            │
         │                                            │  3. POST /add-messages
         │ ◄───────────────────────────────────────── │     (batch of 5)
         │                                            │
         │  ┌─────────────────────────────────────┐   │
         │  │ queued > 0 → immediate resync       │   │
         │  └─────────────────────────────────────┘   │
         │                                            │
         │  4. POST /sync {delivery_report: [...]}    │
         │ ─────────────────────────────────────────► │
         │                                            │
         │  5. Response {ok: true, queued: 5}         │
         │ ◄───────────────────────────────────────── │
         │                                            │
         │                                            │  6. POST /add-messages
         │ ◄───────────────────────────────────────── │     (batch of 5)
         │                                            │
         │  7. POST /sync {delivery_report: [...]}    │
         │ ─────────────────────────────────────────► │
         │                                            │
         │  8. Response {ok: true, queued: 0}         │
         │ ◄───────────────────────────────────────── │
         │                                            │
         │  ┌─────────────────────────────────────┐   │
         │  │ queued == 0 → wait for interval     │   │
         │  └─────────────────────────────────────┘   │
         │                                            │
         ▼                                            ▼

Client implementation example
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A typical client sync endpoint handler:

.. code-block:: python

   from flask import Flask, request, jsonify
   import httpx

   app = Flask(__name__)
   PROXY_URL = "http://mailproxy:8000"
   BATCH_SIZE = 100

   @app.route("/proxy_sync", methods=["POST"])
   def proxy_sync():
       data = request.json
       reports = data.get("delivery_report", [])

       # 1. Process delivery reports
       for report in reports:
           msg_id = report["id"]
           if "sent_ts" in report:
               mark_as_sent(msg_id, report["sent_ts"])
           elif "error_ts" in report:
               mark_as_failed(msg_id, report["error"])
           elif "pec_event" in report:
               handle_pec_event(msg_id, report)

       # 2. Check outbox for pending messages
       pending = get_pending_messages(limit=BATCH_SIZE)
       queued_count = count_total_pending()

       # 3. Submit batch to proxy (async, don't block response)
       if pending:
           submit_to_proxy(pending)

       # 4. Return queued count to trigger accelerated sync
       return jsonify({"ok": True, "queued": queued_count})

   def submit_to_proxy(messages):
       """Submit messages to proxy API."""
       httpx.post(
           f"{PROXY_URL}/commands/add-messages",
           json={"messages": messages},
           headers={"X-API-Token": TENANT_TOKEN}
       )

Error handling
--------------

* Validation failures return ``HTTP 400`` with a body similar to
  ``{"detail": {"error": "...", "rejected": [...]}}``.
* Authentication errors produce ``HTTP 401``.
* Unknown commands return ``{"ok": false, "error": "unknown command"}``.

When the upstream client responds with an error the dispatcher leaves
``reported_ts`` unset so the results are retried on the next loop.
