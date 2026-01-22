
Usage
=====

Configuration
-------------

Configuration is managed via the ``mail-proxy`` CLI. Each instance stores its
settings in a SQLite database at ``~/.mail-proxy/<name>/mail_service.db``.

CLI Setup (recommended)
~~~~~~~~~~~~~~~~~~~~~~~

Start an instance and configure it interactively:

.. code-block:: bash

   # Start an instance (creates it if new)
   mail-proxy start myserver

   # Add a tenant with delivery report endpoint
   mail-proxy myserver tenants add
   # Prompts: tenant_id, name, base_url, sync_path, attachment_path, auth method

   # Add an SMTP account for the tenant
   mail-proxy myserver acme accounts add
   # Prompts: account_id, host, port, user, password, TLS, rate limits

   # View instance info
   mail-proxy myserver info

   # View statistics
   mail-proxy myserver stats

Environment Variables (Docker/Kubernetes)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For containerized deployments, use environment variables prefixed with ``GMP_``:

.. code-block:: bash

   docker run -p 8000:8000 \
     -e GMP_API_TOKEN=your-secret-token \
     -e GMP_DB_PATH=/data/mail_service.db \
     -e GMP_SCHEDULER_ACTIVE=true \
     -v mail-data:/data \
     genro-mail-proxy

Available environment variables::

  GMP_LOG_LEVEL                           - Logging level (default: INFO)
  GMP_DB_PATH                             - Database path (default: /data/mail_service.db)
  GMP_HOST                                - Server host (default: 0.0.0.0)
  GMP_PORT                                - Server port (default: 8000)
  GMP_SCHEDULER_ACTIVE                    - Enable scheduler (default: false)
  GMP_API_TOKEN                           - API authentication token
  GMP_SEND_LOOP_INTERVAL                  - Send loop interval in seconds
  GMP_TEST_MODE                           - Enable test mode (default: false)
  GMP_DEFAULT_PRIORITY                    - Default message priority (default: 2)
  GMP_DELIVERY_REPORT_RETENTION_SECONDS   - Retention time for delivery reports (default: 604800)
  GMP_BATCH_SIZE_PER_ACCOUNT              - Batch size per account (default: 50)
  GMP_LOG_DELIVERY_ACTIVITY               - Log delivery activity (default: false)

Docker Workflow
~~~~~~~~~~~~~~~

In Docker deployments, the container exposes the REST API. Tenant and account
configuration is done dynamically via API calls from your application:

.. code-block:: bash

   # Create a tenant
   curl -X POST http://localhost:8000/tenant \
     -H "Content-Type: application/json" \
     -H "X-API-Token: your-token" \
     -d '{
       "id": "acme",
       "name": "ACME Corp",
       "client_base_url": "https://api.acme.com",
       "client_sync_path": "/mail-proxy/sync",
       "client_attachment_path": "/mail-proxy/attachments"
     }'

   # Create an SMTP account for the tenant
   curl -X POST http://localhost:8000/account \
     -H "Content-Type: application/json" \
     -H "X-API-Token: your-token" \
     -d '{
       "id": "smtp1",
       "tenant_id": "acme",
       "host": "smtp.gmail.com",
       "port": 587,
       "user": "user@example.com",
       "password": "app-password",
       "use_tls": true
     }'

This allows dynamic multi-tenant configuration at runtime. Your application
(e.g., Genropy) manages tenant and account lifecycle via these API endpoints.

API Token
~~~~~~~~~

``api_token`` secures the FastAPI endpoints: every HTTP request must include
``X-API-Token: <value>``. Generate or view the token with:

.. code-block:: bash

   mail-proxy myserver token              # Show current token
   mail-proxy myserver token --regenerate # Generate new token

Delivery Activity Logging
~~~~~~~~~~~~~~~~~~~~~~~~~

Set ``GMP_LOG_DELIVERY_ACTIVITY=true`` to surface each SMTP attempt and every
client sync exchange directly in the console logs, useful during troubleshooting or
integration debugging.

Interactive REPL
----------------

The CLI provides an interactive Python REPL for exploring and managing a running
instance:

