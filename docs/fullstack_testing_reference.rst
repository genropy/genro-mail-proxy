
Fullstack Test Reference
========================

This document provides a complete catalog of all 120 fullstack integration tests,
organized by group and test class.

.. contents:: Table of Contents
   :local:
   :depth: 3


00_core - Core Functionality
----------------------------

22 tests validating basic API functionality, authentication, and infrastructure.

TestHealthAndBasics
~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/00_core/test_00_health.py``

Health endpoint and API authentication tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_health_endpoint_no_auth``
     - GET /health works without authentication
   * - ``test_status_endpoint_requires_auth``
     - GET /status requires API token
   * - ``test_status_endpoint_with_auth``
     - GET /status returns status with valid token
   * - ``test_invalid_token_rejected``
     - Invalid API token returns 403

Docker Integration Tests
~~~~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/00_core/test_05_docker_integration.py``

Tests verifying Docker infrastructure and basic email flow.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_docker_services_available``
     - All Docker services are running and healthy
   * - ``test_send_email_to_tenant1_mailhog``
     - Email sent via API arrives at Tenant 1 MailHog
   * - ``test_send_email_to_tenant2_mailhog``
     - Email sent via API arrives at Tenant 2 MailHog
   * - ``test_tenant_isolation_smtp``
     - Tenant 1 emails don't appear in Tenant 2 MailHog
   * - ``test_batch_emails_same_tenant``
     - Multiple emails to same tenant all arrive
   * - ``test_html_email_via_docker``
     - HTML email content preserved through system
   * - ``test_email_with_custom_headers``
     - Custom headers preserved in sent email

TestInfrastructureCheck
~~~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/00_core/test_10_infrastructure.py``

Infrastructure connectivity verification.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_postgresql_connection``
     - PostgreSQL database is accessible
   * - ``test_mailhog_tenant1_accessible``
     - MailHog Tenant 1 API responds
   * - ``test_mailhog_tenant2_accessible``
     - MailHog Tenant 2 API responds
   * - ``test_minio_accessible``
     - MinIO S3 API responds
   * - ``test_echo_servers_accessible``
     - Client echo servers respond

TestTenantManagement
~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/00_core/test_20_tenants.py``

Tenant CRUD operations.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_create_tenant``
     - POST /tenant creates new tenant
   * - ``test_list_tenants``
     - GET /tenants returns tenant list
   * - ``test_get_tenant_details``
     - GET /tenant?id=X returns tenant details
   * - ``test_update_tenant``
     - PUT /tenant updates tenant configuration

TestAccountManagement
~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/00_core/test_30_accounts.py``

SMTP account management.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_list_accounts``
     - GET /accounts returns account list
   * - ``test_create_account_with_rate_limits``
     - POST /account with rate limit fields


10_messaging - Message Handling
-------------------------------

16 tests covering message validation, dispatch, and batch operations.

TestValidation
~~~~~~~~~~~~~~

**File:** ``tests/fullstack/10_messaging/test_00_validation.py``

Input validation tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_invalid_message_rejected``
     - Message without required fields rejected
   * - ``test_invalid_account_rejected``
     - Message with non-existent account rejected

TestBasicMessageDispatch
~~~~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/10_messaging/test_10_dispatch.py``

Basic email dispatch tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_send_simple_text_email``
     - Plain text email sent and received
   * - ``test_send_html_email``
     - HTML email content preserved
   * - ``test_send_email_with_cc_bcc``
     - CC and BCC recipients receive email
   * - ``test_send_email_with_custom_headers``
     - Custom headers included in sent email

TestMessageManagement
~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/10_messaging/test_20_messages.py``

Message API operations.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_list_messages``
     - GET /messages returns message list
   * - ``test_delete_messages``
     - DELETE /messages removes messages

TestBatchOperations
~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/10_messaging/test_30_batch.py``

Batch message operations.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_batch_enqueue``
     - Multiple messages enqueued in single request
   * - ``test_already_sent_rejected``
     - Duplicate message IDs rejected

TestBatchCodeOperations
~~~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/10_messaging/test_30_batch.py``

