
Installation
============

PyPI
----

.. code-block:: bash

   pip install genro-mail-proxy

This installs the ``mail-proxy`` CLI command.

Docker
------

Build and run:

.. code-block:: bash

   docker build -t genro-mail-proxy .
   docker run -p 8000:8000 -v mail-data:/data genro-mail-proxy

The container stores its SQLite database at ``/data/mail_service.db``. Mount a volume
to persist data across container restarts.

Environment variables:

- ``GMP_DB_PATH``: Database file path (default: ``/data/mail_service.db``)

Docker Compose
--------------

.. code-block:: yaml

   # docker-compose.yml
   services:
     mail-proxy:
       build: .
       ports:
         - "8000:8000"
       volumes:
         - mail-data:/data

   volumes:
     mail-data:

.. code-block:: bash

   docker compose up -d

Local Development
-----------------

.. code-block:: bash

   # Clone and install in development mode
   git clone https://github.com/genropy/genro-mail-proxy.git
   cd genro-mail-proxy
   pip install -e ".[dev]"

   # Run tests
   pytest

   # Start the server
   mail-proxy start myserver

Network Requirements
--------------------

For production deployment, ensure proper network connectivity:

1. **Client → mail-proxy**: HTTP/HTTPS on port 8000 (or configured ``GMP_PORT``)
2. **mail-proxy → Client**: HTTP/HTTPS for delivery reports (tenant's ``sync_path``)
3. **mail-proxy → SMTP**: Outbound TCP on port 587 (STARTTLS) or 465 (SMTPS)

See :doc:`network_requirements` for detailed firewall rules and deployment scenarios.
