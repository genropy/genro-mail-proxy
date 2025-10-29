
Installation
============

Docker
------

.. code-block:: bash

   docker build -t genro-mail-proxy .
   docker run -p 8000:8000 \\
     -e GMP_CLIENT_SYNC_URL=https://your-app/proxy_sync \\
     -e GMP_CLIENT_SYNC_USER=syncuser \\
     -e GMP_CLIENT_SYNC_PASSWORD=syncpass \\
     -e GMP_API_TOKEN=your-secret-token \\
     genro-mail-proxy

See ``config.ini.example`` for all available environment variables (all prefixed with ``GMP_``).

Docker Compose
--------------

.. code-block:: bash

   docker compose up -d --build

Local (Python)
--------------

.. code-block:: bash

   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   python main.py

Developer Installation
----------------------

For development work, install additional testing and linting tools:

.. code-block:: bash

   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   pip install -r requirements-dev.txt

   # Run tests
   pytest

   # Run linter
   flake8

Network Requirements
--------------------

For production deployment, ensure proper network connectivity and firewall configuration.

See :doc:`network_requirements` for detailed information on:

- Required network connections (Genropy ↔ genro-mail-proxy ↔ SMTP servers)
- Firewall rules and port configuration
- Security considerations (TLS, authentication, network isolation)
- Deployment scenarios (single host, separate hosts, Kubernetes)
- Troubleshooting network connectivity issues

Quick checklist:

1. **Genropy → genro-mail-proxy**: Allow HTTP/HTTPS on port 8000 (or configured ``GMP_PORT``)
2. **genro-mail-proxy → Genropy**: Allow HTTP/HTTPS for delivery reports (``GMP_CLIENT_SYNC_URL``)
3. **genro-mail-proxy → SMTP**: Allow outbound TCP on port 587 (STARTTLS) or 465 (SMTPS)
4. **DNS**: Ensure SMTP server hostnames can be resolved
5. **Authentication**: Configure ``GMP_API_TOKEN``, ``GMP_CLIENT_SYNC_USER``/``GMP_CLIENT_SYNC_PASSWORD``

Configuration
-------------

See ``config.ini.example`` for all available configuration options and environment variables.