Batch code grouping and control.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_send_messages_with_batch_code``
     - Messages with batch_code stored correctly
   * - ``test_suspend_specific_batch_code``
     - Suspend only affects specific batch_code
   * - ``test_activate_specific_batch_code``
     - Activate resumes specific batch_code
   * - ``test_suspend_batch_does_not_affect_others``
     - Other batches unaffected by suspend
   * - ``test_suspended_batch_messages_not_sent``
     - Suspended batch messages remain pending

TestPriorityHandling
~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/10_messaging/test_40_priority.py``

Priority queue tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_priority_ordering``
     - Higher priority messages sent first


20_attachments - Attachment Handling
------------------------------------

18 tests covering base64, HTTP, S3 attachments, and unicode encoding.

TestAttachmentsBase64
~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/20_attachments/test_00_attachments.py``

Base64 inline attachment tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_base64_attachment``
     - Base64-encoded attachment sent correctly

TestHttpAttachmentFetch
~~~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/20_attachments/test_00_attachments.py``

HTTP URL attachment fetching.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_fetch_attachment_from_http_url``
     - Single HTTP URL attachment fetched
   * - ``test_fetch_multiple_http_attachments``
     - Multiple HTTP URLs fetched in parallel
   * - ``test_http_attachment_timeout``
     - Timeout handled gracefully
   * - ``test_http_attachment_invalid_url``
     - Invalid URL handled gracefully

TestLargeFileStorage
~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/20_attachments/test_10_large_files.py``

S3 large file storage tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_small_attachment_sent_normally``
     - Small attachment sent inline
   * - ``test_large_attachment_rewritten_to_link``
     - Large attachment uploaded to S3, link in email
   * - ``test_large_attachment_reject_action``
     - action=reject returns error for large files
   * - ``test_large_attachment_warn_action``
     - action=warn sends with warning
   * - ``test_mixed_attachments_partial_rewrite``
     - Mix of small/large attachments handled
   * - ``test_verify_file_uploaded_to_minio``
     - File actually exists in MinIO bucket

TestTenantLargeFileConfigApi
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/20_attachments/test_10_large_files.py``

Large file configuration API.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_create_tenant_with_large_file_config``
     - Tenant created with S3 config
   * - ``test_update_tenant_large_file_config``
     - S3 config updated via API
   * - ``test_disable_large_file_config``
     - S3 config can be disabled

TestUnicodeEncoding
~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/20_attachments/test_20_unicode.py``

Unicode and international character tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_emoji_in_subject``
     - Emoji preserved in subject line
   * - ``test_emoji_in_body``
     - Emoji preserved in message body
   * - ``test_international_characters``
     - CJK, Arabic, Cyrillic characters preserved
   * - ``test_unicode_in_attachment_filename``
     - Unicode filenames handled correctly


30_delivery - Delivery Handling
-------------------------------

9 tests covering SMTP error handling and delivery reports.

TestSmtpErrorHandling
~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/30_delivery/test_00_smtp_errors.py``

SMTP error simulation tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_permanent_error_marks_message_failed``
     - 550 error marks message as error
   * - ``test_temporary_error_defers_message``
     - 451 error defers message for retry
   * - ``test_rate_limited_smtp_defers_excess_messages``
     - 452 rate limit defers excess messages
   * - ``test_random_errors_mixed_results``
     - Random SMTP behavior produces mixed results

TestRetryLogic
~~~~~~~~~~~~~~

**File:** ``tests/fullstack/30_delivery/test_00_smtp_errors.py``

Retry mechanism tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_retry_count_incremented``
     - retry_count increases on each attempt
   * - ``test_message_error_contains_details``
     - Error field contains SMTP error details

TestDeliveryReports
~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/30_delivery/test_10_delivery_reports.py``

Delivery report callback tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_delivery_report_sent_on_success``
     - Success report sent to client endpoint
   * - ``test_delivery_report_sent_on_error``
     - Error report sent to client endpoint
   * - ``test_mixed_delivery_report``
     - Report includes both success and error


40_operations - Operations
--------------------------

21 tests covering metrics, service control, rate limiting, and retention.

TestMetrics
~~~~~~~~~~~

**File:** ``tests/fullstack/40_operations/test_00_metrics.py``

Prometheus metrics tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_metrics_endpoint``
     - GET /metrics returns Prometheus format

TestServiceControl
~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/40_operations/test_10_service_control.py``

