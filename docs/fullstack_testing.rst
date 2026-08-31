
Fullstack Integration Testing
=============================

This document describes the comprehensive fullstack testing infrastructure for
genro-mail-proxy. These tests validate every aspect of the system in an environment
that closely simulates production.

.. contents:: Table of Contents
   :local:
   :depth: 2

Overview
--------

The fullstack test suite consists of **120 tests** organized in **7 groups** that
validate the complete mail proxy functionality:

- End-to-end message flow from API to SMTP delivery
- Multi-tenant isolation and security
- Error handling and retry logic
- Attachment processing (base64, HTTP, S3)
- Bounce detection via IMAP polling
- Rate limiting and service control
- Delivery reports to client endpoints

Test Structure
--------------

Tests are organized in numbered groups to ensure proper execution order:

.. code-block:: text

   tests/fullstack/
   ├── conftest.py              # Shared fixtures
   ├── helpers.py               # Helper functions and constants
   │
   ├── 00_core/                 # Core functionality (22 tests)
   │   ├── test_00_health.py        # Health endpoint, auth
   │   ├── test_05_docker_integration.py  # Docker services
   │   ├── test_10_infrastructure.py      # PostgreSQL, MinIO, MailHog
   │   ├── test_20_tenants.py       # Tenant CRUD
   │   └── test_30_accounts.py      # Account CRUD
   │
   ├── 10_messaging/            # Message handling (16 tests)
   │   ├── test_00_validation.py    # Input validation
   │   ├── test_10_dispatch.py      # Basic dispatch
   │   ├── test_20_messages.py      # Message API
   │   ├── test_30_batch.py         # Batch operations
   │   └── test_40_priority.py      # Priority queuing
   │
   ├── 20_attachments/          # Attachment handling (18 tests)
   │   ├── test_00_attachments.py   # Base64, HTTP attachments
   │   ├── test_10_large_files.py   # S3 large file storage
   │   └── test_20_unicode.py       # Unicode encoding
   │
   ├── 30_delivery/             # Delivery handling (9 tests)
   │   ├── test_00_smtp_errors.py   # SMTP error simulation
   │   └── test_10_delivery_reports.py  # Client callbacks
   │
   ├── 40_operations/           # Operations (21 tests)
   │   ├── test_00_metrics.py       # Prometheus metrics
   │   ├── test_10_service_control.py   # Suspend/activate
   │   ├── test_20_rate_limiting.py     # Rate limiting
   │   └── test_30_retention.py     # Data retention
   │
   ├── 50_security/             # Security (15 tests)
   │   ├── test_00_isolation.py     # Tenant isolation
   │   ├── test_10_security.py      # Input sanitization
   │   └── test_20_tenant_auth.py   # Per-tenant API keys
   │
   └── 60_imap/                 # Bounce detection (19 tests)
       ├── test_00_bounce.py        # Bounce parsing, headers
       └── test_10_bounce_live.py   # Live IMAP polling


Quick Start
-----------

Prerequisites
~~~~~~~~~~~~~

- Docker and Docker Compose
- Python 3.10+ with pytest
- At least 8GB RAM

Running Tests
~~~~~~~~~~~~~

.. code-block:: bash

   # Start infrastructure
   docker compose -f tests/docker/docker-compose.fulltest.yml up -d

   # Start mailproxy locally (in another terminal)
   GMP_DB_PATH=postgresql://mailproxy:testpassword@localhost:5433/mailproxy \
   GMP_API_TOKEN=test-api-token \
   uvicorn mail_proxy.server:app --host 0.0.0.0 --port 8000 --reload

   # Run all tests
   pytest tests/fullstack/ -v

   # Run specific group
   pytest tests/fullstack/40_operations/ -v

   # Run by marker
   pytest tests/fullstack/ -m bounce_e2e -v

   # Stop infrastructure
   docker compose -f tests/docker/docker-compose.fulltest.yml down


Infrastructure Services
-----------------------

The test infrastructure uses Docker Compose to orchestrate multiple services:

