
Monitoring
==========

genro-mail-proxy exposes Prometheus metrics and health endpoints for
comprehensive monitoring and alerting.

Prometheus Metrics
------------------

The ``/metrics`` endpoint returns metrics in Prometheus text exposition format.
**No authentication required** for this endpoint—secure it via firewall rules
or reverse proxy.

.. code-block:: bash

   curl http://localhost:8000/metrics

Available Metrics
~~~~~~~~~~~~~~~~~

All metrics use the ``gmp_`` prefix (genro-mail-proxy):

.. list-table::
   :header-rows: 1
   :widths: 35 15 50

   * - Metric
     - Type
     - Description
   * - ``gmp_sent_total``
     - Counter
     - Successfully sent emails (by ``account_id``)
   * - ``gmp_errors_total``
     - Counter
     - Permanently failed emails (by ``account_id``)
   * - ``gmp_deferred_total``
     - Counter
     - Temporarily deferred emails (by ``account_id``)
   * - ``gmp_rate_limited_total``
     - Counter
     - Rate limit enforcement events (by ``account_id``)
   * - ``gmp_pending_messages``
     - Gauge
     - Current number of messages in queue

Sample Output
~~~~~~~~~~~~~

.. code-block:: text

   # HELP gmp_sent_total Total sent emails
   # TYPE gmp_sent_total counter
   gmp_sent_total{account_id="primary-smtp"} 1523.0
   gmp_sent_total{account_id="bulk-smtp"} 45678.0

   # HELP gmp_errors_total Total send errors
   # TYPE gmp_errors_total counter
   gmp_errors_total{account_id="primary-smtp"} 12.0
   gmp_errors_total{account_id="bulk-smtp"} 89.0

   # HELP gmp_deferred_total Total deferred emails
   # TYPE gmp_deferred_total counter
   gmp_deferred_total{account_id="primary-smtp"} 5.0
   gmp_deferred_total{account_id="bulk-smtp"} 234.0

   # HELP gmp_rate_limited_total Total rate limited occurrences
   # TYPE gmp_rate_limited_total counter
   gmp_rate_limited_total{account_id="primary-smtp"} 0.0
   gmp_rate_limited_total{account_id="bulk-smtp"} 156.0

   # HELP gmp_pending_messages Current pending messages
   # TYPE gmp_pending_messages gauge
   gmp_pending_messages 42.0


Prometheus Configuration
------------------------

Add the mail-proxy to your Prometheus scrape configuration:

.. code-block:: yaml

   # prometheus.yml
   scrape_configs:
     - job_name: 'mail-proxy'
       scrape_interval: 15s
       static_configs:
         - targets: ['mail-proxy:8000']
       metrics_path: /metrics

For Kubernetes with service discovery:

.. code-block:: yaml

   scrape_configs:
     - job_name: 'mail-proxy'
       kubernetes_sd_configs:
         - role: pod
       relabel_configs:
         - source_labels: [__meta_kubernetes_pod_label_app]
           regex: mail-proxy
           action: keep


Grafana Dashboard
-----------------

Import or create a dashboard with these panels:

**Send Rate**:

.. code-block:: promql

   rate(gmp_sent_total[5m])

**Error Rate**:

.. code-block:: promql

   rate(gmp_errors_total[5m])

**Success Rate (percentage)**:

.. code-block:: promql

   rate(gmp_sent_total[5m]) /
   (rate(gmp_sent_total[5m]) + rate(gmp_errors_total[5m])) * 100

**Queue Depth**:

.. code-block:: promql

   gmp_pending_messages

**Rate Limit Hits (per minute)**:

.. code-block:: promql

   increase(gmp_rate_limited_total[1m])

**Throughput by Account**:

.. code-block:: promql

   sum by(account_id) (rate(gmp_sent_total[5m]))


You can find an example dashboard inside examples/grafana_dashboard.

   
Alerting Rules
--------------

Example Prometheus alerting rules:

