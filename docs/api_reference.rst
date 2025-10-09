
API Reference
=============

FastAPI
-------

- OpenAPI JSON: http://localhost:8000/openapi.json
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

Prometheus metrics
------------------
- asyncmail_sent_total{account_id}
- asyncmail_errors_total{account_id}
- asyncmail_deferred_total{account_id}
- asyncmail_rate_limited_total{account_id}
- asyncmail_pending_messages

Command payload
---------------

.. code-block:: json

   {
     "cmd": "run now" | "suspend" | "activate" | "schedule" | "pendingMessages" | "listDeferred",
     "payload": {}
   }