Suspend/activate functionality.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_suspend_and_activate``
     - Basic suspend/activate cycle works
   * - ``test_suspend_single_batch``
     - Suspend specific batch_code only
   * - ``test_suspend_requires_tenant_id``
     - Suspend without tenant_id rejected

TestExtendedSuspendActivate
~~~~~~~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/40_operations/test_10_service_control.py``

Extended suspend/activate tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_suspend_returns_pending_count``
     - Suspend returns count of affected messages
   * - ``test_activate_returns_activated_count``
     - Activate returns count of resumed messages
   * - ``test_suspend_idempotent``
     - Multiple suspends are idempotent
   * - ``test_activate_idempotent``
     - Multiple activates are idempotent
   * - ``test_tenant_isolation_in_suspend``
     - Suspend doesn't affect other tenants
   * - ``test_suspend_with_deferred_messages``
     - Deferred messages handled in suspend
   * - ``test_activate_resumes_deferred_timing``
     - Activate preserves deferred timing

TestAccountRateLimiting
~~~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/40_operations/test_20_rate_limiting.py``

**Marker:** ``rate_limit``

Account-level rate limiting tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_rate_limit_per_minute_defers_excess``
     - Messages over limit_per_minute are deferred
   * - ``test_rate_limit_per_hour``
     - Per-hour rate limit configuration accepted
   * - ``test_rate_limit_reject_behavior``
     - limit_behavior=reject rejects excess messages
   * - ``test_rate_limit_resets_after_window``
     - Rate limit counter resets after window
   * - ``test_rate_limit_independent_per_account``
     - Each account has independent rate limits

TestRetentionCleanup
~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/40_operations/test_30_retention.py``

**Marker:** ``retention``

Data retention tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_cleanup_removes_old_reported_messages``
     - Old reported messages cleaned up
   * - ``test_cleanup_respects_tenant_isolation``
     - Cleanup only affects specified tenant
   * - ``test_unreported_messages_not_cleaned``
     - Messages without reported_ts preserved
   * - ``test_bounced_not_reported_preserved``
     - Bounced but unreported messages preserved
   * - ``test_retention_configurable_per_tenant``
     - Retention period configurable per tenant


50_security - Security
----------------------

15 tests covering tenant isolation, input sanitization, and authentication.

TestTenantIsolation
~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/50_security/test_00_isolation.py``

Tenant isolation tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_messages_routed_to_correct_smtp``
     - Each tenant's messages go to correct SMTP
   * - ``test_run_now_triggers_dispatch``
     - run-now triggers dispatch for specified tenant

TestSecurityInputSanitization
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/50_security/test_10_security.py``

Input sanitization tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_sql_injection_in_tenant_id``
     - SQL injection in tenant_id handled safely
   * - ``test_sql_injection_in_message_id``
     - SQL injection in message_id handled safely
   * - ``test_xss_in_message_subject``
     - XSS in subject stored literally, not executed
   * - ``test_path_traversal_in_attachment_path``
     - Path traversal attempts handled safely
   * - ``test_oversized_payload_rejection``
     - Large payloads don't crash server

TestPerTenantApiKeys
~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/50_security/test_20_tenant_auth.py``

Per-tenant API key authentication.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_tenant_token_access_own_resources``
     - Tenant token accesses own resources
   * - ``test_tenant_token_rejected_for_other_tenant``
     - Tenant token rejected for other tenant
   * - ``test_global_token_fallback``
     - Global token works for all tenants
   * - ``test_invalid_token_rejected``
     - Invalid token returns 403
   * - ``test_missing_token_rejected``
     - Missing token returns 401
   * - ``test_token_rotation``
     - Token can be rotated
   * - ``test_token_revocation``
     - Revoked token rejected
   * - ``test_tenant_token_scoped_operations``
     - Tenant token limited to own operations


60_imap - Bounce Detection
--------------------------

19 tests covering bounce parsing, headers, and live IMAP polling.

TestBounceDetection
~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/60_imap/test_00_bounce.py``

