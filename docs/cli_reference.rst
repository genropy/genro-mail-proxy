
CLI Reference
=============

The ``mail-proxy`` command-line interface provides complete management of
mail-proxy instances, tenants, and accounts without using the HTTP API.

Global Commands
---------------

These commands work at the global level, not tied to a specific instance.

``mail-proxy list``
^^^^^^^^^^^^^^^^^^^

List all configured mail-proxy instances.

.. code-block:: bash

   mail-proxy list

Output shows instance name, status (running/stopped), host, port, and PID.

``mail-proxy start <instance>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Start a mail-proxy instance. Creates the instance if it doesn't exist.

.. code-block:: bash

   # Start with defaults (foreground)
   mail-proxy start myserver

   # Start in background
   mail-proxy start myserver --background

   # Start with custom host/port
   mail-proxy start myserver --host 127.0.0.1 --port 9000

   # Start with auto-reload (development)
   mail-proxy start myserver --reload

Options:

- ``--host``: Bind address (default: 0.0.0.0)
- ``--port``: Port number (default: 8000, auto-increments if busy)
- ``--background``: Run as daemon process
- ``--reload``: Enable auto-reload on code changes

``mail-proxy stop <instance>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Stop a running instance.

.. code-block:: bash

   mail-proxy stop myserver

   # Force kill if graceful shutdown fails
   mail-proxy stop myserver --force

Options:

- ``--force``: Send SIGKILL instead of SIGTERM

``mail-proxy restart <instance>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Restart an instance (stop + start).

.. code-block:: bash

   mail-proxy restart myserver

   # Force restart
   mail-proxy restart myserver --force

``mail-proxy status <instance>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Show detailed status of an instance.

.. code-block:: bash

   mail-proxy status myserver

``mail-proxy delete <instance>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Delete an instance and all its data.

.. code-block:: bash

   mail-proxy delete myserver

   # Skip confirmation prompt
   mail-proxy delete myserver --force

**Warning**: This deletes the SQLite database and all configuration.

Instance Commands
-----------------

These commands operate on a specific instance. Use ``mail-proxy <instance> <command>``.

``mail-proxy <instance> info``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Show instance configuration and statistics.

.. code-block:: bash

   mail-proxy myserver info

   # JSON output
   mail-proxy myserver info --json

``mail-proxy <instance> stats``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Show queue statistics and metrics.

.. code-block:: bash

   mail-proxy myserver stats

   # JSON output
   mail-proxy myserver stats --json

``mail-proxy <instance> token``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Show or regenerate the API token.

.. code-block:: bash

   # Show current token
   mail-proxy myserver token

   # Generate new token
   mail-proxy myserver token --regenerate

``mail-proxy <instance> connect``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Open an interactive Python REPL connected to the instance.

.. code-block:: bash

   mail-proxy myserver connect

   # With custom token
   mail-proxy myserver connect --token your-token

The REPL provides a ``proxy`` object for direct API access. See :doc:`usage`
for REPL examples.

Tenant Commands
---------------

Manage tenants within an instance. Use ``mail-proxy <instance> tenants <command>``.

``mail-proxy <instance> tenants list``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

List all tenants.

.. code-block:: bash

   mail-proxy myserver tenants list

   # Only active tenants
   mail-proxy myserver tenants list --active-only

   # JSON output
   mail-proxy myserver tenants list --json

``mail-proxy <instance> tenants show <tenant_id>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Show details of a specific tenant.

.. code-block:: bash

   mail-proxy myserver tenants show acme

   # JSON output
   mail-proxy myserver tenants show acme --json

``mail-proxy <instance> tenants add``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Add a new tenant (interactive).

.. code-block:: bash

   # Interactive mode
   mail-proxy myserver tenants add

   # With parameters
   mail-proxy myserver tenants add \
     --id acme \
     --name "ACME Corporation" \
     --sync-url https://api.acme.com/mail-sync

Options:

- ``--id``: Tenant identifier (required)
- ``--name``: Display name
- ``--sync-url``: Delivery report callback URL
- ``--attachment-url``: Attachment fetch URL
- ``--auth-method``: Authentication method (bearer, basic, header)
- ``--auth-token``: Authentication token/password
- ``--active/--inactive``: Initial status

``mail-proxy <instance> tenants update <tenant_id>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Update an existing tenant.

.. code-block:: bash

   mail-proxy myserver tenants update acme --name "ACME Corp"

``mail-proxy <instance> tenants delete <tenant_id>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Delete a tenant.

.. code-block:: bash

   mail-proxy myserver tenants delete acme

   # Skip confirmation
   mail-proxy myserver tenants delete acme --force

Tenant-Scoped Commands
----------------------

