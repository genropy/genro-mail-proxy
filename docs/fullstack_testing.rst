
Appendix A: Fullstack Testing Infrastructure
============================================

This appendix describes the comprehensive testing infrastructure for genro-mail-proxy,
designed to validate every aspect of the system in an environment that closely
simulates production.

Overview
--------

The fullstack testing infrastructure uses Docker Compose to orchestrate **13 services**
that work together to test:

1. **End-to-end validation**: Complete flow from API request to SMTP delivery
2. **Error simulation**: SMTP servers that simulate various failure scenarios
3. **Multi-tenancy**: Isolated testing for different tenants
4. **Scalability**: Testing with significant message volumes
5. **Integrations**: S3 storage, PostgreSQL, delivery reports

Architecture
------------

.. code-block:: text

                                    ┌─────────────────────────────────────────────────────────────────┐
                                    │                        Client Layer                             │
                                    │                    ┌──────────────────┐                         │
                                    │                    │   API Client     │                         │
                                    │                    │  (pytest/httpx)  │                         │
                                    │                    └────────┬─────────┘                         │
                                    └─────────────────────────────┼───────────────────────────────────┘
                                                                  │ REST API
                                    ┌─────────────────────────────┼───────────────────────────────────┐
                                    │                Application Layer                                │
                                    │                    ┌────────▼─────────┐                         │
                                    │                    │   Mail Proxy     │                         │
                                    │                    │     :8000        │                         │
                                    │                    └────────┬─────────┘                         │
                                    └─────────────────────────────┼───────────────────────────────────┘
                                                                  │
                    ┌─────────────────────────────────────────────┼─────────────────────────────────────────────┐
                    │                                             │                                             │
    ┌───────────────▼───────────────┐           ┌─────────────────▼─────────────────┐           ┌───────────────▼───────────────┐
    │         Data Layer            │           │          SMTP Layer               │           │      Client Endpoints         │
    │  ┌──────────┐  ┌──────────┐   │           │  ┌──────────┐  ┌──────────┐       │           │  ┌──────────┐  ┌──────────┐   │
    │  │PostgreSQL│  │  MinIO   │   │           │  │ MailHog  │  │ MailHog  │       │           │  │ Echo T1  │  │ Echo T2  │   │
    │  │  :5432   │  │  :9000   │   │           │  │   T1     │  │   T2     │       │           │  │  :8081   │  │  :8082   │   │
    │  └──────────┘  └──────────┘   │           │  │  :1025   │  │  :1026   │       │           │  └──────────┘  └──────────┘   │
    └───────────────────────────────┘           │  └──────────┘  └──────────┘       │           │       ┌──────────┐            │
                                                │  ┌──────────┐  ┌──────────┐       │           │       │Attachment│            │
                                                │  │smtp-     │  │smtp-     │       │           │       │ Server   │            │
                                                │  │reject    │  │tempfail  │       │           │       │  :8083   │            │
                                                │  │  :1027   │  │  :1028   │       │           │       └──────────┘            │
                                                │  └──────────┘  └──────────┘       │           └───────────────────────────────┘
                                                │  ┌──────────┐  ┌──────────┐       │
                                                │  │smtp-     │  │smtp-     │       │
                                                │  │timeout   │  │ratelimit │       │
                                                │  │  :1029   │  │  :1030   │       │
                                                │  └──────────┘  └──────────┘       │
                                                │       ┌──────────┐                │
                                                │       │smtp-     │                │
                                                │       │random    │                │
                                                │       │  :1031   │                │
                                                │       └──────────┘                │
                                                └───────────────────────────────────┘


Docker Services
---------------

Database - PostgreSQL
~~~~~~~~~~~~~~~~~~~~~

============= ========================
Parameter     Value
============= ========================
Image         ``postgres:16-alpine``
Port          5432
Database      ``mailproxy``
User          ``mailproxy``
Password      ``testpassword``
Volume        ``pgdata:/var/lib/postgresql/data``
Healthcheck   ``pg_isready -U mailproxy``
============= ========================

**Purpose**: Primary storage for messages, accounts, tenants, and configuration.

Object Storage - MinIO
~~~~~~~~~~~~~~~~~~~~~~

============= ========================
Parameter     Value
============= ========================
Image         ``minio/minio``
Ports         9000 (S3 API), 9001 (Console)
Credentials   ``minioadmin`` / ``minioadmin``
Bucket        ``mail-attachments``
Volume        ``minio-data:/data``
============= ========================

**Purpose**: S3-compatible storage for large file attachments.

**Console UI**: http://localhost:9001