.. code-block:: bash

   mail-proxy myserver connect

This opens a Python shell with pre-configured objects:

**Client object:**

- ``proxy`` - The connected client instance
- ``proxy.status()`` - Server health and scheduler state
- ``proxy.stats()`` - Queue statistics (tenants, accounts, pending/sent/error counts)
- ``proxy.run_now()`` - Trigger immediate dispatch cycle
- ``proxy.tenants`` - Tenant management interface
- ``proxy.accounts`` - Account management interface
- ``proxy.messages`` - Message management interface

**Interactive forms:**

- ``new_tenant()`` - Create a tenant with guided prompts
- ``new_account()`` - Create an SMTP account with guided prompts
- ``new_message()`` - Create and queue a message with guided prompts

**Example session:**

.. code-block:: python

   >>> proxy.status()
   {'ok': True, 'scheduler_active': True}

   >>> proxy.stats()
   {'tenants': 2, 'accounts': 3, 'messages': {'pending': 5, 'sent': 120, 'error': 2}}

   >>> new_tenant()
   Tenant ID: acme
   Name [acme]: ACME Corp
   Base URL: https://api.acme.com
   ...
   Tenant 'acme' created successfully!

   >>> proxy.run_now()
   {'triggered': True}

Type ``exit()`` or press Ctrl+D to quit the REPL.

Proxy sync exchange
-------------------

When the scheduler has delivery results to report, it POSTs to
``proxy_sync_url`` with basic auth:

.. code-block:: json

   {
     "delivery_report": [
       {"id": "MSG-001", "account_id": "accA", "priority": 1, "sent_ts": 1728460800, "error_ts": null, "error": null, "deferred_ts": null},
       {"id": "MSG-002", "account_id": "accA", "priority": 2, "sent_ts": null, "error_ts": 1728461112, "error": "SMTP timeout", "deferred_ts": null}
     ]
   }

If no events are pending, ``delivery_report`` is an empty list.  A typical
response from Genropy is:

.. code-block:: json

   {"sent": 12, "error": 1, "deferred": 0}

Genropy will subsequently push new messages through ``/commands/add-messages``.
For automated deployments the background SMTP and reporting loops poll the queue
every ``send_interval_seconds``. The ``/commands/run-now`` shortcut can be used
to force an immediate iteration, waking the loops without waiting for the
scheduled interval.


Endpoints
---------

- GET /status
- POST /commands/run-now
- POST /commands/suspend
- POST /commands/activate
- POST /commands/add-messages
- POST /account
- GET /accounts
- DELETE /account/{id}
- GET /messages
- GET /metrics

Test mode
---------

Unit tests and maintenance scripts can instantiate
``async_mail_service.core.AsyncMailCore`` with ``test_mode=True`` (or set
``GMP_TEST_MODE=true``). In this mode the dispatcher and reporting tasks are
still created, but their send interval is stretched to infinity so they wait
for an explicit wake-up. Calling ``/commands/run-now`` (or
``handle_command("run now", {})``) raises that wake-up, making the loops process
the next cycle immediately while still exercising the same code paths used in
production. Production services should leave ``test_mode`` at its default
``False`` value so the periodic loops continue to process the queue automatically
without manual intervention.

REST Examples (curl)
--------------------

Add account:

.. code-block:: bash

   curl -X POST http://localhost:8000/account \
        -H "Content-Type: application/json" \
        -H "X-API-Token: my-secret-token" \
     -d '{
     "id":"gmail","host":"smtp.gmail.com","port":587,
     "user":"you@gmail.com","password":"***","use_tls":false,
     "limit_per_minute":30,"limit_per_hour":500,"limit_per_day":1000
   }'

Python (httpx)
--------------

.. code-block:: python

   import httpx

   client = httpx.Client(base_url="http://localhost:8000",
                         headers={"X-API-Token": "my-secret-token"})

   r = client.post("/commands/add-messages", json={
       "messages": [
           {
               "id": "MSG-001",
               "from": "sender@example.com",
               "to": ["dest@example.com"],
               "subject": "Hi",
               "body": "Hello world"
           }
       ]
   })
   print(r.json())
