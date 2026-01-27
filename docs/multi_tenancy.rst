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
     │  POST {client_base_url + sync_path}    │
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
   to each tenant's sync endpoint (``client_base_url`` + ``client_sync_path``).

2. **Tenant processes reports**: The tenant server receives the delivery reports,
   updates its local database with message statuses (delivered, failed, etc.).

3. **Tenant submits new messages**: Optionally, the tenant can query its pending
   outbox and submit new messages back to the proxy via ``POST /commands/add-messages``.

4. **Proxy acknowledges**: The tenant returns a summary response; the proxy marks
   the reports as delivered (``reported_ts``) and eventually cleans them up.

API Authentication Model
------------------------

genro-mail-proxy implements a **two-tier authentication model**:

1. **Global Admin Token** (``GMP_API_TOKEN``): Full access to all endpoints and all tenants
2. **Tenant-Specific Token**: Limited access to the tenant's own resources only

This separation ensures that:

- Administrators can manage all tenants and system configuration
- Each tenant can only access their own data (messages, accounts, reports)
- A compromised tenant token cannot affect other tenants or system configuration

Token Types and Permissions
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Endpoint Category
     - Global Admin Token
     - Tenant Token
   * - **Admin-Only Endpoints**
     - ✅ Full access
     - ❌ Rejected (HTTP 403)
   * - **Tenant-Scoped Endpoints**
     - ✅ Access to any tenant
     - ✅ Access to own tenant only

**Admin-Only Endpoints** (require global token):

- ``POST /tenant`` - Create new tenants
- ``DELETE /tenant/{id}`` - Delete tenants
- ``GET /tenants`` - List all tenants
- ``POST /tenant/{id}/api-key`` - Generate tenant API key
- ``DELETE /tenant/{id}/api-key`` - Revoke tenant API key
- ``GET /instance``, ``PUT /instance`` - Instance configuration
- ``POST /instance/reload-bounce`` - Reload bounce configuration
- ``GET /command-log``, ``GET /command-log/export`` - Audit trail

**Tenant-Scoped Endpoints** (allow tenant or admin tokens):

- ``GET /tenant/{id}``, ``PUT /tenant/{id}`` - View/update own tenant
- ``GET /messages``, ``POST /commands/add-messages`` - Manage messages
- ``GET /accounts``, ``POST /account``, ``DELETE /account/{id}`` - Manage SMTP accounts
- ``POST /commands/suspend``, ``POST /commands/activate`` - Suspend/activate sending
- ``POST /commands/delete-messages``, ``POST /commands/cleanup-messages`` - Message cleanup

Authentication Flow
~~~~~~~~~~~~~~~~~~~

When a request arrives with an ``X-API-Token`` header:

1. **Check global token first**: If it matches ``GMP_API_TOKEN``, grant admin access
2. **Check tenant tokens**: Look up the token hash in the tenants table
3. **Verify scope**: For tenant tokens, verify the request's ``tenant_id`` matches the token owner
4. **Reject if neither**: Return HTTP 401 Unauthorized

.. code-block:: text

   Request with X-API-Token header
            │
            ▼
   ┌─────────────────────┐
   │ Is token = global?  │──Yes──► Admin access (all endpoints)
   └─────────────────────┘
            │ No
            ▼
   ┌─────────────────────┐
   │ Is token in tenants │──Yes──► Tenant access
   │     table?          │         (own resources only)
   └─────────────────────┘
            │ No
            ▼
      HTTP 401 Unauthorized

For tenant-scoped endpoints, an additional scope check ensures tenant tokens
can only access their own data:

.. code-block:: text

   Tenant-scoped request (e.g., GET /messages?tenant_id=acme)
            │
            ▼
   ┌─────────────────────┐
   │ Is admin token?     │──Yes──► Allow access to any tenant
   └─────────────────────┘
            │ No (tenant token)
            ▼
   ┌─────────────────────┐
   │ Does tenant_id      │──Yes──► Allow access
   │ match token owner?  │
   └─────────────────────┘
            │ No
            ▼
      HTTP 401 "Token not authorized for this tenant"

Automatic API Key Generation
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

When creating a new tenant via ``POST /tenant``, an API key is **automatically
generated** and returned in the response:

.. code-block:: bash

   curl -X POST http://localhost:8000/tenant \
     -H "Content-Type: application/json" \
     -H "X-API-Token: $ADMIN_TOKEN" \
     -d '{"id": "acme", "name": "ACME Corp"}'

Response for **new tenant**:

.. code-block:: json

   {
     "ok": true,
     "api_key": "k3Xp9qR7mNvL2sWtYhBjCfDgEaUiOp..."
   }

.. warning::

   The ``api_key`` is shown **only once** at creation time. Store it securely!
   It cannot be retrieved later. If lost, use ``POST /tenant/{id}/api-key``
   to generate a new key (which invalidates the old one).

Response for **existing tenant** (update):

.. code-block:: json

   {
     "ok": true
   }

Note: When updating an existing tenant, the API key is not changed or returned.

Managing Tenant API Keys
~~~~~~~~~~~~~~~~~~~~~~~~

**Generate a new API key** (invalidates the previous one):

.. code-block:: bash

   curl -X POST http://localhost:8000/tenant/acme/api-key \
     -H "X-API-Token: $ADMIN_TOKEN"

Response:

.. code-block:: json

   {
     "ok": true,
     "api_key": "newKeyHere..."
   }

**Revoke an API key** (tenant must use admin token or get a new key):

.. code-block:: bash

   curl -X DELETE http://localhost:8000/tenant/acme/api-key \
     -H "X-API-Token: $ADMIN_TOKEN"

Response:

.. code-block:: json

   {
     "ok": true
   }

Token Properties
~~~~~~~~~~~~~~~~

- Tokens are stored as **SHA-256 hashes** (the raw token is never stored)
- Optional expiration via ``api_key_expires_at`` (Unix timestamp)
- **One token per tenant** (creating a new one replaces the old)
- Tokens are 43 characters long (URL-safe base64)

Security Best Practices
~~~~~~~~~~~~~~~~~~~~~~~

1. **Keep admin token secret**: Only system administrators should have access
2. **Use tenant tokens for applications**: Each tenant application should use
   its own tenant token, not the admin token
3. **Rotate keys periodically**: Use ``POST /tenant/{id}/api-key`` to generate
   new keys
4. **Use HTTPS**: Always use TLS in production to protect tokens in transit
5. **Set expiration**: For temporary access, use the ``expires_at`` parameter

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
   * - ``client_base_url``
     - ``str``
     - No
     - Base URL for tenant HTTP endpoints (e.g., ``https://api.tenant.com``)
   * - ``client_sync_path``
     - ``str``
     - No
     - Path for delivery report callbacks (default: ``/mail-proxy/sync``)
   * - ``client_attachment_path``
     - ``str``
     - No
     - Path for attachment fetcher endpoint (default: ``/mail-proxy/attachments``)
   * - ``client_auth``
     - ``TenantAuth``
     - No
     - Common authentication for all HTTP endpoints (sync and attachments)
   * - ``active``
     - ``bool``
     - No
     - Whether tenant is enabled (default: ``true``)
   * - ``api_key_hash``
     - ``str``
     - No
     - SHA-256 hash of tenant's dedicated API token (internal use)
   * - ``api_key_expires_at``
     - ``timestamp``
     - No
     - Unix timestamp when the API token expires (optional)

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

``POST /tenant`` *(Admin only)*
   Create or update a tenant configuration.

   **Authentication**: Requires global admin token.

   Request body:

   .. code-block:: json

      {
        "id": "tenant-acme",
        "name": "ACME Corporation",
        "client_base_url": "https://api.acme.com",
        "client_sync_path": "/proxy_sync",
        "client_attachment_path": "/attachments",
        "client_auth": {
          "method": "bearer",
          "token": "acme-secret-token"
        },
        "active": true
      }

   Response for **new tenant** (API key auto-generated):

   .. code-block:: json

      {
        "ok": true,
        "api_key": "k3Xp9qR7mNvL2sWtYhBjCfDg..."
      }

   Response for **existing tenant** (update, no API key change):

   .. code-block:: json

      {
        "ok": true
      }

``GET /tenants`` *(Admin only)*
   List all configured tenants.

   **Authentication**: Requires global admin token.

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
            "client_base_url": "https://api.acme.com",
            "active": true,
            "created_at": "2024-01-20T10:00:00Z",
            "updated_at": "2024-01-20T10:00:00Z"
          }
        ]
      }