SMTP Servers - MailHog
~~~~~~~~~~~~~~~~~~~~~~

============== ========= ======== ========
Service        SMTP Port API Port Tenant
============== ========= ======== ========
mailhog-tenant1  1025    8025     Tenant 1
mailhog-tenant2  1026    8026     Tenant 2
============== ========= ======== ========

**Purpose**: Capture emails for verification. Each tenant has its own isolated SMTP server.

**Web UI**:

- Tenant 1: http://localhost:8025
- Tenant 2: http://localhost:8026

**API Example**:

.. code-block:: bash

   # List messages
   curl http://localhost:8025/api/v2/messages

   # Delete all
   curl -X DELETE http://localhost:8025/api/v1/messages

Error SMTP Servers
~~~~~~~~~~~~~~~~~~

Custom servers based on ``aiosmtpd`` that simulate various SMTP behaviors:

=============== ===== ============= ======================================
Service         Port  Error Mode    Behavior
=============== ===== ============= ======================================
smtp-reject     1027  ``reject_all``   Always responds ``550 Mailbox not found``
smtp-tempfail   1028  ``temp_fail``    Always responds ``451 Temporary failure``
smtp-timeout    1029  ``timeout``      Waits 30s before responding
smtp-ratelimit  1030  ``rate_limit``   Accepts first 3 messages, then ``452 Too many``
smtp-random     1031  ``random``       Mix: 60% OK, 20% temp, 10% perm, 10% slow
=============== ===== ============= ======================================

**Environment Configuration**:

.. code-block:: yaml

   environment:
     - SMTP_ERROR_MODE=reject_all|temp_fail|timeout|rate_limit|random|none
     - SMTP_RATE_LIMIT=3        # For rate_limit mode
     - SMTP_TIMEOUT_SECONDS=30  # For timeout mode

Echo Servers (Client Endpoints)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

========== ===== ========
Service    Port  Tenant
========== ===== ========
client-tenant1  8081  Tenant 1
client-tenant2  8082  Tenant 2
========== ===== ========

**Image**: ``mendhak/http-https-echo``

**Purpose**: Simulate client endpoints to receive delivery reports.
They respond with an echo of the received request for verification.

Attachment Server
~~~~~~~~~~~~~~~~~

=========== ========================
Parameter   Value
=========== ========================
Image       ``python:3.11-slim``
Port        8083
Command     ``python -m http.server 8080``
Volume      ``./test-attachments:/data:ro``
=========== ========================

**Purpose**: Serve static files for testing HTTP URL attachment fetching.

**Available test files**:

- ``small.txt`` - Small text file
- ``document.html`` - HTML document
- ``large-file.bin`` - 2MB binary file for large file testing

Mail Proxy Service
~~~~~~~~~~~~~~~~~~

=========== ========================
Parameter   Value
=========== ========================
Build       ``Dockerfile.test`` in tests/docker
Port        8000
Database    PostgreSQL (via ``GMP_DB_PATH``)
API Token   ``test-api-token``
=========== ========================

**Environment Variables**:

.. code-block:: yaml

   environment:
     - GMP_DB_PATH=postgresql://mailproxy:testpassword@db:5432/mailproxy
     - GMP_API_TOKEN=test-api-token
     - AWS_ACCESS_KEY_ID=minioadmin
     - AWS_SECRET_ACCESS_KEY=minioadmin
     - AWS_ENDPOINT_URL=http://minio:9000


Test Categories
---------------

The test suite is organized into 17 test classes covering 46 tests total:

========================== ======= ===============================================
Class                      # Tests Description
========================== ======= ===============================================
TestHealthAndBasics        4       Health endpoint, API authentication
TestTenantManagement       4       CRUD tenant via API
TestAccountManagement      2       SMTP account management
TestBasicMessageDispatch   4       Basic email sending (text, HTML, CC/BCC, headers)
TestTenantIsolation        2       Message isolation between tenants
TestBatchOperations        2       Batch enqueue, deduplication
TestAttachmentsBase64      1       Base64 inline attachments
TestPriorityHandling       1       Priority ordering
TestServiceControl         1       Suspend/Activate
TestMetrics                1       Prometheus endpoint
TestValidation             2       Payload validation
TestMessageManagement      2       List/Delete messages
TestInfrastructureCheck    5       Docker service verification
TestSmtpErrorHandling      4       SMTP errors (reject, tempfail, ratelimit, random)
TestRetryLogic             2       Retry count, error details
TestLargeFileStorage       6       S3 upload, link rewrite, reject, warn, mixed
TestTenantLargeFileConfigApi 3     CRUD large_file_config via API
========================== ======= ===============================================

