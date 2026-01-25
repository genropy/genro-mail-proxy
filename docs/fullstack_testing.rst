
Appendix A: Fullstack Testing Infrastructure
============================================

This appendix describes the comprehensive testing infrastructure for genro-mail-proxy,
designed to validate every aspect of the system in an environment that closely
simulates production.

.. note::

   **Modular Test Structure (Recommended)**

   The fullstack tests have been reorganized into a modular package at ``tests/fullstack/``.
   This replaces the monolithic ``test_fullstack_integration.py`` file.

   .. code-block:: bash

      # Run all fullstack tests
      pytest tests/fullstack/ -v -m fullstack

      # Run specific test categories
      pytest tests/fullstack/ -m bounce_e2e -v      # Bounce detection tests
      pytest tests/fullstack/ -m retention -v       # Message retention tests
      pytest tests/fullstack/ -m rate_limit -v      # Rate limiting tests

   **Package structure:**

   .. code-block:: text

      tests/fullstack/
      ├── __init__.py
      ├── README.md              # Quick start guide
      ├── conftest.py            # Shared fixtures (api_client, imap_bounce, etc.)
      ├── helpers.py             # Helper functions and constants
      │
      ├── test_health.py         # Health and basic API tests
      ├── test_tenants.py        # Tenant management
      ├── test_accounts.py       # Account management
      ├── test_dispatch.py       # Basic message dispatch
      ├── test_isolation.py      # Tenant isolation
      ├── test_batch.py          # Batch operations
      ├── test_attachments.py    # Base64 and HTTP attachments
      ├── test_priority.py       # Priority handling
      ├── test_service_control.py # Suspend/Activate
      ├── test_metrics.py        # Prometheus metrics
      ├── test_validation.py     # Input validation
      ├── test_messages.py       # Message management
      ├── test_infrastructure.py # Docker service checks
      ├── test_smtp_errors.py    # SMTP error handling
      ├── test_large_files.py    # S3 large file storage
      ├── test_delivery_reports.py # Delivery report callbacks
      ├── test_security.py       # Security/sanitization
      ├── test_unicode.py        # Unicode/encoding
      ├── test_bounce.py         # Bounce detection (DSN parsing)
      │
      ├── test_bounce_live.py    # Live bounce polling (BounceReceiver)
      ├── test_retention.py      # Message retention cleanup
      ├── test_rate_limiting.py  # Account rate limiting
      └── test_tenant_auth.py    # Per-tenant API keys

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

Infrastructure Overview
~~~~~~~~~~~~~~~~~~~~~~~

.. mermaid::

   graph TB
       subgraph Client["Client Layer"]
           pytest["pytest/httpx<br/>API Client"]
       end

       subgraph App["Application Layer"]
           proxy["Mail Proxy<br/>:8000"]
       end

       subgraph Data["Data Layer"]
           pg["PostgreSQL<br/>:5432"]
           minio["MinIO S3<br/>:9000"]
       end

       subgraph SMTP["SMTP Layer"]
           mh1["MailHog T1<br/>:1025"]
           mh2["MailHog T2<br/>:1026"]
           reject["smtp-reject<br/>:1027"]
           tempfail["smtp-tempfail<br/>:1028"]
           timeout["smtp-timeout<br/>:1029"]
           ratelimit["smtp-ratelimit<br/>:1030"]
           random["smtp-random<br/>:1031"]
       end

       subgraph Clients["Client Endpoints"]
           echo1["Echo T1<br/>:8081"]
           echo2["Echo T2<br/>:8082"]
           attach["Attachment Server<br/>:8083"]
       end

       subgraph IMAP["IMAP Layer (Bounce)"]
           dovecot["Dovecot IMAP<br/>:10143"]
       end

       pytest -->|REST API| proxy
       proxy --> pg
       proxy --> minio
       proxy -->|SMTP| mh1
       proxy -->|SMTP| mh2
       proxy -->|SMTP errors| reject
       proxy -->|SMTP errors| tempfail
       proxy -->|SMTP errors| timeout
       proxy -->|SMTP errors| ratelimit
       proxy -->|SMTP errors| random
       proxy -->|Delivery Reports| echo1
       proxy -->|Delivery Reports| echo2
       proxy -->|Fetch Attachments| attach
       proxy -->|Poll Bounces| dovecot
       pytest -->|Inject Bounces| dovecot