These commands operate within a specific tenant context.
Use ``mail-proxy <instance> <tenant_id> <command>``.

``mail-proxy <instance> <tenant> info``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Show tenant information.

.. code-block:: bash

   mail-proxy myserver acme info

   # JSON output
   mail-proxy myserver acme info --json

``mail-proxy <instance> <tenant> run-now``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Trigger immediate dispatch cycle for this tenant's messages.

.. code-block:: bash

   mail-proxy myserver acme run-now

Account Commands
----------------

Manage SMTP accounts within a tenant.
Use ``mail-proxy <instance> <tenant> accounts <command>``.

``mail-proxy <instance> <tenant> accounts list``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

List all SMTP accounts for this tenant.

.. code-block:: bash

   mail-proxy myserver acme accounts list

   # JSON output
   mail-proxy myserver acme accounts list --json

Output includes: ``id``, ``tenant_id``, ``host``, ``port``, ``user``, and rate limit settings.

``mail-proxy <instance> <tenant> accounts show <account_id>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Show details of a specific account.

.. code-block:: bash

   mail-proxy myserver acme accounts show smtp-primary

   # JSON output
   mail-proxy myserver acme accounts show smtp-primary --json

``mail-proxy <instance> <tenant> accounts add``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Add a new SMTP account (interactive).

.. code-block:: bash

   # Interactive mode (recommended)
   mail-proxy myserver acme accounts add

   # With parameters
   mail-proxy myserver acme accounts add \
     --id smtp-primary \
     --host smtp.gmail.com \
     --port 587 \
     --username user@gmail.com \
     --password app-password \
     --use-tls

Options:

- ``--id``: Account identifier (required)
- ``--host``: SMTP server hostname (required)
- ``--port``: SMTP server port (default: 587)
- ``--username``: SMTP username
- ``--password``: SMTP password
- ``--use-tls``: Enable STARTTLS
- ``--use-ssl``: Enable SSL/TLS
- ``--from-address``: Default sender address
- ``--hourly-limit``: Rate limit per hour
- ``--daily-limit``: Rate limit per day

``mail-proxy <instance> <tenant> accounts delete <account_id>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Delete an SMTP account.

.. code-block:: bash

   mail-proxy myserver acme accounts delete smtp-primary

   # Skip confirmation
   mail-proxy myserver acme accounts delete smtp-primary --force

Message Commands
----------------

Manage messages within a tenant.
Use ``mail-proxy <instance> <tenant> messages <command>``.

``mail-proxy <instance> <tenant> messages list``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

List queued messages.

.. code-block:: bash

   mail-proxy myserver acme messages list

   # Filter by status
   mail-proxy myserver acme messages list --status pending
   mail-proxy myserver acme messages list --status sent
   mail-proxy myserver acme messages list --status error

   # Limit results
   mail-proxy myserver acme messages list --limit 50

   # Export as CSV
   mail-proxy myserver acme messages list --csv > messages.csv

   # JSON output
   mail-proxy myserver acme messages list --json

Output includes: ``pk`` (internal UUID), ``id`` (client-provided), ``tenant_id``,
``tenant_name``, ``account_id``, ``status``, ``priority``, and message details.

``mail-proxy <instance> <tenant> send <file.eml>``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Send a message from an .eml file.

.. code-block:: bash

   mail-proxy myserver acme send /path/to/email.eml

   # Specify account
   mail-proxy myserver acme send email.eml --account smtp-primary

   # Set priority
   mail-proxy myserver acme send email.eml --priority high

REPL-Only Operations
--------------------

Some advanced operations are available only through the interactive REPL
(``mail-proxy <instance> connect``). These require the full Python client
context:

**Message Operations:**

.. code-block:: python

   # Delete specific messages
   proxy.messages.delete(["msg-1", "msg-2"], tenant_id="acme")

   # Cleanup old reported messages
   proxy.messages.cleanup("acme", older_than_seconds=86400)

**Direct API Access:**

.. code-block:: python

   # Access raw API responses
   proxy._get("/metrics")
   proxy._post("/commands/run-now")

See :doc:`usage` for more REPL examples and the full client API.

Exit Codes
----------

The CLI uses standard exit codes:

- ``0``: Success
- ``1``: General error
- ``2``: Invalid arguments or usage error

Environment Variables
---------------------

``GMP_API_TOKEN``
   API token for authentication. Can also be set via ``--token`` flag.

``GMP_DEBUG``
   Enable debug logging when set to ``1``.

Configuration Files
-------------------

Instance data is stored in ``~/.mail-proxy/<instance>/``:

- ``mail_service.db``: SQLite database with all data
- ``pid``: PID file for running instances
- ``config.json``: Instance configuration

