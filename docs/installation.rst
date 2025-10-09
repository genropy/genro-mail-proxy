
Installation
============

Docker
------

.. code-block:: bash

   docker build -t gnr-async-mail-service .
   docker run -p 8000:8000 \\
     -e SMTP_USER=... -e SMTP_PASSWORD=... \\
     -e FETCH_URL=https://your/api gnr-async-mail-service

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
