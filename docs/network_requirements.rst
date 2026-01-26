Network Requirements
====================

This document describes the network connectivity requirements for deploying
genro-mail-proxy in production environments, including firewall rules, port
configurations, and network topology.

.. contents::
   :local:
   :depth: 2

Network Architecture
--------------------

genro-mail-proxy acts as a middleware service between your your application application
and external SMTP servers. Understanding the network flows is essential for
proper firewall configuration and security planning.

Network Flow Diagram
^^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   ┌─────────────────────────────────────────────────────────────┐
   │                    Network Topology                          │
   └─────────────────────────────────────────────────────────────┘

   ┌──────────────────┐                    ┌──────────────────┐
   │                  │ ➊ REST API         │                  │
   │    your application       │───────────────────>│ genro-mail-proxy │
   │  Application     │   (HTTP/HTTPS)     │                  │
   │                  │                    │                  │
   │                  │ ➋ Delivery Reports │                  │
   │                  │<───────────────────│                  │
   │                  │   (HTTP/HTTPS)     │                  │
   └──────────────────┘                    └────────┬─────────┘
                                                    │
                                                    │ ➌ SMTP
                                                    │  (SMTP/SMTPS)
                                                    ▼
                                           ┌──────────────────┐
                                           │  SMTP Servers    │
                                           │  (Gmail, SES,    │
                                           │   SendGrid...)   │
                                           └──────────────────┘

Connection Details
------------------

1. your application → genro-mail-proxy (REST API)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Direction:** Inbound to genro-mail-proxy

**Protocol:** HTTP or HTTPS

**Default Port:** 8000 (configurable via ``GMP_PORT``)

**Authentication:** X-API-Token header (configured via ``GMP_API_TOKEN``)

**Endpoints Used:**

- ``POST /commands/add-messages`` - Submit messages to queue
- ``POST /commands/run-now`` - Trigger immediate dispatch
- ``POST /account`` - Configure SMTP accounts
- ``DELETE /account/{id}`` - Remove SMTP accounts
- ``GET /accounts`` - List configured accounts
- ``GET /messages`` - Query message queue
- ``GET /status`` - Health check
- ``GET /metrics`` - Prometheus metrics

**Firewall Rules:**

.. code-block:: text

   # Allow your application instances to reach genro-mail-proxy
   Source: your application servers (e.g., 10.0.1.0/24)
   Destination: genro-mail-proxy (e.g., 10.0.2.10)
   Port: 8000/tcp (or configured GMP_PORT)
   Protocol: TCP
   Action: ALLOW

**Security Considerations:**

- Use HTTPS in production (configure via reverse proxy like nginx)
- Set strong ``GMP_API_TOKEN`` (minimum 32 characters)
- Consider network segmentation (genro-mail-proxy in DMZ or separate subnet)
- Enable firewall rules to restrict access to known your application IPs only

**Client Configuration:**

The your application client must be configured with:

- **Mail Proxy URL**: The base URL where genro-mail-proxy is accessible (e.g., ``http://mail-proxy.internal:8000``)
- **API Token**: The authentication token matching ``GMP_API_TOKEN`` on the proxy server

The client uses these parameters when making REST API calls to the proxy endpoints.
See ``example_client.py`` for a reference implementation showing how to configure
and use these parameters in your client application.

2. genro-mail-proxy → your application (Delivery Reports)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Direction:** Outbound from genro-mail-proxy

**Protocol:** HTTP or HTTPS

**Port:** Configurable (typically 80/443 or custom application port)

**Authentication:**
- Basic Auth (``GMP_CLIENT_SYNC_USER`` + ``GMP_CLIENT_SYNC_PASSWORD``)
- OR Token-based (``GMP_CLIENT_SYNC_TOKEN``)

**Endpoint:** Configured via ``GMP_CLIENT_SYNC_URL``

**Typical Endpoint:** ``/email/mailproxy/mp_endpoint/proxy_sync``

**Purpose:**
genro-mail-proxy periodically reports message delivery status back to your application
(sent, error, deferred). This allows your application to update its database and track
email delivery lifecycle.

**Firewall Rules:**

.. code-block:: text

   # Allow genro-mail-proxy to report back to your application
   Source: genro-mail-proxy (e.g., 10.0.2.10)
   Destination: your application servers (e.g., 10.0.1.0/24)
   Port: Application port (e.g., 8080/tcp)
   Protocol: TCP
   Action: ALLOW

**Payload Example:**

.. code-block:: json

   {
     "delivery_report": [
       {
         "id": "MSG-001",
         "account_id": "smtp-main",
         "priority": 1,
         "sent_ts": 1735689600,
         "error_ts": null,
         "error": null,
         "deferred_ts": null
       }
     ]
   }

**Configuration:**

.. code-block:: bash

   # Environment variables
   export GMP_CLIENT_SYNC_URL="http://app.internal:8080/email/mailproxy/mp_endpoint/proxy_sync"
   export GMP_CLIENT_SYNC_USER="syncuser"
   export GMP_CLIENT_SYNC_PASSWORD="syncpass"

   # Or use token
   export GMP_CLIENT_SYNC_TOKEN="your-sync-token"

3. genro-mail-proxy → SMTP Servers
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Direction:** Outbound from genro-mail-proxy

**Protocol:** SMTP, SMTP+STARTTLS, or SMTPS

**Ports:**

- **25/tcp** - SMTP (unencrypted, rarely used in production)
- **587/tcp** - SMTP with STARTTLS (submission, **recommended**)
- **465/tcp** - SMTPS (implicit TLS, legacy but still used)

**Authentication:** Per-account credentials (username/password)

**DNS Requirements:**
genro-mail-proxy needs to resolve SMTP server hostnames (e.g., smtp.gmail.com,
email-smtp.eu-west-1.amazonaws.com)

**Firewall Rules:**

.. code-block:: text

   # Allow genro-mail-proxy to reach SMTP servers
   Source: genro-mail-proxy (e.g., 10.0.2.10)
   Destination: Internet SMTP servers (any)
   Ports: 25/tcp, 587/tcp, 465/tcp
   Protocol: TCP
   Action: ALLOW

**Common SMTP Providers:**

.. list-table::
   :header-rows: 1
   :widths: 20 30 15 15

   * - Provider
     - Hostname
     - Port
     - Use TLS
   * - Gmail
     - smtp.gmail.com
     - 587
     - false (STARTTLS)
   * - SendGrid
     - smtp.sendgrid.net
     - 587
     - false (STARTTLS)
   * - AWS SES
     - email-smtp.REGION.amazonaws.com
     - 587
     - false (STARTTLS)
   * - Mailgun
     - smtp.mailgun.org
     - 587
     - false (STARTTLS)
   * - Office 365
     - smtp.office365.com
     - 587
     - false (STARTTLS)

**SMTP Account Configuration:**

.. code-block:: bash

   # Add SMTP account via API
   curl -X POST http://localhost:8000/account \\
     -H "X-API-Token: your-token" \\
     -H "Content-Type: application/json" \\
     -d '{
       "id": "smtp-main",
       "host": "smtp.gmail.com",
       "port": 587,
       "user": "your-email@gmail.com",
       "password": "app-specific-password",
       "use_tls": false
     }'

Port Configuration
------------------

genro-mail-proxy Service Port
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

**Environment Variable:** ``GMP_PORT``

**Default:** 8000

**Recommendation:**
Use the default 8000 for internal services. If exposed externally, use a
reverse proxy (nginx, Traefik) on standard ports 80/443.

**Docker Mapping:**

.. code-block:: bash

   # Map container port to host
   docker run -p 8000:8000 genro-mail-proxy

   # Or use custom port
   docker run -p 9000:8000 -e GMP_PORT=8000 genro-mail-proxy

**Kubernetes Service:**

.. code-block:: yaml

   apiVersion: v1
   kind: Service
   metadata:
     name: genro-mail-proxy
   spec:
     selector:
       app: genro-mail-proxy
     ports:
       - port: 8000
         targetPort: 8000
         protocol: TCP

Deployment Scenarios
--------------------

Scenario 1: Single Host (Development)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   ┌────────────────────────────────────────┐
   │         Single Server (localhost)      │
   │                                        │
   │  ┌──────────┐      ┌──────────────┐  │
   │  │ your application  │─────>│ mail-proxy   │──┼─> Internet
   │  │  :8080   │      │   :8000      │  │   (SMTP)
   │  └──────────┘      └──────────────┘  │
   └────────────────────────────────────────┘

**Network Requirements:**

- No firewall rules needed (localhost)
- Both services on same machine
- Use 127.0.0.1 or localhost for URLs

**Configuration:**