Test Flow
~~~~~~~~~

.. mermaid::

   sequenceDiagram
       participant T as pytest
       participant P as Mail Proxy
       participant DB as PostgreSQL
       participant S as SMTP (MailHog)
       participant C as Client Echo

       T->>P: POST /commands/add-messages
       P->>DB: INSERT messages
       P-->>T: 200 OK (queued)

       T->>P: POST /commands/run-now
       P->>DB: SELECT ready messages
       P->>S: SMTP SEND
       S-->>P: 250 OK
       P->>DB: UPDATE sent_ts

       P->>C: POST /proxy_sync (delivery report)
       C-->>P: 200 OK
       P->>DB: UPDATE reported_ts

       T->>P: GET /messages
       P->>DB: SELECT messages
       P-->>T: messages with sent_ts

       T->>S: GET /api/v2/messages
       S-->>T: captured emails

Test Coverage Overview
~~~~~~~~~~~~~~~~~~~~~~

.. mermaid::

   pie title Fullstack Test Coverage (91 tests)
       "Core Features" : 35
       "Error Handling" : 12
       "Security" : 9
       "Attachments" : 11
       "Multi-tenancy" : 6
       "Bounce/Batch" : 17
       "Bounce E2E (NEW)" : 10

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

IMAP Server - Dovecot
~~~~~~~~~~~~~~~~~~~~~

============= ========================
Parameter     Value
============= ========================
Image         ``dovecot/dovecot:latest``
Ports         10143 (IMAP), 10993 (IMAPS)
Bounce User   ``bounces@localhost``
Password      ``bouncepass``
Volume        ``dovecot-mail:/var/mail``
============= ========================

**Purpose**: IMAP server for bounce detection testing. BounceReceiver polls this mailbox
for bounce emails (DSN/MDN format) and correlates them with sent messages via X-Genro-Mail-ID header.

**Why Dovecot over GreenMail?**

We evaluated two options for IMAP testing:

1. **Dovecot** - Production-grade IMAP/POP3 server (chosen)
2. **GreenMail** - Java-based test email server with REST API

We chose Dovecot for the following reasons:

- **Production standard**: Dovecot is the same software used in production environments.
  Testing against the real thing gives us confidence that bounce detection will work
  with actual mail servers.

- **No Java dependency**: GreenMail requires a JVM, adding ~200MB+ to container size
  and startup time. Dovecot is a lightweight native binary.

- **IMAP APPEND works perfectly**: For testing, we inject bounce emails directly into
  the mailbox using standard IMAP APPEND command. This is simple and requires no
  special test APIs.

- **Realistic testing**: By using production-grade software, we catch edge cases and
  compatibility issues that a test-only server might not expose.

GreenMail's REST API for programmatic email injection is convenient, but the benefits
of testing against a real IMAP server outweigh the minor convenience gain.

**Test Usage**:

- Tests inject bounce emails directly into the mailbox using IMAP APPEND
- BounceParser processes the DSN format to extract bounce type, code, and original message ID
- Messages are correlated using the X-Genro-Mail-ID header

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

The test suite is organized into 25 test classes covering 91 tests total:

============================== ======= ===============================================
Class                          # Tests Description
============================== ======= ===============================================
TestHealthAndBasics            4       Health endpoint, API authentication
TestTenantManagement           4       CRUD tenant via API
TestAccountManagement          2       SMTP account management
TestBasicMessageDispatch       4       Basic email sending (text, HTML, CC/BCC, headers)
TestTenantIsolation            2       Message isolation between tenants
TestBatchOperations            2       Batch enqueue, deduplication
TestAttachmentsBase64          1       Base64 inline attachments
TestPriorityHandling           1       Priority ordering
TestServiceControl             1       Suspend/Activate validation
TestMetrics                    1       Prometheus endpoint
TestValidation                 2       Payload validation
TestMessageManagement          2       List/Delete messages
TestInfrastructureCheck        5       Docker service verification
TestSmtpErrorHandling          4       SMTP errors (reject, tempfail, ratelimit, random)
TestRetryLogic                 2       Retry count, error details
TestLargeFileStorage           6       S3 upload, link rewrite, reject, warn, mixed
TestTenantLargeFileConfigApi   3       CRUD large_file_config via API
TestDeliveryReports            3       Delivery report callbacks to client endpoints
TestSecurityInputSanitization  5       SQL injection, XSS, path traversal protection
TestUnicodeEncoding            4       Emoji, international characters, Unicode filenames
TestHttpAttachmentFetch        4       HTTP URL attachment fetching
TestBounceDetection            5       X-Genro-Mail-ID header, bounce fields in API
TestBatchCodeOperations        5       batch_code field, suspend/activate by batch
TestExtendedSuspendActivate    7       Suspend/activate counts, idempotency, isolation
**TestBounceEndToEnd**         10      Full bounce detection with IMAP/DSN
============================== ======= ===============================================

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

