Multi-tenancy Architecture
==========================

This document describes the multi-tenant architecture of genro-mail-proxy,
including how to configure tenants and the bidirectional PUSH communication
pattern between the proxy and tenant servers.

Overview
--------

genro-mail-proxy supports multiple tenants, each with:

* Dedicated SMTP accounts
* Per-tenant delivery report routing
* Independent authentication for sync callbacks
* Isolated rate limiting and quotas

The proxy implements a **PUSH-based architecture** where:

1. Tenant servers submit messages to the proxy via ``POST /commands/add-messages``
2. The proxy dispatches messages via SMTP
3. The proxy **pushes** delivery reports back to each tenant's configured endpoint

Bidirectional Communication Flow
--------------------------------

The following diagram illustrates the complete message lifecycle:

.. code-block:: text

   Proxy                              Tenant Server (Client)
     │                                       │
     │  POST {client_sync_url}               │
     │  {"delivery_report": [...]}           │
     │ ─────────────────────────────────►    │
     │                                       │
     │                         1. Process delivery reports
     │                         2. Update local message statuses
     │                         3. Query pending messages
     │                         4. POST /commands/add-messages ──────┐
     │  ◄───────────────────────────────────────────────────────────┘
     │                                       │
     │  return report_summary                │
     │  {"sent": N, "error": M, ...}         │
     │  ◄─────────────────────────────────   │

**Step-by-step flow:**

1. **Proxy sends delivery reports**: The proxy's client report loop periodically
   collects completed message results (sent, error, deferred) and POSTs them
   to each tenant's ``client_sync_url``.

2. **Tenant processes reports**: The tenant server receives the delivery reports,
   updates its local database with message statuses (delivered, failed, etc.).

3. **Tenant submits new messages**: Optionally, the tenant can query its pending
   outbox and submit new messages back to the proxy via ``POST /commands/add-messages``.

4. **Proxy acknowledges**: The tenant returns a summary response; the proxy marks
   the reports as delivered (``reported_ts``) and eventually cleans them up.

Tenant Configuration
--------------------

Tenants are configured via the REST API. Each tenant has:

