
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