Delivery Reports
~~~~~~~~~~~~~~~~

.. code-block:: text

   test_delivery_report_sent_on_success      - Report sent after successful delivery
   test_delivery_report_sent_on_error        - Report includes failed messages
   test_mixed_delivery_report                - Report with both success and failure

Security & Input Sanitization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_sql_injection_in_tenant_id           - SQL injection attempts handled safely
   test_sql_injection_in_message_id          - SQL injection in message IDs handled
   test_xss_in_message_subject               - XSS attempts stored literally
   test_path_traversal_in_attachment_path    - Path traversal handled safely
   test_oversized_payload_rejection          - Large payloads don't crash server

Unicode & Encoding
~~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_emoji_in_subject                     - Emoji in subject line preserved
   test_emoji_in_body                        - Emoji in body preserved
   test_international_characters             - CJK, Arabic, Cyrillic, etc. preserved
   test_unicode_in_attachment_filename       - Unicode filenames handled

HTTP Attachment Fetch
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_fetch_attachment_from_http_url       - Single HTTP URL fetch
   test_fetch_multiple_http_attachments      - Multiple HTTP URL fetches
   test_http_attachment_timeout              - Timeout handled gracefully
   test_http_attachment_invalid_url          - Invalid URLs handled gracefully

Bounce Detection
~~~~~~~~~~~~~~~~

.. code-block:: text

   test_x_genro_mail_id_header_added         - X-Genro-Mail-ID header in outgoing emails
   test_bounce_fields_in_message_list        - Bounce fields present in /messages response
   test_message_includes_bounce_tracking_fields - MessageRecord has bounce fields
   test_multiple_messages_unique_mail_ids    - Each message gets unique Mail-ID
   test_bounce_header_with_custom_headers    - X-Genro-Mail-ID coexists with custom headers

Batch Code Operations
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_send_messages_with_batch_code        - Messages with batch_code are stored correctly
   test_suspend_specific_batch_code          - Suspend only specific batch_code
   test_activate_specific_batch_code         - Activate specific batch_code
   test_suspend_batch_does_not_affect_others - Suspended batch doesn't affect other batches
   test_suspended_batch_messages_not_sent    - Suspended batch messages remain pending

Extended Suspend/Activate
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_suspend_returns_pending_count        - Suspend returns count of pending messages
   test_activate_returns_activated_count     - Activate returns count of activated messages
   test_suspend_idempotent                   - Multiple suspends are idempotent
   test_activate_idempotent                  - Multiple activates are idempotent
   test_tenant_isolation_in_suspend          - Suspend doesn't affect other tenants
   test_suspend_with_deferred_messages       - Deferred messages handled in suspend
   test_activate_resumes_deferred_timing     - Activate preserves deferred timing

Bounce End-to-End
~~~~~~~~~~~~~~~~~

.. code-block:: text

   test_imap_server_accessible               - Verify Dovecot IMAP server is accessible
   test_bounce_email_injection               - Can inject bounce email into IMAP mailbox
   test_dsn_bounce_format_valid              - Generated DSN bounces are properly formatted
   test_soft_bounce_email_format             - Soft bounce (4xx) format is correct
   test_bounce_parser_extracts_original_id   - BounceParser extracts X-Genro-Mail-ID
   test_bounce_parser_soft_vs_hard           - BounceParser classifies hard/soft bounces
   test_message_sent_includes_tracking_header - Sent messages include X-Genro-Mail-ID
   test_bounce_updates_message_record        - Bounce detection updates message in DB
   test_multiple_bounces_correlation         - Multiple bounces correlated to correct messages