.. list-table::
   :header-rows: 1

   * - Field
     - Type
     - Required
     - Description
   * - ``id``
     - ``str``
     - Yes
     - Unique tenant identifier
   * - ``name``
     - ``str``
     - No
     - Human-readable name
   * - ``client_sync_url``
     - ``str``
     - No
     - URL where proxy sends delivery reports (e.g., ``https://api.tenant.com/proxy_sync``)
   * - ``client_auth``
     - ``TenantAuth``
     - No
     - Common authentication for all HTTP endpoints (sync and attachments)
   * - ``client_attachment_url``
     - ``str``
     - No
     - URL for fetching attachments via HTTP (e.g., ``https://api.tenant.com/attachments``)
   * - ``active``
     - ``bool``
     - No
     - Whether tenant is enabled (default: ``true``)

TenantAuth Configuration
------------------------

The ``client_auth`` object supports multiple authentication methods
and is used for **both** delivery report sync and attachment fetching:

**Bearer Token Authentication:**

.. code-block:: json

   {
     "client_auth": {
       "method": "bearer",
       "token": "your-secret-token"
     }
   }

The proxy will send: ``Authorization: Bearer your-secret-token``

**Basic Authentication:**

.. code-block:: json

   {
     "client_auth": {
       "method": "basic",
       "user": "username",
       "password": "password"
     }
   }

The proxy will send: ``Authorization: Basic <base64(user:password)>``

**No Authentication:**

.. code-block:: json

   {
     "client_auth": {
       "method": "none"
     }
   }

Or simply omit the ``client_auth`` field entirely.

Tenant Management API
---------------------

``POST /tenant``
   Create or update a tenant configuration.

   Request body:

   .. code-block:: json

      {
        "id": "tenant-acme",
        "name": "ACME Corporation",
        "client_sync_url": "https://api.acme.com/proxy_sync",
        "client_attachment_url": "https://api.acme.com/attachments",
        "client_auth": {
          "method": "bearer",
          "token": "acme-secret-token"
        },
        "active": true
      }

   Response: ``{"ok": true}``

``GET /tenants``
   List all configured tenants.

   Query parameters:

   - ``active_only`` (bool, optional): Filter to active tenants only

   Response:

   .. code-block:: json

      {
        "ok": true,
        "tenants": [
          {
            "id": "tenant-acme",
            "name": "ACME Corporation",
            "client_sync_url": "https://api.acme.com/proxy_sync",
            "active": true,
            "created_at": "2024-01-20T10:00:00Z",
            "updated_at": "2024-01-20T10:00:00Z"
          }
        ]
      }

``GET /tenant/{tenant_id}``
   Get a specific tenant configuration.

   Response: Single tenant object or ``404`` if not found.

``PUT /tenant/{tenant_id}``
   Update an existing tenant. All fields are optional in the request body.

   Response: ``{"ok": true}``

``DELETE /tenant/{tenant_id}``
   Remove a tenant configuration.

   Response: ``{"ok": true}``

Delivery Report Routing
-----------------------

When the proxy has delivery reports to send, it routes them based on the
``tenant_id`` associated with each message:

1. **Messages with tenant_id**: Reports are grouped by tenant and sent to each
   tenant's ``client_sync_url`` with the appropriate authentication.

2. **Messages without tenant_id**: Reports are sent to the global
   ``client_sync_url`` (configured via ``GMP_CLIENT_SYNC_URL`` environment variable).

3. **Tenants without client_sync_url**: Falls back to the global URL.

The routing logic ensures tenant isolation - each tenant only receives reports
for their own messages.

Delivery Report Payload
-----------------------

The proxy sends delivery reports as HTTP POST requests:

.. code-block:: http

   POST /proxy_sync HTTP/1.1
   Host: api.tenant.com
   Content-Type: application/json
   Authorization: Bearer acme-secret-token

   {
     "delivery_report": [
       {
         "id": "MSG-001",
         "account_id": "smtp-acme",
         "priority": 2,
         "sent_ts": 1705750800,
         "error_ts": null,
         "error": null,
         "deferred_ts": null
       },
       {
         "id": "MSG-002",
         "account_id": "smtp-acme",
         "priority": 2,
         "sent_ts": null,
         "error_ts": 1705750850,
         "error": "550 User not found",
         "deferred_ts": null
       }
     ]
   }

**Report status interpretation:**

- ``sent_ts`` set: Message was successfully delivered
- ``error_ts`` set: Message delivery failed permanently
- ``deferred_ts`` set: Message is scheduled for retry
- All null: Message is still pending

Expected Response
-----------------

The tenant should respond with a summary:

.. code-block:: json

   {
     "sent": 5,
     "error": 1,
     "deferred": 0
   }

The proxy uses this response to:

1. Mark reports as acknowledged (``reported_ts`` timestamp)
2. Eventually clean up old reports based on retention policy

If the tenant returns an error (HTTP 4xx/5xx), the reports remain unacknowledged
and will be retried on the next sync cycle.

Implementing the Tenant Endpoint
--------------------------------

Your tenant server must expose an endpoint to receive delivery reports.
Example using FastAPI:

.. code-block:: python

   from fastapi import FastAPI, Request, HTTPException

   app = FastAPI()

   @app.post("/proxy_sync")
   async def receive_delivery_reports(request: Request):
       """
       Receive delivery reports from the mail proxy.

       This endpoint is called by the proxy to notify us about
       message delivery status.
       """
       data = await request.json()
       reports = data.get("delivery_report", [])

       sent = error = deferred = 0

       for report in reports:
           msg_id = report["id"]

           if report.get("sent_ts"):
               sent += 1
               # Update local message: mark as delivered
               await update_message_status(msg_id, "delivered")

           elif report.get("error_ts"):
               error += 1
               # Update local message: mark as failed
               error_msg = report.get("error", "Unknown error")
               await update_message_status(msg_id, "failed", error=error_msg)

           elif report.get("deferred_ts"):
               deferred += 1
               # Message will be retried by proxy
               await update_message_status(msg_id, "deferred")

       # Optionally: submit new pending messages to the proxy
       pending_messages = await get_pending_outbox_messages()
       if pending_messages:
           await submit_messages_to_proxy(pending_messages)

       return {"sent": sent, "error": error, "deferred": deferred}

Configuration Example
---------------------

Complete tenant setup example:

1. **Create tenant:**

   .. code-block:: bash

      curl -X POST http://localhost:8000/tenant \
        -H "Content-Type: application/json" \
        -H "X-API-Token: your-api-token" \
        -d '{
          "id": "acme",
          "name": "ACME Corp",
          "client_sync_url": "https://api.acme.com/proxy_sync",
          "client_attachment_url": "https://api.acme.com/attachments",
          "client_auth": {
            "method": "bearer",
            "token": "acme-secret"
          }
        }'

2. **Create SMTP account for tenant:**

   .. code-block:: bash

      curl -X POST http://localhost:8000/account \
        -H "Content-Type: application/json" \
        -H "X-API-Token: your-api-token" \
        -d '{
          "id": "smtp-acme",
          "tenant_id": "acme",
          "host": "smtp.acme.com",
          "port": 587,
          "user": "mailer@acme.com",
          "password": "smtp-password",
          "use_tls": true
        }'

3. **Submit messages:**

   .. code-block:: bash

      curl -X POST http://localhost:8000/commands/add-messages \
        -H "Content-Type: application/json" \
        -H "X-API-Token: your-api-token" \
        -d '{
          "messages": [{
            "id": "acme-msg-001",
            "account_id": "smtp-acme",
            "from": "noreply@acme.com",
            "to": ["customer@example.com"],
            "subject": "Welcome!",
            "body": "Welcome to ACME."
          }]
        }'

4. **Proxy delivers message and sends report to** ``https://api.acme.com/proxy_sync``
