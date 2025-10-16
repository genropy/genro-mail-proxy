
Usage
=====

Configuration
-------------

The service loads settings from ``config.ini`` (or the path provided by ``ASYNC_MAIL_CONFIG``)
and environment variables. Main sections/keys::

  [storage]     db_path
  [server]      host, port, api_token
  [client]      client_sync_url, client_sync_user, client_sync_password, client_sync_token
  [scheduler]   active, timezone, rules (JSON-encoded)
  [delivery]    send_interval_seconds, default_priority, delivery_report_retention_seconds

``api_token`` secures the FastAPI endpoints: every HTTP request must include
``X-API-Token: <value>``. The ``[client]`` section configures the outbound
``proxy_sync`` call performed by the dispatcher towards the Genropy server.
Credentials are sent using HTTP basic authentication (the same format obtained
with ``https://user:password@host/path`` URLs) unless ``client_sync_token`` is
provided.

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
For automated deployments the background SMTP loop already polls the queue every
``send_interval_seconds``; the ``/commands/run-now`` shortcut is intended only
for instances created with ``test_mode=True`` (for example during unit tests or
manual maintenance) where the background tasks are disabled.


Endpoints
---------

- GET /status
- POST /commands/run-now (available only when the service runs with ``test_mode=True``)
- POST /commands/suspend
- POST /commands/activate
- POST /commands/add-messages
- POST /commands/rules
- GET /commands/rules
- PATCH /commands/rules/{rule_id}
- DELETE /commands/rules/{rule_id}
- POST /account
- GET /accounts
- DELETE /account/{id}
- GET /messages
- GET /metrics

Test mode
---------

Unit tests and manual maintenance scripts can instantiate
``async_mail_service.core.AsyncMailCore`` with ``test_mode=True``. In this
configuration the background dispatcher/reporting/cleanup tasks are not started;
instead, operators can call ``/commands/run-now`` (or invoke
``handle_command("run now", {})`` directly) to execute single, on-demand cycles.
Production services should leave ``test_mode`` at its default ``False`` value so
the periodic loops continue to process the queue automatically.

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