Health & API Basics
~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_health_endpoint_no_auth          - GET /health without auth
   test_status_endpoint_requires_auth    - GET /status requires token
   test_status_endpoint_with_auth        - GET /status with valid token
   test_invalid_token_rejected           - Invalid token → 403

Tenant Management
~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_create_tenant                    - POST /tenants/add
   test_list_tenants                     - GET /tenants/list
   test_get_tenant_details               - GET /tenants/{id}
   test_update_tenant                    - POST /tenants/{id}/update

Message Dispatch
~~~~~~~~~~~~~~~~

.. code-block:: text

   test_send_simple_text_email           - Plain text email
   test_send_html_email                  - HTML email
   test_send_email_with_cc_bcc           - Email with CC and BCC
   test_send_email_with_custom_headers   - Custom headers

SMTP Error Handling
~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_permanent_error_marks_message_failed   - 550 → status error
   test_temporary_error_defers_message         - 451 → status deferred
   test_rate_limited_smtp_defers_excess        - 452 after N messages
   test_random_errors_mixed_results            - Mix of outcomes

Large File Storage
~~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_small_attachment_sent_normally       - Small attachment sent normally
   test_large_attachment_rewritten_to_link   - Large attachment → S3 upload → link
   test_large_attachment_reject_action       - action=reject → message error
   test_large_attachment_warn_action         - action=warn → sent with warning
   test_mixed_attachments_partial_rewrite    - Mix small/large → partial rewrite
   test_verify_file_uploaded_to_minio        - Verify MinIO upload


Running the Tests
-----------------

Prerequisites
~~~~~~~~~~~~~

- Docker and Docker Compose installed
- Python 3.11+ with pytest and httpx
- At least 8GB RAM and 10GB free disk space

Quick Start
~~~~~~~~~~~

.. code-block:: bash

   # Start the infrastructure
   docker compose -f tests/docker/docker-compose.fulltest.yml up -d --build

   # Wait for services to be healthy
   docker compose -f tests/docker/docker-compose.fulltest.yml ps

   # Run the tests
   pytest tests/test_fullstack_integration.py -v

   # Stop the infrastructure
   docker compose -f tests/docker/docker-compose.fulltest.yml down

Using the Runner Script
~~~~~~~~~~~~~~~~~~~~~~~

A convenience script is provided:

.. code-block:: bash

   ./scripts/run-fullstack-tests.sh

This script:

1. Starts the Docker infrastructure
2. Waits for all services to be healthy
3. Runs the test suite
4. Optionally stops the infrastructure

Running Specific Tests
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Run only large file tests
   pytest tests/test_fullstack_integration.py -v -k "LargeFile"

   # Run only SMTP error tests
   pytest tests/test_fullstack_integration.py -v -k "SmtpError"

   # Run tests with fullstack marker
   pytest -m fullstack -v


Test Configuration
------------------

Test Tenants
~~~~~~~~~~~~

The tests automatically create 2 test tenants:

**Tenant 1**:

.. code-block:: json

   {
     "id": "test-tenant-1",
     "name": "Test Tenant 1",
     "client_base_url": "http://client-tenant1:8080",
     "client_sync_path": "/proxy_sync",
     "client_auth": {"method": "none"},
     "active": true
   }

SMTP Account:

.. code-block:: json

   {
     "id": "test-account-1",
     "tenant_id": "test-tenant-1",
     "host": "mailhog-tenant1",
     "port": 1025,
     "use_tls": false
   }

**Tenant 2**:

.. code-block:: json

   {
     "id": "test-tenant-2",
     "name": "Test Tenant 2",
     "client_base_url": "http://client-tenant2:8080",
     "client_sync_path": "/proxy_sync",
     "client_auth": {"method": "bearer", "token": "tenant2-secret-token"},
     "active": true
   }

SMTP Account:

.. code-block:: json

   {
     "id": "test-account-2",
     "tenant_id": "test-tenant-2",
     "host": "mailhog-tenant2",
     "port": 1025,
     "use_tls": false
   }


Extending the Tests
-------------------

Adding a New Test
~~~~~~~~~~~~~~~~~

1. Identify the appropriate class or create a new one
2. Use existing fixtures (``api_client``, ``setup_test_tenants``)
3. Follow the existing pattern:

.. code-block:: python

   async def test_new_feature(self, api_client, setup_test_tenants):
       """Description of the test."""
       ts = int(time.time())
       msg_id = f"new-feature-test-{ts}"

       # Setup
       message = {...}
       resp = await api_client.post("/messages/add", json={"messages": [message]})
       assert resp.status_code == 200

       # Action
       await api_client.post("/run-now")
       await asyncio.sleep(3)

       # Verify
       messages = await wait_for_messages(MAILHOG_TENANT1_API, 1)
       assert len(messages) >= 1

Adding a New Error SMTP Mode
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

1. Modify ``tests/docker/error-smtp/server.py``:

.. code-block:: python

   elif self.error_mode == "new_mode":
       # Implementation
       return "5xx Custom error"

2. Add service in ``docker-compose.fulltest.yml``:

.. code-block:: yaml

   smtp-newmode:
     build:
       context: ./error-smtp
     ports:
       - "1032:1025"
     environment:
       - SMTP_ERROR_MODE=new_mode
     networks:
       - testnet

3. Add constants in ``test_fullstack_integration.py``:

.. code-block:: python

   SMTP_NEWMODE_HOST = "smtp-newmode"
   SMTP_NEWMODE_PORT = 1032


Network Configuration
---------------------

All services are connected to the Docker network ``testnet``:

.. code-block:: yaml

   networks:
     testnet:
       driver: bridge

**Internal communication**: Services reach each other via container name
(e.g., ``db``, ``minio``, ``mailhog-tenant1``).

**Exposed ports to host**:

=================== ========= ===============
Service             Host Port Container Port
=================== ========= ===============
PostgreSQL          5432      5432
MinIO S3            9000      9000
MinIO Console       9001      9001
MailHog T1 SMTP     1025      1025
MailHog T1 API      8025      8025
MailHog T2 SMTP     1026      1025
MailHog T2 API      8026      8025
smtp-reject         1027      1025
smtp-tempfail       1028      1025
smtp-timeout        1029      1025
smtp-ratelimit      1030      1025
smtp-random         1031      1025
Echo T1             8081      8080
Echo T2             8082      8080
Attachment Server   8083      8080
Mail Proxy          8000      8000
=================== ========= ===============


Performance and Limits
----------------------

Recommended Docker Resources
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

- **CPU**: 4 cores
- **RAM**: 8 GB
- **Disk**: 10 GB free

Startup Times
~~~~~~~~~~~~~

======================== ==============
Phase                    Estimated Time
======================== ==============
Pull images (first time) 2-5 min
Build mail-proxy         30-60 sec
Startup services         10-20 sec
Healthcheck complete     30-60 sec
======================== ==============

Known Limitations
~~~~~~~~~~~~~~~~~

- ``smtp-timeout`` with 30s delay can cause test timeouts if not handled
- MailHog does not persist messages on restart
- MinIO runs in standalone mode (not cluster)


Troubleshooting
---------------

Services Not Starting
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Check service status
   docker compose -f tests/docker/docker-compose.fulltest.yml ps

   # Check logs for a specific service
   docker compose -f tests/docker/docker-compose.fulltest.yml logs mailproxy

   # Check all logs
   docker compose -f tests/docker/docker-compose.fulltest.yml logs

Mail Proxy Restarting
~~~~~~~~~~~~~~~~~~~~~

Check the logs:

.. code-block:: bash

   docker compose -f tests/docker/docker-compose.fulltest.yml logs mailproxy --tail 50

Common causes:

- Database not ready (wait for healthcheck)
- Missing environment variables
- Import errors (check if package is installed correctly)

Tests Timing Out
~~~~~~~~~~~~~~~~

- Increase ``asyncio.sleep()`` durations in tests
- Check if ``smtp-timeout`` is involved (30s delay)
- Verify all services are healthy

MinIO Connection Issues
~~~~~~~~~~~~~~~~~~~~~~~

Ensure the bucket is created:

.. code-block:: bash

   # Check minio-setup logs
   docker compose -f tests/docker/docker-compose.fulltest.yml logs minio-setup

The ``minio-setup`` service should create the ``mail-attachments`` bucket automatically.


File Locations
--------------

========================= ================================================
File                      Description
========================= ================================================
``tests/docker/docker-compose.fulltest.yml``  Docker Compose configuration
``tests/docker/Dockerfile.test``              Test-specific Dockerfile
``tests/docker/error-smtp/server.py``         Error SMTP server implementation
``tests/docker/error-smtp/Dockerfile``        Error SMTP Dockerfile
``tests/docker/test-attachments/``            Test attachment files
``tests/test_fullstack_integration.py``       Test suite
``scripts/run-fullstack-tests.sh``            Runner script
========================= ================================================