**Infrastructure**:

- Dovecot IMAP server (port 10143) for bounce mailbox
- DSN (RFC 3464) formatted bounce emails
- IMAP APPEND for injecting test bounces

**Test Flow**:

.. mermaid::

   sequenceDiagram
       participant T as pytest
       participant P as Mail Proxy
       participant S as MailHog SMTP
       participant I as Dovecot IMAP
       participant BR as BounceReceiver

       T->>P: POST /commands/add-messages
       P->>S: SMTP SEND (X-Genro-Mail-ID: msg-123)
       T->>I: IMAP APPEND (DSN bounce with X-Genro-Mail-ID)
       BR->>I: IMAP FETCH (polling)
       BR->>BR: BounceParser.parse()
       BR->>P: mark_bounced(msg-123, hard, 550)
       T->>P: GET /messages
       P-->>T: message with bounce_type, bounce_code


Test Coverage Gaps
------------------

.. note::

   **Recently Added Tests**

   The following test coverage gaps have been addressed in the modular test structure:

   - ✅ **Bounce Live Polling** - ``test_bounce_live.py`` (5 tests)
   - ✅ **Message Retention** - ``test_retention.py`` (5 tests)
   - ✅ **Account Rate Limiting** - ``test_rate_limiting.py`` (5 tests)
   - ✅ **Per-Tenant API Keys** - ``test_tenant_auth.py`` (8 tests)

The following features still require additional testing:

Bounce Detection - Live BounceReceiver (NOW TESTED)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. versionadded:: 2025.01

   Live bounce polling is now tested in ``test_bounce_live.py``.

**What IS tested:**

- ✅ DSN (RFC 3464) bounce email format generation
- ✅ BounceParser extraction of X-Genro-Mail-ID
- ✅ Hard vs soft bounce classification
- ✅ IMAP injection and retrieval
- ✅ X-Genro-Mail-ID header in outgoing emails
- ✅ **Live BounceReceiver automatic polling** (NEW)
- ✅ **Multiple bounces processed in batch** (NEW)
- ✅ **IMAP message deletion after processing** (NEW)

**Configuration required:**

The mailproxy service must have bounce detection enabled via environment variables:

.. code-block:: yaml

   environment:
     - GMP_BOUNCE_ENABLED=1
     - GMP_BOUNCE_IMAP_HOST=dovecot
     - GMP_BOUNCE_IMAP_PORT=143
     - GMP_BOUNCE_IMAP_USER=bounces@localhost
     - GMP_BOUNCE_IMAP_PASSWORD=bouncepass
     - GMP_BOUNCE_POLL_INTERVAL=2

PEC Support (NOT TESTED)
~~~~~~~~~~~~~~~~~~~~~~~~

Italian Certified Email (PEC) is **not tested**:

.. warning::

   **Missing PEC tests:**

   - PEC ricevuta di accettazione (RdA) parsing
   - PEC ricevuta di consegna (RdC) parsing
   - PEC error notifications
   - S/MIME envelope parsing
   - pec_rda_ts, pec_rdc_ts, pec_error fields update

**Implementation approach:**

- Use same Dovecot infrastructure
- Inject fake PEC emails (multipart/signed + report)
- Start with unit tests for PEC parser, then integrate in fullstack
- Use ``email``, ``dkim``, ``cryptography`` libraries for S/MIME envelope generation

Message Lifecycle - Retention (NOW TESTED)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. versionadded:: 2025.01

   Message retention is now tested in ``test_retention.py``.

**What IS tested:**

- ✅ Cleanup removes old reported messages
- ✅ Cleanup respects tenant isolation
- ✅ Unreported messages are preserved
- ✅ Bounced but unreported messages are preserved
- ✅ Retention configuration per tenant

Rate Limiting (NOW TESTED)
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. versionadded:: 2025.01

   Account-level rate limiting is now tested in ``test_rate_limiting.py``.

**What IS tested:**

- ✅ Per-minute rate limit defers excess messages
- ✅ Per-hour rate limit configuration
- ✅ Reject vs defer behavior
- ✅ Rate limit window reset
- ✅ Independent rate limits per account