.. code-block:: bash

   # your application config
   GMP_CLIENT_SYNC_URL=http://127.0.0.1:8080/email/mailproxy/mp_endpoint/proxy_sync

Scenario 2: Separate Hosts (Production)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   ┌──────────────┐         ┌──────────────┐
   │   your application    │         │ mail-proxy   │
   │  10.0.1.10   │────────>│  10.0.2.10   │───> Internet
   │   :8080      │         │    :8000     │     (SMTP)
   └──────────────┘         └──────────────┘
         ▲                          │
         └──────────────────────────┘
              (delivery reports)

**Network Requirements:**

1. your application → mail-proxy: Allow 10.0.1.10 → 10.0.2.10:8000
2. mail-proxy → your application: Allow 10.0.2.10 → 10.0.1.10:8080
3. mail-proxy → Internet: Allow outbound 587/tcp

**Firewall Configuration (iptables example):**

.. code-block:: bash

   # On genro-mail-proxy host
   # Allow inbound API from your application
   iptables -A INPUT -s 10.0.1.10 -p tcp --dport 8000 -j ACCEPT

   # Allow outbound to your application
   iptables -A OUTPUT -d 10.0.1.10 -p tcp --dport 8080 -j ACCEPT

   # Allow outbound SMTP
   iptables -A OUTPUT -p tcp --dport 587 -j ACCEPT

Scenario 3: Kubernetes Cluster
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: text

   ┌─────────────────────────────────────────────┐
   │          Kubernetes Cluster                  │
   │                                              │
   │  ┌──────────────┐    ┌──────────────┐      │
   │  │ app-svc  │───>│ mail-proxy   │──────┼──> Internet
   │  │ ClusterIP    │    │   Service    │      │    (SMTP)
   │  └──────────────┘    └──────────────┘      │
   └─────────────────────────────────────────────┘

**Network Requirements:**

- Services communicate via Kubernetes ClusterIP
- NetworkPolicies may restrict pod-to-pod communication
- Egress for SMTP traffic must be allowed

**NetworkPolicy Example:**

.. code-block:: yaml

   apiVersion: networking.k8s.io/v1
   kind: NetworkPolicy
   metadata:
     name: genro-mail-proxy-policy
   spec:
     podSelector:
       matchLabels:
         app: genro-mail-proxy
     policyTypes:
       - Ingress
       - Egress
     ingress:
       # Allow from your application pods
       - from:
         - podSelector:
             matchLabels:
               app: myapp
         ports:
         - protocol: TCP
           port: 8000
     egress:
       # Allow to your application pods
       - to:
         - podSelector:
             matchLabels:
               app: myapp
         ports:
         - protocol: TCP
           port: 8080
       # Allow DNS
       - to:
         - namespaceSelector: {}
         ports:
         - protocol: UDP
           port: 53
       # Allow SMTP
       - to:
         - namespaceSelector: {}
         ports:
         - protocol: TCP
           port: 587

**Service Configuration:**

.. code-block:: bash

   # genro-mail-proxy uses service DNS name
   export GMP_CLIENT_SYNC_URL="http://app-svc.default.svc.cluster.local:8080/email/mailproxy/mp_endpoint/proxy_sync"

Security Considerations
-----------------------

TLS/SSL Configuration
^^^^^^^^^^^^^^^^^^^^^

**For API Communication (your application ↔ genro-mail-proxy):**

genro-mail-proxy does not natively support HTTPS. Use a reverse proxy:

.. code-block:: nginx

   # /etc/nginx/sites-available/mail-proxy
   server {
       listen 443 ssl http2;
       server_name mail-proxy.internal;

       ssl_certificate /etc/ssl/certs/mail-proxy.crt;
       ssl_certificate_key /etc/ssl/private/mail-proxy.key;

       location / {
           proxy_pass http://localhost:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
           proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
           proxy_set_header X-Forwarded-Proto $scheme;
       }
   }

**For SMTP Communication:**

Configure ``use_tls: false`` for STARTTLS (port 587) or ``use_tls: true`` for
implicit TLS (port 465).

Authentication
^^^^^^^^^^^^^^

**API Authentication:**

Always set ``GMP_API_TOKEN`` in production:

.. code-block:: bash

   # Generate strong token
   export GMP_API_TOKEN=$(openssl rand -hex 32)

**SMTP Authentication:**

Store SMTP credentials securely:

