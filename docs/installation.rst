
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

Environment variables:

- ``GMP_DB_PATH``: Database connection string. Formats:

  - ``/path/to/db.sqlite`` - SQLite file (default: ``/data/mail_service.db``)
  - ``postgresql://user:pass@host:5432/db`` - PostgreSQL

Docker Compose with PostgreSQL
------------------------------

The default ``docker-compose.yml`` includes PostgreSQL:

.. code-block:: yaml

   # docker-compose.yml
   services:
     db:
       image: postgres:16-alpine
       environment:
         POSTGRES_USER: mailproxy
         POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:-changeme}
         POSTGRES_DB: mailproxy
       volumes:
         - pgdata:/var/lib/postgresql/data
       healthcheck:
         test: ["CMD-SHELL", "pg_isready -U mailproxy"]
         interval: 5s
         timeout: 5s
         retries: 5

     mailservice:
       build: .
       image: genro-mail-proxy:latest
       environment:
         - GMP_DB_PATH=postgresql://mailproxy:${POSTGRES_PASSWORD:-changeme}@db:5432/mailproxy
       ports:
         - "8000:8000"
       depends_on:
         db:
           condition: service_healthy
       restart: unless-stopped

   volumes:
     pgdata:

.. code-block:: bash

   # Start with default password
   docker compose up -d

   # Or set a custom password
   POSTGRES_PASSWORD=mysecret docker compose up -d

Docker Compose with SQLite
--------------------------

For simpler deployments using SQLite:

.. code-block:: yaml

   # docker-compose-sqlite.yml
   services:
     mailservice:
       build: .
       image: genro-mail-proxy:latest
       environment:
         - GMP_DB_PATH=/data/mail_service.db
       ports:
         - "8000:8000"
       volumes:
         - maildata:/data
       restart: unless-stopped

   volumes:
     maildata:

.. code-block:: bash

   docker compose -f docker-compose-sqlite.yml up -d

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