``GET /tenant/{tenant_id}`` *(Tenant-scoped)*
   Get a specific tenant configuration.

   **Authentication**: Admin token or matching tenant token.

   Response: Single tenant object or ``404`` if not found.

``PUT /tenant/{tenant_id}`` *(Tenant-scoped)*
   Update an existing tenant. All fields are optional in the request body.

   **Authentication**: Admin token or matching tenant token.

   Response: ``{"ok": true}``

``DELETE /tenant/{tenant_id}`` *(Admin only)*
   Remove a tenant configuration.

   **Authentication**: Requires global admin token.

   Response: ``{"ok": true}``

``POST /tenant/{tenant_id}/api-key`` *(Admin only)*
   Generate a new API key for a tenant. Invalidates any existing key.

   **Authentication**: Requires global admin token.

   Response:

   .. code-block:: json

      {
        "ok": true,
        "api_key": "newGeneratedKey..."
      }

``DELETE /tenant/{tenant_id}/api-key`` *(Admin only)*
   Revoke the tenant's API key.

   **Authentication**: Requires global admin token.

   Response: ``{"ok": true}``

Delivery Report Routing
-----------------------

When the proxy has delivery reports to send, it routes them based on the
``tenant_id`` associated with each message:

1. **Messages with tenant_id**: Reports are grouped by tenant and sent to each
   tenant's sync endpoint (``client_base_url`` + ``client_sync_path``) with the
   appropriate authentication.

2. **Messages without tenant_id**: Reports are sent to the global sync endpoint
   (configured via ``GMP_CLIENT_SYNC_URL`` environment variable).

3. **Tenants without client_base_url**: Falls back to the global URL.

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
         "tenant_id": "acme",
         "id": "MSG-001",
         "pk": "550e8400-e29b-41d4-a716-446655440000",
         "sent_ts": 1705750800
       },
       {
         "tenant_id": "acme",
         "id": "MSG-002",
         "pk": "550e8400-e29b-41d4-a716-446655440001",
         "error_ts": 1705750850,
         "error": "550 User not found"
       }
     ]
   }

**Report fields:**

- ``tenant_id``: Tenant identifier
- ``id``: Client-provided message identifier
- ``pk``: Internal UUID primary key (useful for correlation)

**Event-specific fields** (only relevant field is present):

- ``sent_ts``: Unix timestamp when message was successfully delivered
- ``error_ts`` + ``error``: Timestamp and description when delivery failed permanently
- ``deferred_ts`` + ``deferred_reason``: Timestamp when message was deferred for retry
- ``bounce_ts`` + ``bounce_type`` + ``bounce_code`` + ``bounce_reason``: Bounce notification details
- ``pec_event`` + ``pec_ts`` + ``pec_details``: PEC receipt information (pec_acceptance, pec_delivery, pec_error)

Expected Response
-----------------

The tenant should respond with a JSON object containing at minimum an ``ok`` field:

.. code-block:: json

   {
     "ok": true,
     "queued": 15,
     "next_sync_after": null
   }

**Response fields:**

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
     - Number of messages ready to send. When > 0, triggers immediate resync.
   * - ``next_sync_after``
     - int
     - No
     - Unix timestamp for "Do Not Disturb". Proxy won't call until this time.
   * - ``error``
     - list[str]
     - No
     - Message IDs that failed to process
   * - ``not_found``
     - list[str]
     - No
     - Message IDs not found in tenant's database

The proxy uses this response to:

1. Mark reports as acknowledged (``reported_ts`` timestamp)
2. Schedule next sync based on ``next_sync_after`` (or default 5-minute interval)
3. Trigger immediate resync if ``queued > 0``
4. Eventually clean up old reports based on retention policy

If the tenant returns an error (HTTP 4xx/5xx), the reports remain unacknowledged
and will be retried on the next sync cycle.

Do Not Disturb Feature
~~~~~~~~~~~~~~~~~~~~~~

Tenants with serverless databases (Neon, PlanetScale) may want to avoid cold-start
costs during idle hours. By returning ``next_sync_after`` with a future timestamp,
the tenant tells the proxy: "don't call me until this time".