- Use environment variables instead of config file
- Consider secrets management (Vault, AWS Secrets Manager)
- Rotate credentials periodically

**your application Sync Authentication:**

Use token-based auth when possible (``GMP_CLIENT_SYNC_TOKEN``) instead of
basic auth for better security.

Network Isolation
^^^^^^^^^^^^^^^^^

**Recommended Network Segmentation:**

.. code-block:: text

   ┌─────────────────┐
   │  Application    │  Private subnet: 10.0.1.0/24
   │  Tier           │
   │  (your application)      │
   └────────┬────────┘
            │
   ┌────────▼────────┐
   │  Middleware     │  Private subnet: 10.0.2.0/24
   │  Tier           │  (no direct internet)
   │  (mail-proxy)   │
   └────────┬────────┘
            │
   ┌────────▼────────┐
   │  NAT Gateway /  │  Public subnet: 10.0.3.0/24
   │  Proxy          │
   │  (outbound only)│
   └─────────────────┘
            │
            ▼
      [ Internet ]

Troubleshooting
---------------

Connection Testing
^^^^^^^^^^^^^^^^^^

**Test API Connectivity:**

.. code-block:: bash

   # From your application server
   curl -H "X-API-Token: your-token" http://mail-proxy:8000/status

**Test SMTP Connectivity:**

.. code-block:: bash

   # From genro-mail-proxy server
   telnet smtp.gmail.com 587

**Test your application Callback:**

.. code-block:: bash

   # From genro-mail-proxy server
   curl -u syncuser:syncpass \\
     http://myapp:8080/email/mailproxy/mp_endpoint/proxy_sync \\
     -H "Content-Type: application/json" \\
     -d '{"delivery_report": []}'

Common Issues
^^^^^^^^^^^^^

**Issue: Connection refused to genro-mail-proxy**

.. code-block:: text

   Solution:
   1. Check firewall allows port 8000
   2. Verify service is running: docker ps
   3. Check GMP_HOST is 0.0.0.0 (not 127.0.0.1)

**Issue: Cannot reach SMTP servers**

.. code-block:: text

   Solution:
   1. Check outbound firewall for port 587
   2. Verify DNS resolution: nslookup smtp.gmail.com
   3. Check proxy/NAT configuration

**Issue: Delivery reports not reaching your application**

.. code-block:: text

   Solution:
   1. Verify GMP_CLIENT_SYNC_URL is correct
   2. Check your application endpoint is accessible
   3. Verify authentication credentials
   4. Check your application logs for incoming requests

Monitoring and Logging
-----------------------

**Enable Network Logging:**

.. code-block:: bash

   export GMP_LOG_LEVEL=DEBUG
   export GMP_LOG_DELIVERY_ACTIVITY=true

**Monitor Connections:**

.. code-block:: bash

   # Active connections
   netstat -an | grep :8000

   # SMTP connections
   netstat -an | grep :587

**Prometheus Metrics:**

.. code-block:: bash

   # Check metrics endpoint
   curl http://localhost:8000/metrics

Summary Checklist
-----------------

Before deploying genro-mail-proxy, ensure:

☑ **Firewall Rules:**
  - ✅ your application → genro-mail-proxy (port 8000)
  - ✅ genro-mail-proxy → your application (application port)
  - ✅ genro-mail-proxy → Internet SMTP (port 587)

☑ **DNS Resolution:**
  - ✅ SMTP server hostnames resolve correctly

☑ **Authentication:**
  - ✅ ``GMP_API_TOKEN`` configured
  - ✅ ``GMP_CLIENT_SYNC_USER`` + ``GMP_CLIENT_SYNC_PASSWORD`` configured
  - ✅ SMTP account credentials configured via API

☑ **Configuration:**
  - ✅ ``GMP_HOST=0.0.0.0`` (or specific interface)
  - ✅ ``GMP_PORT`` matches firewall rules
  - ✅ ``GMP_CLIENT_SYNC_URL`` points to correct your application endpoint

☑ **Testing:**
  - ✅ API health check succeeds: ``GET /status``
  - ✅ Can send test message: ``POST /commands/add-messages``
  - ✅ SMTP connection works (check logs)
  - ✅ Delivery reports reach your application

See Also
--------

- :doc:`installation` - Deployment guide
- :doc:`usage` - API reference and configuration
- :doc:`architecture_overview` - High-level architecture
- ``TROUBLESHOOTING.md`` - Debugging guide
