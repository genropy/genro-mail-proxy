Overview
========

This page summarises how the async mail service fits together and the path
followed by each message.

High-level architecture
-----------------------

The service is composed of the following building blocks:

* **AsyncMailCore** – orchestrates scheduling, rate limiting, persistence and
  delivery.  It exposes a coroutine-based API (`handle_command`) used by the
  HTTP layer.
* **REST API** – defined in :mod:`async_mail_service.api`, built with FastAPI
  and protected by the ``X-API-Token`` header.
* **Fetcher** – optional helper for upstream integrations; batch submissions are
  primarily handled through the REST API.
* **Persistence** – stores SMTP accounts, the unified ``messages`` table, and
  send logs in SQLite.
* **RateLimiter** – inspects send logs to determine whether a message needs to
  be deferred.
* **SMTPPool** – keeps SMTP connections warm for the currently executing
  asyncio task.
* **Metrics** – :class:`async_mail_service.prometheus.MailMetrics` exports
  Prometheus counters and gauges.

.. mermaid::
   :caption: Logical architecture of the async mail service

   graph TD
     Client["REST Clients"] -->|JSON commands| API[FastAPI layer]
     API --> Core[AsyncMailCore]
     Core --> Persistence[(SQLite<br/>messages, accounts, send_log)]
     Core --> RateLimiter[RateLimiter]
     RateLimiter --> Persistence
     Core --> Pool[SMTPPool]
     Pool --> SMTP[SMTP Server]
     Core --> Metrics[Prometheus exporter]
     Core --> Sync["Client sync (proxy_sync)"]
     Sync --> Upstream["Genropy / external system"]
     Metrics --> Prometheus["Prometheus server"]

Request flow
------------

1. A client issues ``/commands/add-messages`` with one or more payloads.  The
   API dependency validates ``X-API-Token`` before dispatching to
   :meth:`AsyncMailCore.handle_command`.
2. ``AsyncMailCore`` validates each message (mandatory ``id``, sender, recipients,
   known account, etc.).  Accepted messages are written to the ``messages`` table
   with ``priority`` (default ``2``) and optional ``deferred_ts``; rejected ones
   are reported back with the associated reason.
3. The SMTP dispatch loop repeatedly queries ``messages`` for entries lacking
   ``sent_ts``/``error_ts`` whose ``deferred_ts`` is in the past.  Rate limiting
   can reschedule the delivery by updating ``deferred_ts``.
4. Delivery uses :mod:`aiosmtplib` via :class:`async_mail_service.smtp_pool.SMTPPool`
   so repeated sends within the same asyncio task can reuse the connection.
5. Delivery results are buffered in the ``messages`` table (``sent_ts`` /
   ``error_ts`` / ``error``) and streamed to API consumers through
   :meth:`AsyncMailCore.results`.

.. mermaid::
   :caption: Message delivery sequence

   sequenceDiagram
     participant Client
     participant API as FastAPI
     participant Core as AsyncMailCore
     participant DB as SQLite
     participant SMTP as SMTP Server

     Client->>API: POST /commands/add-messages
     API->>Core: handle_command("addMessages")
     Core->>DB: INSERT into messages
     loop Background SMTP loop
       Core->>DB: SELECT ready messages
       Core->>SMTP: send_message()
       alt Success
         Core->>DB: UPDATE sent_ts
       else Error
         Core->>DB: UPDATE error_ts / error
       end
     end
     Core->>API: results queue / delivery report
     API-->>Client: Deferred status or polling

Client synchronisation
----------------------

The client report loop periodically performs a ``POST`` using
``proxy_sync_url`` (or a custom coroutine) whenever there are rows in
``messages`` with ``sent_ts`` / ``error_ts`` / ``deferred_ts`` but no
``reported_ts``.  The body contains a ``delivery_report`` array with the
current lifecycle state for each message.  Once the upstream service confirms
reception (for example returning ``{"sent": 12, "error": 1, "deferred": 3}``)
the dispatcher stamps ``reported_ts`` and eventually purges those rows when
they age past the configured retention window.