.. code-block:: json

   {
     "ok": true,
     "queued": 0,
     "next_sync_after": 1706500800
   }

**Important**: If the tenant has pending events to report (sent, error, bounce, etc.),
the proxy will still call them regardless of the DND setting. DND only affects
the periodic "heartbeat" sync calls when there are no events.

**Overriding DND**: If a tenant needs immediate sync while in DND mode, they can
call ``POST /commands/run-now`` with their tenant token. This resets their DND
and triggers an immediate sync cycle for that tenant only.

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

1. **Create tenant** (using admin token):

   .. code-block:: bash

      # Use the global admin token (GMP_API_TOKEN)
      curl -X POST http://localhost:8000/tenant \
        -H "Content-Type: application/json" \
        -H "X-API-Token: $ADMIN_TOKEN" \
        -d '{
          "id": "acme",
          "name": "ACME Corp",
          "client_base_url": "https://api.acme.com",
          "client_sync_path": "/proxy_sync",
          "client_attachment_path": "/attachments",
          "client_auth": {
            "method": "bearer",
            "token": "acme-secret"
          }
        }'

   Response (save the api_key!):

   .. code-block:: json

      {
        "ok": true,
        "api_key": "ACME_TENANT_TOKEN_save_this_securely"
      }

2. **Create SMTP account for tenant** (can use tenant token now):

   .. code-block:: bash

      # Use either admin token or the tenant's own token
      curl -X POST http://localhost:8000/account \
        -H "Content-Type: application/json" \
        -H "X-API-Token: $ACME_TENANT_TOKEN" \
        -d '{
          "id": "smtp-acme",
          "tenant_id": "acme",
          "host": "smtp.acme.com",
          "port": 587,
          "user": "mailer@acme.com",
          "password": "smtp-password",
          "use_tls": true
        }'

3. **Submit messages** (using tenant token):

   .. code-block:: bash

      curl -X POST http://localhost:8000/commands/add-messages \
        -H "Content-Type: application/json" \
        -H "X-API-Token: $ACME_TENANT_TOKEN" \
        -d '{
          "messages": [{
            "id": "acme-msg-001",
            "tenant_id": "acme",
            "account_id": "smtp-acme",
            "from": "noreply@acme.com",
            "to": ["customer@example.com"],
            "subject": "Welcome!",
            "body": "Welcome to ACME."
          }]
        }'

4. **Proxy delivers message and sends report to** ``https://api.acme.com/proxy_sync``

.. note::

   In production, each tenant application should use its own tenant token
   (received at creation time) rather than the global admin token. This
   ensures proper isolation - a tenant cannot access or modify other
   tenants' data.

Batch Suspension
----------------

Tenants can suspend message sending at different granularity levels:

* **Full suspension**: Stop all message sending for the tenant
* **Batch-specific suspension**: Stop only messages belonging to a specific batch/campaign

This feature is useful when you need to halt a mailing campaign due to content errors,
while allowing other messages (transactional emails, other campaigns) to continue normally.

**Use case example:**

A tenant sends a newsletter to 5000 recipients and discovers an error in the content:

1. **Suspend the batch**: Stop sending for that specific campaign
2. **Re-submit corrected messages**: Messages with the same IDs overwrite unsent ones
3. **Activate the batch**: Resume sending with corrected content

Meanwhile, transactional emails and other campaigns continue uninterrupted.

Batch Code in Messages
~~~~~~~~~~~~~~~~~~~~~~

Messages can include an optional ``batch_code`` field to group them into campaigns:

.. code-block:: json

   {
     "messages": [{
       "id": "newsletter-2026-01-001",
       "account_id": "smtp-acme",
       "batch_code": "NL-2026-01",
       "from": "newsletter@acme.com",
       "to": ["customer@example.com"],
       "subject": "January Newsletter",
       "body": "..."
     }]
   }

Messages without ``batch_code`` are only affected by full tenant suspension (``*``).

Suspend/Activate API
~~~~~~~~~~~~~~~~~~~~