Database - PostgreSQL
~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - Image
     - ``postgres:16-alpine``
   * - Port
     - 5433 (mapped to 5432 internally)
   * - Database
     - ``mailproxy``
   * - Credentials
     - ``mailproxy`` / ``testpassword``

Primary storage for messages, accounts, tenants, and configuration.

Object Storage - MinIO
~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - Image
     - ``minio/minio``
   * - S3 API Port
     - 9000
   * - Console Port
     - 9001
   * - Credentials
     - ``minioadmin`` / ``minioadmin``
   * - Bucket
     - ``mail-attachments``

S3-compatible storage for large file attachments. Console UI at http://localhost:9001

SMTP Servers - MailHog
~~~~~~~~~~~~~~~~~~~~~~

Two MailHog instances capture emails for verification:

.. list-table::
   :widths: 25 25 25 25
   :header-rows: 1

   * - Service
     - SMTP Port
     - API Port
     - Purpose
   * - mailhog-tenant1
     - 1025
     - 8025
     - Tenant 1 emails
   * - mailhog-tenant2
     - 1026
     - 8026
     - Tenant 2 emails

Web UI: http://localhost:8025 (Tenant 1), http://localhost:8026 (Tenant 2)

Error SMTP Servers
~~~~~~~~~~~~~~~~~~

Custom servers based on ``aiosmtpd`` that simulate various SMTP behaviors:

.. list-table::
   :widths: 20 10 20 50
   :header-rows: 1

   * - Service
     - Port
     - Mode
     - Behavior
   * - smtp-reject
     - 1027
     - ``reject_all``
     - Always responds ``550 Mailbox not found``
   * - smtp-tempfail
     - 1028
     - ``temp_fail``
     - Always responds ``451 Temporary failure``
   * - smtp-timeout
     - 1029
     - ``timeout``
     - Waits 30s before responding
   * - smtp-ratelimit
     - 1030
     - ``rate_limit``
     - Accepts first 3, then ``452 Too many``
   * - smtp-random
     - 1031
     - ``random``
     - Mix: 60% OK, 20% temp, 10% perm, 10% slow

IMAP Server - Dovecot
~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - Image
     - ``dovecot/dovecot:2.4.0``
   * - Port
     - 10143 (IMAP)
   * - User
     - ``bounces@localhost``
   * - Password
     - ``bouncepass``
   * - Profile
     - ``bounce`` (not started by default)

IMAP server for bounce detection testing. Start with:

.. code-block:: bash

   docker compose -f tests/docker/docker-compose.fulltest.yml --profile bounce up -d

Client Endpoints
~~~~~~~~~~~~~~~~

Echo servers simulate client endpoints for delivery report callbacks:

.. list-table::
   :widths: 30 20 50
   :header-rows: 1

   * - Service
     - Port
     - Purpose
   * - client-tenant1
     - 8081
     - Delivery reports for Tenant 1
   * - client-tenant2
     - 8082
     - Delivery reports for Tenant 2
   * - attachment-server
     - 8083
     - HTTP attachment source


Test Markers
------------

Tests are tagged with markers for selective execution:

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Marker
     - Description
   * - ``fullstack``
     - All fullstack integration tests
   * - ``asyncio``
     - Async tests (auto-applied via conftest)
   * - ``bounce_e2e``
     - Bounce detection tests requiring Dovecot
   * - ``retention``
     - Data retention/cleanup tests
   * - ``rate_limit``
     - Account-level rate limiting tests
   * - ``docker``
     - Docker-specific integration tests
   * - ``chaos``
     - Non-deterministic tests (random SMTP errors)

Usage examples:

.. code-block:: bash

   # Run bounce tests only
   pytest tests/fullstack/ -m bounce_e2e -v

   # Exclude chaos tests (for CI)
   pytest tests/fullstack/ -m "not chaos" -v

   # Run rate limiting tests
   pytest tests/fullstack/ -m rate_limit -v


Configuration
-------------