Per-Tenant API Keys (NOW TESTED)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. versionadded:: 2025.01

   Per-tenant API authentication is now tested in ``test_tenant_auth.py``.

**What IS tested:**

- ✅ Tenant token accesses own resources
- ✅ Tenant token rejected for other tenants
- ✅ Global token fallback
- ✅ Invalid/missing token rejection
- ✅ Token rotation
- ✅ Scoped operations per tenant

Remaining Coverage Gaps
~~~~~~~~~~~~~~~~~~~~~~~

The following features are **still NOT TESTED**:

PEC Support (Italian Certified Email)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Italian Certified Email (PEC) is **not tested**:

.. warning::

   **Missing PEC tests:**

   - PEC ricevuta di accettazione (RdA) parsing
   - PEC ricevuta di consegna (RdC) parsing
   - PEC error notifications
   - S/MIME envelope parsing
   - pec_rda_ts, pec_rdc_ts, pec_error fields update

**Implementation approach:**

- Use same Dovecot infrastructure
- Inject fake PEC emails (multipart/signed + report)
- Start with unit tests for PEC parser, then integrate in fullstack
- Use ``email``, ``dkim``, ``cryptography`` libraries for S/MIME envelope generation


Test Roadmap - Priority Summary
-------------------------------

The following table summarizes the **remaining** test gaps and their priority:

==================== ========== ===============================================
Feature              Priority   Status
==================== ========== ===============================================
Live bounce polling  HIGH       ✅ COMPLETED (test_bounce_live.py)
Bounce delivery rpt  HIGH       ✅ COMPLETED (test_bounce_live.py)
Retention cleanup    MEDIUM     ✅ COMPLETED (test_retention.py)
Rate limit account   MEDIUM     ✅ COMPLETED (test_rate_limiting.py)
Per-tenant API keys  MEDIUM     ✅ COMPLETED (test_tenant_auth.py)
PEC support          LOW        ❌ NOT TESTED (Italian market specific)
==================== ========== ===============================================

**Recommended next steps:**

1. Run the new tests to verify infrastructure works correctly
2. Implement PEC support tests if needed for Italian market
3. Add chaos/fuzz testing for resilience


DX Improvements - Implemented Enhancements
------------------------------------------

The following DX improvements have been implemented:

Improved Dovecot Healthcheck (IMPLEMENTED)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The healthcheck now verifies actual IMAP login works:

.. code-block:: yaml

   healthcheck:
     test: ["CMD", "sh", "-c", "echo -e 'A1 LOGIN bounces@localhost bouncepass\nA2 LOGOUT' | nc localhost 143 | grep -q 'A1 OK'"]
     interval: 5s
     timeout: 5s
     retries: 5
     start_period: 15s

IMAP Client Fixtures (IMPLEMENTED)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Fixtures are now available in ``tests/fullstack/conftest.py``:

.. code-block:: python

   @pytest.fixture
   def imap_bounce():
       """IMAP client connected to bounce mailbox."""
       M = imaplib.IMAP4(DOVECOT_IMAP_HOST, DOVECOT_IMAP_PORT)
       M.login(DOVECOT_BOUNCE_USER, DOVECOT_BOUNCE_PASS)
       M.select("INBOX")
       yield M
       M.logout()

   @pytest.fixture
   def clean_imap(imap_bounce):
       """Clear IMAP mailbox before and after test."""
       clear_imap_mailbox(imap_bounce)
       yield imap_bounce
       clear_imap_mailbox(imap_bounce)

Test Markers (IMPLEMENTED)
~~~~~~~~~~~~~~~~~~~~~~~~~~

Markers are now configured in ``pyproject.toml``:

.. code-block:: toml

   [tool.pytest.ini_options]
   markers = [
       "fullstack: marks tests as fullstack integration tests",
       "bounce_e2e: marks tests as bounce end-to-end tests requiring Dovecot IMAP",
       "chaos: marks tests with non-deterministic/random behavior",
       "retention: marks tests for message retention and cleanup features",
       "rate_limit: marks tests for account-level rate limiting",
   ]

**Usage:**

