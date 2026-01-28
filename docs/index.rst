
genro-mail-proxy
================

.. image:: _static/logo.png
   :alt: logo
   :align: right
   :width: 120px

A microservice that decouples email delivery from your application.

What it does
------------

genro-mail-proxy sits between your application and SMTP servers. Your application
sends messages to the proxy via REST API; the proxy handles delivery with:

- **Persistent queue**: Messages are stored in SQLite and survive restarts
- **Automatic retry**: Failed deliveries are retried with exponential backoff
- **Rate limiting**: Per-account limits (minute/hour/day) shared across instances
- **Priority queuing**: Four levels (immediate, high, medium, low) with FIFO within each
- **Delivery reports**: Results are posted back to your application via HTTP callback
- **Bounce detection**: IMAP polling for bounces with DSN parsing and hard/soft classification (BSL 1.1)
- **Multi-tenancy**: Multiple organizations can share one instance with separate accounts (BSL 1.1)

Architecture
~~~~~~~~~~~~

.. code-block:: text

   ┌─────────────┐      REST       ┌──────────────────┐      SMTP      ┌─────────────┐
   │ Application │ ──────────────► │ genro-mail-proxy │ ─────────────► │ SMTP Server │
   └─────────────┘                 └──────────────────┘                └─────────────┘
          ▲                               │
          │                               │
          └───────────────────────────────┘
                   delivery reports

The proxy exposes a FastAPI REST API secured by ``X-API-Token``. Background loops
handle SMTP delivery and delivery report callbacks.

When to use it
--------------

Consider this proxy when:

- **Multiple application instances** need shared rate limits for outbound email
- **Email delivery should not block** your application's main request flow
- **Delivery tracking** is needed with central logging and metrics
- **Retry logic** is required without implementing it in every service
- **Multi-tenant isolation** is needed for different organizations or environments

When NOT to use it
------------------

This proxy adds operational complexity. Direct SMTP may be simpler when:

- You have a **single application instance** with low email volume
- **Latency is acceptable** (direct SMTP adds ~500-600ms per send)
- **No retry logic** is needed (transactional emails with immediate feedback)
- **No rate limiting** is required by your SMTP provider
- You prefer **fewer moving parts** in your infrastructure

Command-line interface
----------------------

The ``mail-proxy`` CLI manages instances without going through the HTTP API:

.. code-block:: bash

   # Instance management
   mail-proxy list                          # List all instances
   mail-proxy start myserver                # Start an instance
   mail-proxy stop myserver                 # Stop an instance
   mail-proxy myserver info                 # Show instance details

   # Tenant management
   mail-proxy myserver tenants list         # List tenants
   mail-proxy myserver tenants add acme     # Add a tenant (interactive)

   # Account management (per tenant)
   mail-proxy myserver acme accounts list   # List SMTP accounts
   mail-proxy myserver acme accounts add    # Add account (interactive)

   # Message operations
   mail-proxy myserver acme messages list   # List queued messages
   mail-proxy myserver acme send email.eml  # Send from .eml file
   mail-proxy myserver acme run-now         # Trigger immediate dispatch

Each instance stores its configuration in ``~/.mail-proxy/<name>/mail_service.db``.
The CLI supports both command-line arguments and interactive prompts for complex
operations like adding tenants or accounts.

Quick start
-----------

**Docker**:

.. code-block:: bash

   docker run -p 8000:8000 \
     -e GMP_API_TOKEN=your-secret-token \
     genro-mail-proxy

**CLI**:

.. code-block:: bash

   pip install genro-mail-proxy
   mail-proxy start myserver

Then configure a tenant, add an SMTP account, and start sending messages.
See :doc:`installation` and :doc:`usage` for details.

Performance notes
-----------------

When the proxy is under load:

- **Request latency**: ~30ms to queue a message (vs ~600ms for direct SMTP)
- **Throughput**: Limited by SMTP provider rate limits, not the proxy
- **Memory**: Attachment content is held in memory during send; use HTTP endpoints
  for large files instead of base64 encoding

The SQLite database handles typical workloads but doesn't scale well under high
concurrency. For high-volume deployments, consider running multiple instances
with separate databases.

.. toctree::
   :maxdepth: 2
   :hidden:

   overview
   features
   architecture_overview
   protocol
   installation
   network_requirements
   usage
   cli_reference
   attachments
   rate_limiting
   priority_queuing
   monitoring
   example_client
   integrations
   multi_tenancy
   pec
   api_reference
   faq
   appendix_endpoints
   modules
   contributing
   fullstack_testing
   fullstack_testing_reference