Bounce header and field tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_x_genro_mail_id_header_added``
     - X-Genro-Mail-ID header added to outgoing email
   * - ``test_bounce_fields_in_message_list``
     - Bounce fields present in /messages response
   * - ``test_message_includes_bounce_tracking_fields``
     - MessageRecord has bounce_type, bounce_code, etc.
   * - ``test_multiple_messages_unique_mail_ids``
     - Each message gets unique X-Genro-Mail-ID
   * - ``test_bounce_header_with_custom_headers``
     - X-Genro-Mail-ID coexists with custom headers

TestBounceEndToEnd
~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/60_imap/test_00_bounce.py``

**Marker:** ``bounce_e2e``

End-to-end bounce detection tests.

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_imap_server_accessible``
     - Dovecot IMAP server is accessible
   * - ``test_bounce_email_injection``
     - Bounce email injected via IMAP APPEND
   * - ``test_dsn_bounce_format_valid``
     - Generated DSN bounce is RFC 3464 compliant
   * - ``test_soft_bounce_email_format``
     - Soft bounce (4xx) format is correct
   * - ``test_bounce_parser_extracts_original_id``
     - BounceParser extracts X-Genro-Mail-ID
   * - ``test_bounce_parser_soft_vs_hard``
     - BounceParser classifies hard vs soft bounces
   * - ``test_message_sent_includes_tracking_header``
     - Sent message includes tracking header
   * - ``test_bounce_updates_message_record``
     - Bounce updates message in database
   * - ``test_multiple_bounces_correlation``
     - Multiple bounces correlated to correct messages

TestBounceLivePolling
~~~~~~~~~~~~~~~~~~~~~

**File:** ``tests/fullstack/60_imap/test_10_bounce_live.py``

**Marker:** ``bounce_e2e``

Live BounceReceiver polling tests.

.. note::

   These tests require Dovecot IMAP server. Start with::

      docker compose -f tests/docker/docker-compose.fulltest.yml --profile bounce up -d

.. list-table::
   :widths: 50 50
   :header-rows: 1

   * - Test
     - Description
   * - ``test_live_hard_bounce_detected_automatically``
     - Hard bounce detected by BounceReceiver
   * - ``test_live_soft_bounce_detected``
     - Soft bounce detected and classified
   * - ``test_bounce_included_in_delivery_report``
     - Bounce info included in delivery report
   * - ``test_multiple_bounces_processed_in_batch``
     - Multiple bounces processed in single poll
   * - ``test_imap_message_deleted_after_processing``
     - Processed bounce deleted from IMAP mailbox


Test Flow Diagrams
------------------

Basic Message Flow
~~~~~~~~~~~~~~~~~~

.. code-block:: text

   pytest                    Mail Proxy             MailHog
     │                           │                     │
     │  POST /commands/add-messages                    │
     │─────────────────────────────►│                  │
     │                           │                     │
     │  200 OK                   │                     │
     │◄─────────────────────────────│                  │
     │                           │                     │
     │  POST /commands/run-now   │                     │
     │─────────────────────────────►│                  │
     │                           │  SMTP SEND         │
     │                           │────────────────────►│
     │                           │  250 OK            │
     │                           │◄────────────────────│
     │                           │                     │
     │  GET /api/v2/messages     │                     │
     │────────────────────────────────────────────────►│
     │  [captured emails]        │                     │
     │◄────────────────────────────────────────────────│

Bounce Detection Flow
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: text

   pytest           Mail Proxy          MailHog         Dovecot
     │                  │                  │               │
     │  add-messages    │                  │               │
     │─────────────────►│                  │               │
     │                  │  SMTP SEND       │               │
     │                  │─────────────────►│               │
     │                  │  (X-Genro-Mail-ID: msg-123)     │
     │                  │                  │               │
     │  IMAP APPEND (DSN bounce)          │               │
     │───────────────────────────────────────────────────►│
     │                  │                  │               │
     │                  │  BounceReceiver polls           │
     │                  │───────────────────────────────►│
     │                  │  (fetches DSN, parses)          │
     │                  │◄───────────────────────────────│
     │                  │  mark_bounced(msg-123)          │
     │                  │                  │               │
     │  GET /messages   │                  │               │
     │─────────────────►│                  │               │
     │  [{bounce_type: "hard", ...}]       │               │
     │◄─────────────────│                  │               │


See Also
--------

- :doc:`fullstack_testing` - Infrastructure setup and quick start guide
- ``tests/fullstack/README.md`` - Quick reference for running tests
