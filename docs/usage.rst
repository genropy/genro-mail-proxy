
Usage
=====

Configuration
-------------

The service loads settings from ``config.ini`` (or the path provided by ``GMP_CONFIG``)
and environment variables. All environment variables use the ``GMP_`` prefix (Genro Mail Proxy)
to avoid conflicts in k8s environments.

Main config file sections/keys::

  [storage]     db_path
  [server]      host, port, api_token
  [client]      client_sync_url, client_sync_user, client_sync_password, client_sync_token
  [scheduler]   active
  [delivery]    send_interval_seconds, test_mode, default_priority,
                delivery_report_retention_seconds, batch_size_per_account
  [logging]     delivery_activity

Environment variables (all prefixed with GMP_)::

  GMP_CONFIG                              - Path to config.ini file (default: config.ini)
  GMP_LOG_LEVEL                           - Logging level (default: INFO)
  GMP_DB_PATH                             - Database path (default: /data/mail_service.db)
  GMP_HOST                                - Server host (default: 0.0.0.0)
  GMP_PORT                                - Server port (default: 8000)
  GMP_SCHEDULER_ACTIVE                    - Enable scheduler (default: false)
  GMP_API_TOKEN                           - API authentication token
  GMP_CLIENT_SYNC_URL                     - Client sync URL
  GMP_CLIENT_SYNC_USER                    - Client sync username
  GMP_CLIENT_SYNC_PASSWORD                - Client sync password
  GMP_CLIENT_SYNC_TOKEN                   - Client sync token (alternative to user/password)
  GMP_SEND_LOOP_INTERVAL                  - Send loop interval in seconds
  GMP_TEST_MODE                           - Enable test mode (default: false)
  GMP_DEFAULT_PRIORITY                    - Default message priority (default: 2)
  GMP_DELIVERY_REPORT_RETENTION_SECONDS   - Retention time for delivery reports (default: 604800)
  GMP_BATCH_SIZE_PER_ACCOUNT              - Batch size per account (default: 50)
  GMP_LOG_DELIVERY_ACTIVITY               - Log delivery activity (default: false)

See ``config.ini.example`` for detailed documentation of all parameters.

``api_token`` secures the FastAPI endpoints: every HTTP request must include
``X-API-Token: <value>``. The ``[client]`` section configures the outbound
``proxy_sync`` call performed by the dispatcher towards the Genropy server.
Credentials are sent using HTTP basic authentication (the same format obtained
with ``https://user:password@host/path`` URLs) unless ``client_sync_token`` is
provided.

Enable ``[logging] delivery_activity = true`` to surface each SMTP attempt and every
client sync exchange directly in the console logs, useful during troubleshooting or
integration debugging.

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
``async_mail_service.core.AsyncMailCore`` (or set ``[delivery] test_mode = true`` in
``config.ini``) with ``test_mode=True``. In this mode
the dispatcher and reporting tasks are still created, but their send interval is
stretched to infinity so they wait for an explicit wake-up. Calling
``/commands/run-now`` (or ``handle_command("run now", {})``) raises that wake-up,
making the loops process the next cycle immediately while still exercising the
same code paths used in production. Production services should leave
``test_mode`` at its default ``False`` value so the periodic loops continue to
process the queue automatically without manual intervention.

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
