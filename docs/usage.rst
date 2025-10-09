
Usage
=====

Endpoints
---------

- GET /status
- POST /command
- POST /account
- GET /accounts
- DELETE /account/{id}
- GET /pending
- GET /deferred
- GET /metrics

REST Examples (curl)
--------------------

Run now:

.. code-block:: bash

   curl -X POST http://localhost:8000/command -H "Content-Type: application/json" -d '{"cmd":"run now"}'

Add account:

.. code-block:: bash

   curl -X POST http://localhost:8000/account -H "Content-Type: application/json" -d '{
     "id":"gmail","host":"smtp.gmail.com","port":587,
     "user":"you@gmail.com","password":"***",
     "limit_per_minute":30,"limit_per_hour":500,"limit_per_day":1000
   }'

Python (httpx)
--------------

.. code-block:: python

   import httpx
   r = httpx.post("http://localhost:8000/command", json={"cmd":"run now"})
   print(r.json())
