
Usage
=====

Configuration
-------------

The service loads settings from ``config.ini`` (or the path provided by ``ASYNC_MAIL_CONFIG``)
and environment variables. Main sections/keys::

  [smtp]        host, port, user, password, use_tls
  [storage]     db_path
  [server]      host, port, api_token, sync_token
  [sync]        proxy_sync_url, proxy_sync_user, proxy_sync_password, proxy_sync_batch_size
  [delivery]    send_interval_seconds, default_priority, delivery_report_retention_seconds

``api_token`` secures the FastAPI endpoints: every HTTP request must include
``X-API-Token: <value>``. ``sync_token`` is available for future Genropy-to-proxy
handshakes. The ``[sync]`` section configures the outbound ``proxy_sync`` call
performed by the dispatcher towards the Genropy server.  Credentials are sent
using HTTP basic authentication (the same format obtained with
``https://user:password@host/path`` URLs).

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

Genropy will subsequently push new messages through ``/commands/add-messages``
and, when ``more_messages`` is ``true``, the dispatcher can trigger
``/commands/run-now`` to pick up the backlog immediately.


Endpoints
---------

- GET /status
- POST /commands/run-now
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

REST Examples (curl)
--------------------

Run now:

.. code-block:: bash

   curl -X POST http://localhost:8000/commands/run-now \
        -H "Content-Type: application/json" \
        -H "X-API-Token: my-secret-token"

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
