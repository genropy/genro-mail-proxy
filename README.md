[![Documentation Status](https://readthedocs.org/projects/gnr-async-mail-service/badge/?version=latest)](https://gnr-async-mail-service.readthedocs.io/en/latest/)

# gnr-async-mail-service

**Authors:** Softwell S.r.l. - Giovanni Porcari  
**License:** MIT

Asynchronous email dispatcher microservice with scheduling, rate limiting, attachments (S3/URL/base64), REST API (FastAPI), and Prometheus metrics.

Main integration points:

- REST control plane secured by ``X-API-Token`` for queue management and configuration.
- Outbound ``proxy_sync`` call towards Genropy, authenticated via basic auth and configured through ``[sync]`` in ``config.ini``.
- Delivery reports and Prometheus metrics to monitor message lifecycle and rate limiting.
- Unified SQLite storage with a single ``messages`` table that tracks queue state (`priority`, `deferred_ts`) and delivery lifecycle (`sent_ts`, `error_ts`, `reported_ts`), removing the legacy `pending_messages`, `deferred_messages`, `queued_messages`, and `delivery_reports` tables.
- Background loops:
  - **SMTP dispatch loop** selects records from ``messages`` that lack ``sent_ts``/``error_ts`` and have ``deferred_ts`` in the past, enforces rate limits, then stamps ``sent_ts`` or ``error_ts``/``error``.
  - **Client report loop** batches completed items (sent/error/deferred) that are still missing ``reported_ts`` and posts them to the upstream ``proxy_sync`` endpoint; on acknowledgement the records receive ``reported_ts`` and are later purged according to the retention window.

## Quick start

```bash
docker build -t gnr-async-mail-service .
docker run -p 8000:8000 -e SMTP_USER=... -e SMTP_PASSWORD=... -e FETCH_URL=https://your/api gnr-async-mail-service
```

## Configuration highlights

- ``[delivery]`` now exposes ``delivery_report_retention_seconds`` to control how long reported messages stay in the ``messages`` table (default seven days).
- ``/commands/add-messages`` validates each payload (``id``, ``from``, ``to`` etc.), enqueues valid messages with `priority=2` when omitted, and returns a response with queued count plus a `rejected` list containing `{"id","reason"}` entries for failures.
- Legacy endpoints `/commands/send-message`, `/pending`, and `/deferred` have been removed in favour of the richer state served by `/messages`.