.. code-block:: yaml

   # alerts.yml
   groups:
     - name: mail-proxy
       rules:
         # High error rate
         - alert: MailProxyHighErrorRate
           expr: |
             rate(gmp_errors_total[5m]) /
             (rate(gmp_sent_total[5m]) + rate(gmp_errors_total[5m])) > 0.1
           for: 5m
           labels:
             severity: warning
           annotations:
             summary: "Mail proxy error rate above 10%"
             description: "{{ $labels.account_id }} has {{ $value | humanizePercentage }} error rate"

         # Queue backing up
         - alert: MailProxyQueueBacklog
           expr: gmp_pending_messages > 1000
           for: 10m
           labels:
             severity: warning
           annotations:
             summary: "Mail proxy queue backlog"
             description: "{{ $value }} messages pending for over 10 minutes"

         # Rate limiting active
         - alert: MailProxyRateLimited
           expr: increase(gmp_rate_limited_total[5m]) > 100
           for: 5m
           labels:
             severity: info
           annotations:
             summary: "Mail proxy hitting rate limits"
             description: "{{ $labels.account_id }} rate limited {{ $value }} times in 5m"

         # Service down
         - alert: MailProxyDown
           expr: up{job="mail-proxy"} == 0
           for: 1m
           labels:
             severity: critical
           annotations:
             summary: "Mail proxy is down"


Health Endpoints
----------------

Two health endpoints are available:

``GET /health``
~~~~~~~~~~~~~~~

Lightweight health check. Returns 200 OK if the service is running.
**No authentication required**.

.. code-block:: bash

   curl http://localhost:8000/health

Response:

.. code-block:: json

   {"ok": true}

Use this for load balancer health checks and Kubernetes liveness probes.

``GET /status``
~~~~~~~~~~~~~~~

Detailed status including scheduler state. **Requires authentication**.

.. code-block:: bash

   curl http://localhost:8000/status -H "X-API-Token: $TOKEN"

Response:

.. code-block:: json

   {
     "ok": true,
     "scheduler_active": true,
     "pending_messages": 42,
     "uptime_seconds": 86400
   }


Kubernetes Probes
-----------------

Configure probes in your deployment:

.. code-block:: yaml

   apiVersion: apps/v1
   kind: Deployment
   spec:
     template:
       spec:
         containers:
           - name: mail-proxy
             livenessProbe:
               httpGet:
                 path: /health
                 port: 8000
               initialDelaySeconds: 10
               periodSeconds: 10
             readinessProbe:
               httpGet:
                 path: /health
                 port: 8000
               initialDelaySeconds: 5
               periodSeconds: 5


Docker Compose Health Check
---------------------------

.. code-block:: yaml

   services:
     mail-proxy:
       image: genro-mail-proxy
       healthcheck:
         test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
         interval: 30s
         timeout: 10s
         retries: 3
         start_period: 10s


Logging
-------

The service logs to stdout with Python's logging module. Key log events:

- **INFO**: Service start/stop, message sent, delivery report posted
- **WARNING**: Rate limit hit, temporary SMTP error, retry scheduled
- **ERROR**: Permanent SMTP error, attachment fetch failure

Configure log level via environment:

.. code-block:: bash

   export LOG_LEVEL=DEBUG
   uvicorn core.mail_proxy.server:app --host 0.0.0.0

Or in Docker:

.. code-block:: yaml

   services:
     mail-proxy:
       environment:
         - LOG_LEVEL=INFO


Log Format
~~~~~~~~~~

Default format includes timestamp, level, and message:

.. code-block:: text

   2025-01-23 10:15:32,456 INFO     Message msg-123 sent via primary-smtp
   2025-01-23 10:15:33,789 WARNING  Rate limit reached for bulk-smtp, deferring msg-456
   2025-01-23 10:15:34,012 ERROR    SMTP error for msg-789: 550 Mailbox not found


Structured Logging (JSON)
~~~~~~~~~~~~~~~~~~~~~~~~~

For log aggregation systems, use JSON output. Set environment variable:

.. code-block:: bash

   export LOG_FORMAT=json

Output:

.. code-block:: json

   {"timestamp": "2025-01-23T10:15:32.456Z", "level": "INFO", "message": "Message msg-123 sent", "account_id": "primary-smtp"}


Best Practices
--------------

1. **Scrape frequently**: 15-second intervals catch transient issues.

2. **Alert on trends, not spikes**: Use ``for: 5m`` to avoid false positives.

3. **Monitor queue depth**: Rising ``gmp_pending_messages`` indicates
   throughput issues.

4. **Track per-account metrics**: Different accounts may have different
   error patterns.

5. **Correlate with SMTP provider**: Match your metrics with provider's
   dashboard for root cause analysis.

6. **Secure /metrics**: Even without auth, the endpoint reveals account IDs.
   Use firewall rules or reverse proxy.


See Also
--------

- :doc:`rate_limiting` for understanding rate limit metrics
- :doc:`usage` for configuration options
- :doc:`network_requirements` for firewall and port configuration