.. code-block:: bash

   # Run only bounce E2E tests
   pytest tests/fullstack/ -m bounce_e2e -v

   # Run retention tests
   pytest tests/fullstack/ -m retention -v

   # Exclude chaos tests (non-deterministic)
   pytest tests/fullstack/ -m "not chaos" -v

   @pytest.mark.chaos
   async def test_random_smtp_behavior(...):
       ...

Run with:

.. code-block:: bash

   # Run bounce tests only
   pytest -m bounce_e2e -v

   # Run all except chaos tests (recommended for CI)
   pytest -m "not chaos" -v

   # Run chaos tests separately
   pytest -m chaos -v


Test Profiles
~~~~~~~~~~~~~

Organize tests into profiles for different use cases:

=================== ============================================== ==============
Profile             Description                                    Markers
=================== ============================================== ==============
**smoke**           Quick happy-path validation                    ``-m smoke``
**fullstack**       Complete test suite (default)                  no marker
**bounce_e2e**      Bounce detection only                          ``-m bounce_e2e``
**chaos**           Random failures, timeouts, edge cases          ``-m chaos``
=================== ============================================== ==============

**Recommended CI configuration:**

.. code-block:: yaml

   # Fast feedback (< 2 min)
   - pytest -m smoke -v

   # Full validation (exclude chaos)
   - pytest -m "not chaos" -v

   # Nightly/weekly chaos testing
   - pytest -m chaos -v --timeout=120


Deterministic Chaos Testing
~~~~~~~~~~~~~~~~~~~~~~~~~~~

The ``smtp-random`` server introduces non-determinism. To make failures reproducible:

1. **Add seed support** to error-smtp server:

   .. code-block:: python

      # In server.py
      import os
      import random

      seed = int(os.environ.get("SMTP_RANDOM_SEED", 0))
      if seed:
          random.seed(seed)

2. **Log the seed** when tests fail for reproduction:

   .. code-block:: python

      @pytest.fixture(scope="session")
      def chaos_seed():
          seed = int(os.environ.get("SMTP_RANDOM_SEED", time.time()))
          print(f"Chaos seed: {seed}")
          return seed

3. **Reproduce failures** by setting the seed:

   .. code-block:: bash

      SMTP_RANDOM_SEED=12345 pytest -m chaos -v


On-Failure Diagnostics
~~~~~~~~~~~~~~~~~~~~~~

Automatic diagnostics when tests fail dramatically reduce debugging time:

.. code-block:: python

   import pytest

   @pytest.hookimpl(tryfirst=True, hookwrapper=True)
   def pytest_runtest_makereport(item, call):
       outcome = yield
       report = outcome.get_result()

       if report.when == "call" and report.failed:
           # Collect diagnostics
           print("\n=== FAILURE DIAGNOSTICS ===")

           # Docker service status
           import subprocess
           result = subprocess.run(
               ["docker", "compose", "-f", "tests/docker/docker-compose.fulltest.yml", "ps"],
               capture_output=True, text=True
           )
           print(f"Docker status:\n{result.stdout}")

           # Mail proxy logs (last 20 lines)
           result = subprocess.run(
               ["docker", "compose", "-f", "tests/docker/docker-compose.fulltest.yml",
                "logs", "mailproxy", "--tail", "20"],
               capture_output=True, text=True
           )
           print(f"Mail proxy logs:\n{result.stdout}")

           # MailHog message count
           import httpx
           try:
               resp = httpx.get("http://localhost:8025/api/v2/messages", timeout=5)
               count = len(resp.json().get("items", []))
               print(f"MailHog T1 messages: {count}")
           except Exception as e:
               print(f"MailHog check failed: {e}")


Isolation and Cleanup
~~~~~~~~~~~~~~~~~~~~~

Ensure tests don't interfere with each other:

**Database isolation:**

.. code-block:: python

   @pytest.fixture(scope="function")
   async def clean_db(api_client):
       """Clean test data before each test."""
       # Delete test messages
       await api_client.delete("/messages/cleanup-all-test")
       yield
       # Cleanup after test
       await api_client.delete("/messages/cleanup-all-test")

**MinIO isolation:**

.. code-block:: python

   @pytest.fixture(scope="session")
   def test_run_id():
       """Unique prefix for this test run."""
       return f"test-{int(time.time())}"

   # Use prefix in attachments: attachments/{test_run_id}/...

