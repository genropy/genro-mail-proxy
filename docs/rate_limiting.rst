
Rate Limiting
=============

genro-mail-proxy provides per-account rate limiting to prevent exceeding
SMTP provider limits and ensure fair resource distribution across tenants.

How It Works
------------

The rate limiter uses a **sliding window algorithm** backed by persistent
send logs. For each SMTP account, you can configure limits at three
granularities:

- **Per minute**: Maximum messages per 60-second window
- **Per hour**: Maximum messages per 3600-second window
- **Per day**: Maximum messages per 86400-second window

When a message is ready to send, the limiter checks each configured limit
in order (minute → hour → day). If any limit is exceeded, the message is
**deferred** (not rejected) to the next window boundary.

.. code-block:: text

   Message ready to send
           │
           ▼
   ┌───────────────────┐
   │ Check minute limit │──exceeded──► Defer to next minute
   └─────────┬─────────┘
             │ ok
             ▼
   ┌───────────────────┐
   │ Check hour limit   │──exceeded──► Defer to next hour
   └─────────┬─────────┘
             │ ok
             ▼
   ┌───────────────────┐
   │ Check day limit    │──exceeded──► Defer to next day
   └─────────┬─────────┘
             │ ok
             ▼
        Send message


Configuration
-------------

Rate limits are configured per SMTP account. Set them when creating or
updating an account:

**Via CLI**:

.. code-block:: bash

   mail-proxy myserver acme accounts add \
     --host smtp.example.com \
     --port 587 \
     --user sender@example.com \
     --password secret \
     --limit-minute 10 \
     --limit-hour 100 \
     --limit-day 1000

**Via REST API**:

.. code-block:: bash

   curl -X POST http://localhost:8000/account \
     -H "X-API-Token: $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "id": "primary-smtp",
       "tenant_id": "acme",
       "host": "smtp.example.com",
       "port": 587,
       "user": "sender@example.com",
       "password": "secret",
       "use_tls": true,
       "limit_per_minute": 10,
       "limit_per_hour": 100,
       "limit_per_day": 1000
     }'

**Via Python Client**:

.. code-block:: python

   from mail_proxy.client import MailProxyClient

   client = MailProxyClient("http://localhost:8000", token="your-token")
   client.accounts.create(
       id="primary-smtp",
       tenant_id="acme",
       host="smtp.example.com",
       port=587,
       user="sender@example.com",
       password="secret",
       use_tls=True,
       limit_per_minute=10,
       limit_per_hour=100,
       limit_per_day=1000,
   )


Deferred Messages
-----------------

When rate limited, messages are not rejected—they're deferred. The message's
``deferred_ts`` field is set to the timestamp when it can be retried:

- **Minute limit exceeded**: Deferred to next minute boundary
- **Hour limit exceeded**: Deferred to next hour boundary
- **Day limit exceeded**: Deferred to next day boundary (midnight UTC)

The dispatcher automatically picks up deferred messages when their time arrives.

**Checking deferred messages**:

.. code-block:: bash

   # List messages with deferred_ts set
   curl "http://localhost:8000/messages?tenant_id=acme" \
     -H "X-API-Token: $TOKEN" | jq '.messages[] | select(.deferred_ts != null)'


Recommended Limits
------------------

Limits depend on your SMTP provider. Here are common configurations:

.. list-table::
   :header-rows: 1
   :widths: 30 20 20 20

   * - Provider
     - Per Minute
     - Per Hour
     - Per Day
   * - Amazon SES (sandbox)
     - 1
     - 200
     - 200
   * - Amazon SES (production)
     - 14
     - 840
     - 50,000+
   * - SendGrid (free)
     - 1
     - 100
     - 100
   * - SendGrid (paid)
     - 10
     - 600
     - varies
   * - Mailgun (free)
     - 5
     - 300
     - 5,000
   * - SMTP2GO (free)
     - 2
     - 100
     - 1,000
   * - Self-hosted (Postfix)
     - 30
     - 1,000
     - unlimited

**Rule of thumb**: Set limits slightly below your provider's actual limits
to account for other applications using the same account.


Global vs Per-Instance Limits
-----------------------------

Rate limits are **global across all instances** sharing the same database.
Send counts are stored in the ``send_log`` table and visible to all instances.

This means:

- Multiple service instances share the same rate limit pool
- You don't need to divide limits by instance count
- Failover to another instance won't reset counters

.. code-block:: text

   Instance A ──┬──► Database ◄──┬── Instance B
                │    send_log    │
                │                │
                └───────┬────────┘
                        │
                Shared rate limit tracking


Monitoring Rate Limits
----------------------

The service exposes Prometheus metrics for rate limiting:

.. code-block:: text

   # Counter: incremented each time rate limiting triggers
   gmp_rate_limited_total{account_id="primary-smtp"} 42

   # Gauge: currently deferred messages in queue
   gmp_deferred_total{account_id="primary-smtp"} 5

Query in Grafana:

.. code-block:: promql

   # Rate limit hits per minute
   rate(gmp_rate_limited_total[1m])

   # Percentage of messages rate-limited
   rate(gmp_rate_limited_total[5m]) / rate(gmp_sent_total[5m]) * 100


Disabling Rate Limits
---------------------

To disable rate limiting for an account, either:

1. Don't set any ``limit_per_*`` fields when creating the account
2. Set all limits to 0 or null

.. code-block:: bash

   # Create account without limits
   curl -X POST http://localhost:8000/account \
     -H "X-API-Token: $TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "id": "unlimited-smtp",
       "tenant_id": "acme",
       "host": "smtp.example.com",
       "port": 587
     }'


Best Practices
--------------

1. **Start conservative**: Begin with lower limits and increase based on
   monitoring. It's easier to raise limits than to deal with provider blocks.

2. **Use multiple accounts**: Spread load across accounts if you need higher
   throughput. Each account has independent limits.

3. **Monitor deferred counts**: High ``gmp_deferred_total`` indicates limits
   are too tight for your volume.

4. **Set all three levels**: Minute limits smooth bursts, hour limits prevent
   sustained spikes, day limits respect provider quotas.

5. **Account for retries**: Failed messages retry, consuming rate limit slots.
   Factor this into your limits.

.. code-block:: python

   # Example: High-volume setup with multiple accounts
   accounts = [
       {"id": "smtp-1", "limit_per_minute": 10, "limit_per_hour": 500},
       {"id": "smtp-2", "limit_per_minute": 10, "limit_per_hour": 500},
       {"id": "smtp-3", "limit_per_minute": 10, "limit_per_hour": 500},
   ]
   # Effective rate: 30/min, 1500/hour across all accounts


See Also
--------

- :doc:`usage` for configuration options
- :doc:`monitoring` for Prometheus metrics setup
- :doc:`multi_tenancy` for per-tenant account isolation
- :doc:`faq` for troubleshooting rate limit issues