``POST /commands/suspend``
   Suspend message sending for a tenant.

   Query parameters:

   - ``tenant_id`` (str, required): The tenant to suspend
   - ``batch_code`` (str, optional): Specific batch to suspend. If omitted, suspends all sending.

   Examples:

   .. code-block:: bash

      # Suspend all sending for tenant
      curl -X POST "http://localhost:8000/commands/suspend?tenant_id=acme" \
        -H "X-API-Token: your-api-token"

      # Suspend only a specific batch
      curl -X POST "http://localhost:8000/commands/suspend?tenant_id=acme&batch_code=NL-2026-01" \
        -H "X-API-Token: your-api-token"

   Response:

   .. code-block:: json

      {
        "ok": true,
        "tenant_id": "acme",
        "batch_code": "NL-2026-01",
        "suspended_batches": ["NL-2026-01"],
        "pending_messages": 4500
      }

``POST /commands/activate``
   Resume message sending for a tenant.

   Query parameters:

   - ``tenant_id`` (str, required): The tenant to activate
   - ``batch_code`` (str, optional): Specific batch to activate. If omitted, clears all suspensions.

   Examples:

   .. code-block:: bash

      # Activate all sending for tenant (clear all suspensions)
      curl -X POST "http://localhost:8000/commands/activate?tenant_id=acme" \
        -H "X-API-Token: your-api-token"

      # Activate only a specific batch
      curl -X POST "http://localhost:8000/commands/activate?tenant_id=acme&batch_code=NL-2026-01" \
        -H "X-API-Token: your-api-token"

   Response:

   .. code-block:: json

      {
        "ok": true,
        "tenant_id": "acme",
        "batch_code": "NL-2026-01",
        "suspended_batches": [],
        "pending_messages": 0
      }

Suspension Behavior
~~~~~~~~~~~~~~~~~~~

The ``suspended_batches`` field in the tenant record stores the suspension state:

- **Empty/NULL**: No suspension, all messages are processed normally
- **"*"**: Full suspension, no messages are sent for this tenant
- **"NL-01,NL-02"**: Comma-separated list of suspended batch codes

**Processing rules:**

1. If ``suspended_batches = "*"``: All messages for the tenant are skipped
2. If ``suspended_batches`` contains the message's ``batch_code``: That message is skipped
3. Messages without ``batch_code`` are only affected by full suspension (``*``)

**Important notes:**

- Suspending multiple batches accumulates them in the list
- Activating a single batch removes only that batch from the list
- Activating without ``batch_code`` clears all suspensions
- You cannot activate a single batch when full suspension (``*``) is active;
  you must first activate all (clear the ``*``)

Complete Workflow Example
~~~~~~~~~~~~~~~~~~~~~~~~~

1. **Submit newsletter campaign:**

   .. code-block:: bash

      curl -X POST http://localhost:8000/commands/add-messages \
        -H "Content-Type: application/json" \
        -H "X-API-Token: your-api-token" \
        -d '{
          "messages": [
            {"id": "nl-001", "account_id": "smtp-acme", "batch_code": "NL-2026-01", ...},
            {"id": "nl-002", "account_id": "smtp-acme", "batch_code": "NL-2026-01", ...},
            ...
          ]
        }'

2. **Discover error, suspend the batch:**

   .. code-block:: bash

      curl -X POST "http://localhost:8000/commands/suspend?tenant_id=acme&batch_code=NL-2026-01" \
        -H "X-API-Token: your-api-token"

      # Response shows 4500 pending messages in that batch

3. **Re-submit corrected messages (same IDs overwrite unsent ones):**

   .. code-block:: bash

      curl -X POST http://localhost:8000/commands/add-messages \
        -H "Content-Type: application/json" \
        -H "X-API-Token: your-api-token" \
        -d '{
          "messages": [
            {"id": "nl-001", "account_id": "smtp-acme", "batch_code": "NL-2026-01", "body": "Corrected content..."},
            {"id": "nl-002", "account_id": "smtp-acme", "batch_code": "NL-2026-01", "body": "Corrected content..."},
            ...
          ]
        }'

4. **Resume sending:**

   .. code-block:: bash

      curl -X POST "http://localhost:8000/commands/activate?tenant_id=acme&batch_code=NL-2026-01" \
        -H "X-API-Token: your-api-token"

      # Messages with corrected content are now being sent