**MailHog cleanup:**

.. code-block:: python

   @pytest.fixture(scope="class", autouse=True)
   async def clean_mailhog():
       """Clear MailHog before each test class."""
       async with httpx.AsyncClient() as client:
           await client.delete("http://localhost:8025/api/v1/messages")
           await client.delete("http://localhost:8026/api/v1/messages")
       yield


Enhanced Healthchecks
~~~~~~~~~~~~~~~~~~~~~

Current healthchecks verify "container up". Enhanced checks verify "service ready":

**Mail Proxy - verify full readiness:**

.. code-block:: yaml

   healthcheck:
     test: ["CMD", "curl", "-f", "http://localhost:8000/health?check=db"]
     interval: 5s
     timeout: 5s
     retries: 10

**MinIO - verify bucket exists:**

.. code-block:: yaml

   # In minio-setup, add verification
   entrypoint: >
     /bin/sh -c "
       mc alias set local http://minio:9000 minioadmin minioadmin;
       mc mb local/mail-attachments --ignore-existing;
       mc ls local/mail-attachments;
       echo 'MinIO setup verified';
     "

**Dovecot - verify IMAP login works:**

.. code-block:: yaml

   healthcheck:
     test: ["CMD", "sh", "-c", "echo -e 'A1 LOGIN bounces@localhost bouncepass\nA2 LOGOUT' | nc localhost 143 | grep 'A1 OK'"]
     interval: 5s
     timeout: 5s
     retries: 5


Timeout Configuration
~~~~~~~~~~~~~~~~~~~~~

Use shorter timeouts in tests, longer in chaos:

.. code-block:: yaml

   # Standard tests: smtp-timeout with 5s delay
   smtp-timeout:
     environment:
       - SMTP_TIMEOUT_SECONDS=${SMTP_TIMEOUT:-5}

   # Chaos profile: 30s delay
   # SMTP_TIMEOUT=30 docker compose up ...

**Bounce polling interval:**

.. code-block:: yaml

   # Fast polling for tests (1 second)
   mailproxy:
     environment:
       - BOUNCE_POLL_INTERVAL=${BOUNCE_POLL_INTERVAL:-1}


Additional Test Assertions
~~~~~~~~~~~~~~~~~~~~~~~~~~

Beyond functional tests, add system invariant checks:

**Idempotency:**

.. code-block:: python

   async def test_run_now_idempotent(api_client, setup_test_tenants):
       """Multiple run-now calls don't duplicate sends."""
       # Send one message
       msg_id = f"idem-{time.time()}"
       await api_client.post("/messages/add", json={"messages": [...]})

       # Call run-now multiple times
       await api_client.post("/commands/run-now")
       await api_client.post("/commands/run-now")
       await api_client.post("/commands/run-now")

       # Should have exactly 1 email in MailHog
       messages = await get_mailhog_messages()
       matching = [m for m in messages if msg_id in str(m)]
       assert len(matching) == 1, "Duplicate sends detected!"

**No cross-tenant data:**

.. code-block:: python

   async def test_tenant_isolation_invariant(api_client, setup_test_tenants):
       """Verify no data leaks between tenants."""
       # Get all messages for tenant 1
       resp1 = await api_client.get("/messages", params={"tenant_id": "test-tenant-1"})

       # Get all messages for tenant 2
       resp2 = await api_client.get("/messages", params={"tenant_id": "test-tenant-2"})

       # Verify no overlap in message IDs
       ids1 = {m["id"] for m in resp1.json()["messages"]}
       ids2 = {m["id"] for m in resp2.json()["messages"]}
       assert ids1.isdisjoint(ids2), "Cross-tenant data detected!"

**Attachment integrity:**

.. code-block:: python

   async def test_attachment_integrity(api_client, minio_client):
       """Verify uploaded attachments match original."""
       original_content = b"test content 12345"
       original_hash = hashlib.sha256(original_content).hexdigest()

       # Send message with attachment
       # ... (upload happens)

       # Verify in MinIO
       obj = minio_client.get_object("mail-attachments", "path/to/file")
       stored_hash = hashlib.sha256(obj.read()).hexdigest()

       assert stored_hash == original_hash, "Attachment corruption detected!"


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
