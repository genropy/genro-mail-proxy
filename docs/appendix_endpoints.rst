
Appendix: Endpoint Reference
============================

This appendix provides a quick reference for all HTTP endpoints, both those
exposed by the proxy and those that client applications must implement.

Proxy Endpoints (Server)
------------------------

These endpoints are exposed by genro-mail-proxy for client applications to consume.

**Authentication levels:**

- **Public**: No authentication required
- **Auth**: Requires ``X-API-Token`` header (admin or tenant token)
- **Admin**: Requires admin token only
- **Tenant**: Accepts admin or matching tenant token

.. list-table:: Proxy Endpoints
   :header-rows: 1
   :widths: 10 35 15 40

   * - Method
     - Endpoint
     - Auth
     - Description
   * - GET
     - ``/health``
     - Public
     - Health check, returns ``{"ok": true}``
   * - GET
     - ``/metrics``
     - Public
     - Prometheus metrics (text format)
   * - GET
     - ``/status``
     - Auth
     - Service status with scheduler state
   * - POST
     - ``/commands/run-now``
     - Tenant
     - Trigger immediate dispatch cycle
   * - POST
     - ``/commands/suspend``
     - Tenant
     - Suspend sending for a tenant
   * - POST
     - ``/commands/activate``
     - Tenant
     - Resume sending for a tenant
   * - POST
     - ``/commands/add-messages``
     - Tenant
     - Queue messages for delivery
   * - POST
     - ``/commands/delete-messages``
     - Tenant
     - Remove messages from queue
   * - POST
     - ``/commands/cleanup-messages``
     - Tenant
     - Cleanup old reported messages
   * - GET
     - ``/messages``
     - Tenant
     - List queued/sent messages
   * - POST
     - ``/account``
     - Tenant
     - Add/update SMTP account
   * - GET
     - ``/accounts``
     - Tenant
     - List SMTP accounts for tenant
   * - DELETE
     - ``/account/{account_id}``
     - Tenant
     - Delete an SMTP account
   * - POST
     - ``/tenant``
     - Admin
     - Create new tenant
   * - GET
     - ``/tenants``
     - Admin
     - List all tenants
   * - GET
     - ``/tenants/sync-status``
     - Admin
     - Sync status for all tenants
   * - GET
     - ``/tenant/{tenant_id}``
     - Tenant
     - Get tenant configuration
   * - PUT
     - ``/tenant/{tenant_id}``
     - Tenant
     - Update tenant configuration
   * - DELETE
     - ``/tenant/{tenant_id}``
     - Admin
     - Delete tenant and all data
   * - POST
     - ``/tenant/{tenant_id}/api-key``
     - Admin
     - Generate new API key for tenant
   * - DELETE
     - ``/tenant/{tenant_id}/api-key``
     - Admin
     - Revoke tenant's API key
   * - GET
     - ``/instance``
     - Admin
     - Get instance configuration
   * - PUT
     - ``/instance``
     - Admin
     - Update instance configuration
   * - POST
     - ``/instance/reload-bounce``
     - Admin
     - Reload bounce detection rules
   * - GET
     - ``/command-log``
     - Admin
     - Query command audit log
   * - GET
     - ``/command-log/export``
     - Admin
     - Export command log as JSON lines

Client Endpoints (Required)
---------------------------

These endpoints must be implemented by client applications to receive
callbacks from the proxy.

.. list-table:: Client Endpoints
   :header-rows: 1
   :widths: 10 35 55

   * - Method
     - Endpoint
     - Description
   * - POST
     - ``{client_base_url}{client_sync_path}``
     - **Delivery reports callback**. Receives JSON array of message statuses
       (sent, error, bounced). Must return HTTP 200 with JSON response.
       Can optionally return ``next_sync_after`` timestamp for Do Not Disturb.
   * - POST
     - ``{client_base_url}{client_attachment_path}``
     - **Attachment fetcher** (optional). Called when ``fetch_mode: "endpoint"``.
       Receives ``{"storage_path": "..."}`` and returns file content.
       Required only if using endpoint-based attachments.

Sync Endpoint Details
~~~~~~~~~~~~~~~~~~~~~

The sync endpoint receives delivery reports in this format:

.. code-block:: json

   {
     "reports": [
       {
         "tenant_id": "acme",
         "id": "msg-123",
         "pk": "uuid-...",
         "sent_ts": 1706000000.0,
         "recipient_email": "user@example.com"
       },
       {
         "tenant_id": "acme",
         "id": "msg-456",
         "error_ts": 1706000001.0,
         "error": "Connection refused",
         "recipient_email": "bad@example.com"
       }
     ]
   }

Expected response:

.. code-block:: json

   {
     "ok": true,
     "next_sync_after": 1706003600
   }

The ``next_sync_after`` field is optional. If provided, the proxy will not
call this tenant's sync endpoint until after that Unix timestamp (Do Not Disturb).

Attachment Endpoint Details
~~~~~~~~~~~~~~~~~~~~~~~~~~~

When a message includes an attachment with ``fetch_mode: "endpoint"``, the proxy
calls the attachment endpoint:

.. code-block:: json

   {
     "storage_path": "document_id=12345"
   }

The endpoint must return the file content with appropriate ``Content-Type``
and optionally ``Content-Disposition`` headers.

Configuration
~~~~~~~~~~~~~

Client endpoints are configured per-tenant:

- ``client_base_url``: Base URL (e.g., ``https://app.example.com``)
- ``client_sync_path``: Sync endpoint path (default: ``/mail-proxy/sync``)
- ``client_attachment_path``: Attachment endpoint path (default: ``/mail-proxy/attachments``)
- ``client_auth``: Authentication (bearer token or basic auth)

See :doc:`multi_tenancy` for complete configuration details.
