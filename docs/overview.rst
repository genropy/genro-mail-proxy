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
* **Fetcher** – pulls new messages from an upstream Genropy endpoint and, when
  delivery reports are available, packages them into the ``proxy_sync`` call
  configured in ``config.ini``.
* **Persistence** – stores SMTP accounts, pending/deferred messages and send
  logs in a SQLite database.
* **RateLimiter** – inspects send logs to determine whether a message needs to
  be deferred.
* **SMTPPool** – keeps SMTP connections warm for the currently executing
  asyncio task.
* **Metrics** – :class:`async_mail_service.prometheus.MailMetrics` exports
  Prometheus counters and gauges.

Request flow
------------

1. A client issues a command through the REST API (for example ``send-message``
   or ``add-messages``).  The API dependency validates ``X-API-Token`` before
   dispatching to :meth:`AsyncMailCore.handle_command`.
2. ``AsyncMailCore`` validates the payload, enqueues messages or triggers the
   immediate send path.  Attachments are normalised by
   :class:`async_mail_service.attachments.AttachmentManager`.
3. Messages processed by the scheduler go through rate limiting.  If the quota
   is exceeded the message is registered in the deferred table and a
   ``deferred`` event is produced for observers (and Prometheus metrics).
4. Delivery uses :mod:`aiosmtplib` via :class:`async_mail_service.smtp_pool.SMTPPool`
   so repeated sends within the same asyncio task can reuse the connection.
5. Delivery results are pushed to the upstream service via
   :meth:`async_mail_service.fetcher.Fetcher.report_delivery` and are also
   available through :meth:`AsyncMailCore.results`.

Fetch synchronisation
---------------------

The dispatcher periodically performs two outbound calls:

* ``GET`` ``/fetch-messages`` – retrieves pending messages that Genropy has
  prepared.
* ``POST`` ``proxy_sync_url`` – sends a JSON body containing ``delivery_report``
  entries; credentials are supplied via HTTP basic authentication using
  ``proxy_sync_user`` and ``proxy_sync_password``.  Genropy can respond with
  a list of message identifiers that were not accepted, prompting the
  dispatcher to retry them in the next cycle.

When the upstream response to ``/commands/add-messages`` carries
``"more_messages": true``, the dispatcher can immediately trigger
``/commands/run-now`` instead of waiting for the next scheduled fetch.

Suggested diagrams
------------------

Read the Docs supports rich diagrams (for example via the ``sphinxcontrib-mermaid``
extension or embedded SVG images).  Adding a sequence diagram covering
``proxySync`` or the delivery flow would make the lifecycle even clearer.
This documentation currently provides a textual overview so it remains useful
even without optional diagram extensions.