Service URLs and constants are defined in ``tests/fullstack/helpers.py``:

.. code-block:: python

   # Mail Proxy
   MAILPROXY_URL = "http://localhost:8000"
   MAILPROXY_TOKEN = "test-api-token"

   # MailHog
   MAILHOG_TENANT1_API = "http://localhost:8025"
   MAILHOG_TENANT2_API = "http://localhost:8026"

   # Client endpoints
   CLIENT_TENANT1_URL = "http://localhost:8081"
   CLIENT_TENANT2_URL = "http://localhost:8082"

   # IMAP (Dovecot)
   DOVECOT_IMAP_HOST = "localhost"
   DOVECOT_IMAP_PORT = 10143
   DOVECOT_BOUNCE_USER = "bounces@localhost"
   DOVECOT_BOUNCE_PASS = "bouncepass"


Test Fixtures
-------------

Key fixtures available in ``tests/fullstack/conftest.py``:

``api_client``
   Async HTTP client configured with API token and base URL.

``setup_test_tenants``
   Creates two test tenants with their SMTP accounts.

``imap_bounce``
   IMAP client connected to Dovecot bounce mailbox.

``clean_imap``
   Clears IMAP mailbox before and after test.

``configure_bounce_receiver``
   Configures BounceReceiver via API for live testing.


Helper Functions
----------------

Common operations in ``tests/fullstack/helpers.py``:

``clear_mailhog(api_url)``
   Delete all messages from a MailHog instance.

``get_mailhog_messages(api_url)``
   Retrieve all captured messages from MailHog.

``wait_for_messages(api_url, count, timeout)``
   Wait for expected number of messages in MailHog.

``trigger_dispatch(api_client, tenant_id)``
   Trigger message dispatch for a tenant.

``get_msg_status(msg)``
   Derive message status (sent/error/deferred/pending) from fields.

``create_dsn_bounce_email(message_id, ...)``
   Create RFC 3464 formatted bounce email.

``inject_bounce_email_to_imap(email)``
   Inject bounce email into IMAP mailbox via APPEND.

``wait_for_bounce(api_client, msg_id, timeout)``
   Wait for BounceReceiver to detect and process bounce.


Writing New Tests
-----------------

1. Place tests in the appropriate numbered group

2. Use ``pytestmark`` to set markers:

   .. code-block:: python

      pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]

3. Use fixtures from ``conftest.py``:

   .. code-block:: python

      async def test_my_feature(self, api_client, setup_test_tenants):
          # Test implementation

4. Add cleanup at the start for tests that depend on clean state:

   .. code-block:: python

      await asyncio.sleep(2)  # Wait for pending dispatches
      await clear_mailhog(MAILHOG_TENANT1_API)

5. Use unique IDs with timestamp to avoid collisions:

   .. code-block:: python

      ts = int(time.time())
      msg_id = f"my-test-{ts}"


Troubleshooting
---------------

Services Not Starting
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Check service status
   docker compose -f tests/docker/docker-compose.fulltest.yml ps

   # Check specific service logs
   docker compose -f tests/docker/docker-compose.fulltest.yml logs mailhog-tenant1

Tests Timing Out
~~~~~~~~~~~~~~~~

- Increase ``asyncio.sleep()`` durations in tests
- Check if ``smtp-timeout`` is involved (30s delay)
- Verify all services are healthy

Rate Limit Tests Flaky
~~~~~~~~~~~~~~~~~~~~~~

Add cleanup at test start to avoid residual state from previous tests:

.. code-block:: python

   await asyncio.sleep(2)
   await clear_mailhog(MAILHOG_TENANT1_API)

MailHog Crash
~~~~~~~~~~~~~

Restart the specific container:

.. code-block:: bash

   docker compose -f tests/docker/docker-compose.fulltest.yml restart mailhog-tenant1


See Also
--------

- :doc:`fullstack_testing_reference` - Complete test catalog with all 120 tests
- ``tests/fullstack/README.md`` - Quick reference for running tests
