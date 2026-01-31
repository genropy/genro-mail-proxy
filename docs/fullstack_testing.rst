
Fullstack Integration Testing
=============================

This document describes the fullstack testing infrastructure for genro-mail-proxy.
These tests validate end-to-end functionality using Docker containers.

.. contents:: Table of Contents
   :local:
   :depth: 2

Overview
--------

The fullstack test suite validates the complete mail proxy functionality:

- End-to-end message flow from API to SMTP delivery
- S3-compatible storage integration (Minio)
- IMAP bounce injection and detection
- Delivery reports via HTTP callbacks

Test Structure
--------------

Tests mirror the source code structure:

.. code-block:: text

   tests/
   ├── core/mail_proxy/           # Core functionality tests
   │   ├── entities/              # Entity table and endpoint tests
   │   │   ├── account/
   │   │   ├── tenant/
   │   │   ├── message/
   │   │   ├── instance/
   │   │   └── ...
   │   ├── interface/             # API and CLI tests
   │   ├── smtp/                  # SMTP sender, pool, retry tests
   │   └── reporting/             # Client reporter tests
   │
   ├── enterprise/mail_proxy/     # Enterprise features tests
   │   ├── attachments/           # Large file storage tests
   │   ├── bounce/                # Bounce parser and receiver
   │   ├── pec/                   # PEC parser and receiver
   │   ├── imap/                  # IMAP client tests
   │   └── entities/              # EE entity extensions
   │
   ├── sql/                       # Database adapter tests
   │
   └── fullstack/                 # Docker-based integration tests
       ├── conftest.py            # Shared fixtures
       ├── docker-compose.yml     # Service definitions
       ├── Dockerfile.proxy       # Proxy container
       ├── imap_injector.py       # Bounce simulation
       └── test_smtp_delivery.py  # SMTP delivery tests


Quick Start
-----------

Prerequisites
~~~~~~~~~~~~~

- Docker and Docker Compose
- Python 3.10+ with pytest
- At least 4GB RAM

Running Tests
~~~~~~~~~~~~~

.. code-block:: bash

   # Start Docker services
   cd tests/fullstack
   docker compose up -d

   # Wait for services to be healthy
   docker compose ps

   # Run all tests (from project root)
   pytest tests/ -v

   # Run only fullstack tests
   pytest tests/fullstack/ -v

   # Run unit tests (no Docker required)
   pytest tests/core/ tests/enterprise/ tests/sql/ -v

   # Stop services when done
   docker compose down


Infrastructure Services
-----------------------

The test infrastructure uses Docker Compose to orchestrate services.

Mailpit (SMTP/IMAP)
~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - Image
     - ``axllent/mailpit:latest``
   * - SMTP Port
     - 1025
   * - IMAP Port
     - 1143 (for bounce injection)
   * - Web UI
     - http://localhost:8025

Mailpit provides a combined SMTP sink and IMAP server for testing.
The Web UI allows visual inspection of captured emails.

Minio (S3-Compatible Storage)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - Image
     - ``minio/minio:latest``
   * - S3 API Port
     - 9000
   * - Console Port
     - 9001
   * - Credentials
     - ``minioadmin`` / ``minioadmin``
   * - Test Bucket
     - ``test-attachments``

S3-compatible storage for large file attachment tests.
Console UI available at http://localhost:9001.

Mail Proxy
~~~~~~~~~~

.. list-table::
   :widths: 30 70
   :header-rows: 0

   * - Build
     - ``tests/fullstack/Dockerfile.proxy``
   * - API Port
     - 8000
   * - Health Check
     - http://localhost:8000/instance/health

The proxy container connects to Mailpit for SMTP delivery and
Minio for S3 storage.


Test Fixtures
-------------

Key fixtures available in ``tests/fullstack/conftest.py``:

``imap_injector``
   IMAP client for injecting bounce messages into Mailpit.

``mailpit_api``
   HTTP client for the Mailpit REST API (message retrieval, deletion).

``minio_config``
   S3 configuration dictionary for storage tests.

``minio_available``
   Boolean indicating if Minio is accessible.


Bounce Injection
----------------

The ``IMAPBounceInjector`` class simulates bounce messages by injecting
RFC 3464 DSN (Delivery Status Notification) messages directly into the
IMAP mailbox.

Example usage in tests:

.. code-block:: python

   async def test_bounce_detection(imap_injector, mailpit_api):
       # Inject a bounce message
       bounce_msg = create_dsn_bounce(
           original_message_id="<msg-123@example.com>",
           bounce_type="hard",
           diagnostic="550 User unknown"
       )
       await imap_injector.inject(bounce_msg)

       # Verify bounce was processed
       # ...


CSV-Driven Tests
----------------

Tests can load message scenarios from CSV files in ``tests/fullstack/fixtures/``.

CSV columns:

- ``id``: Message identifier
- ``from``: Sender address
- ``to``: Recipient address
- ``subject``: Email subject
- ``body``: Email body
- ``expected_status``: Expected final status (``sent``, ``bounced``, ``error``)
- ``simulate_bounce``: Bounce type to inject (``hard``, ``soft``, or empty)


Environment Variables
---------------------

The proxy container accepts these environment variables:

.. list-table::
   :widths: 35 25 40
   :header-rows: 1

   * - Variable
     - Default
     - Description
   * - ``MAIL_PROXY_DB_PATH``
     - ``/data/mail.db``
     - SQLite database path
   * - ``MAIL_PROXY_SMTP_HOST``
     - ``mailpit``
     - SMTP server hostname
   * - ``MAIL_PROXY_SMTP_PORT``
     - ``1025``
     - SMTP server port
   * - ``MAIL_PROXY_IMAP_HOST``
     - ``mailpit``
     - IMAP server for bounce polling
   * - ``MAIL_PROXY_IMAP_PORT``
     - ``1143``
     - IMAP server port
   * - ``MAIL_PROXY_STORAGE_URL``
     - ``s3://test-attachments``
     - S3 storage URL
   * - ``AWS_ENDPOINT_URL``
     - ``http://minio:9000``
     - S3 endpoint (Minio)
   * - ``AWS_ACCESS_KEY_ID``
     - ``minioadmin``
     - S3 access key
   * - ``AWS_SECRET_ACCESS_KEY``
     - ``minioadmin``
     - S3 secret key


Test Markers
------------

Tests use pytest markers for selective execution:

.. list-table::
   :widths: 20 80
   :header-rows: 1

   * - Marker
     - Description
   * - ``fullstack``
     - Docker-based integration tests
   * - ``asyncio``
     - Async tests (auto-applied via conftest)
   * - ``network``
     - Tests requiring network access
   * - ``db``
     - Database integration tests

Usage examples:

.. code-block:: bash

   # Run fullstack tests only
   pytest tests/fullstack/ -m fullstack -v

   # Exclude network tests
   pytest tests/ -m "not network" -v


Troubleshooting
---------------

Services Not Starting
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Check service status
   docker compose ps

   # Check specific service logs
   docker compose logs mailpit
   docker compose logs proxy
   docker compose logs minio

Tests Skipped
~~~~~~~~~~~~~

If tests are skipped with "Docker services not available":

.. code-block:: bash

   # Ensure services are running
   docker compose up -d

   # Verify health checks pass
   docker compose ps

Minio Not Accessible
~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Check minio-setup completed
   docker compose logs minio-setup
   # Should show bucket creation message

Rebuild After Code Changes
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   # Rebuild proxy container
   docker compose build proxy
   docker compose up -d


Writing New Tests
-----------------

1. Place tests in the appropriate directory matching source structure

2. Use ``pytest.mark.asyncio`` for async tests:

   .. code-block:: python

      import pytest

      @pytest.mark.asyncio
      async def test_my_feature():
          # Test implementation

3. Use fixtures from ``conftest.py``:

   .. code-block:: python

      async def test_smtp_delivery(mailpit_api):
          # Clear previous messages
          await mailpit_api.delete_all()

          # ... send message ...

          # Verify delivery
          messages = await mailpit_api.get_messages()
          assert len(messages) == 1


See Also
--------

- ``tests/fullstack/README.md`` - Quick reference for running tests
- :doc:`contributing` - Development guidelines
