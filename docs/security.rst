Security
========

This document covers security features and best practices for genro-mail-proxy.

.. contents:: Table of Contents
   :local:
   :depth: 2

Credential Encryption
---------------------

**License**: Apache 2.0

SMTP passwords and other sensitive credentials are encrypted at rest using
**AES-256-GCM** (Galois/Counter Mode). This provides both confidentiality
and integrity protection.

How It Works
^^^^^^^^^^^^

1. When you add an SMTP account, the password is encrypted before storage
2. The encrypted value is prefixed with ``ENC:`` to identify it
3. When the proxy needs to connect to SMTP, it decrypts the password on-the-fly
4. The plaintext password is never stored in the database

Encryption Details
^^^^^^^^^^^^^^^^^^

.. list-table::
   :widths: 30 70

   * - Algorithm
     - AES-256-GCM (authenticated encryption)
   * - Key size
     - 256 bits (32 bytes)
   * - Nonce size
     - 96 bits (12 bytes, unique per encryption)
   * - Authentication tag
     - 128 bits (integrity verification)
   * - Storage format
     - ``ENC:<base64(nonce + ciphertext + tag)>``

Configuring the Encryption Key
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

The encryption key is loaded from these sources (in priority order):

1. **Environment variable** (recommended for Docker/Kubernetes)::

    export MAIL_PROXY_ENCRYPTION_KEY="<base64-encoded-32-byte-key>"

2. **Secrets file** (for Docker/Kubernetes secrets)::

    # Mount at /run/secrets/encryption_key
    # File contains raw 32 bytes (not base64)

Generating a Key
^^^^^^^^^^^^^^^^

Use the built-in key generator::

    python -c "from tools.encryption import generate_key; print(generate_key())"

Or with OpenSSL::

    openssl rand -base64 32

Example output: ``K7gNU3sdo+OL0wNhqoVWhr3g6s1xYv72ol/pe/Unols=``

Docker Example
^^^^^^^^^^^^^^

.. code-block:: bash

    docker run -p 8000:8000 \
      -e GMP_API_TOKEN=your-api-token \
      -e MAIL_PROXY_ENCRYPTION_KEY=K7gNU3sdo+OL0wNhqoVWhr3g6s1xYv72ol/pe/Unols= \
      genro-mail-proxy

Kubernetes Example
^^^^^^^^^^^^^^^^^^

.. code-block:: yaml

    apiVersion: v1
    kind: Secret
    metadata:
      name: mail-proxy-secrets
    type: Opaque
    data:
      encryption-key: SzdnTlUzc2RvK09MMHdOaHFvVldocjNnNnMxeFl2NzJvbC9wZS9Vbm9scz0=

    ---
    apiVersion: apps/v1
    kind: Deployment
    spec:
      template:
        spec:
          containers:
          - name: mail-proxy
            env:
            - name: MAIL_PROXY_ENCRYPTION_KEY
              valueFrom:
                secretKeyRef:
                  name: mail-proxy-secrets
                  key: encryption-key

Key Rotation
^^^^^^^^^^^^

To rotate the encryption key:

1. Export all account credentials (they will be decrypted with old key)
2. Update the encryption key environment variable
3. Re-add the accounts (they will be encrypted with new key)

.. warning::

   If you lose the encryption key, encrypted passwords cannot be recovered.
   Store the key securely in a secrets manager (Vault, AWS Secrets Manager, etc.).

API Authentication
------------------

**License**: Apache 2.0

All API endpoints require authentication via the ``X-API-Token`` header.

Token Types
^^^^^^^^^^^

.. list-table::
   :header-rows: 1
   :widths: 20 40 40

   * - Token Type
     - Scope
     - Use Case
   * - Admin Token
     - Full access to all tenants
     - System administration, multi-tenant management
   * - Tenant Token
     - Single tenant only
     - Application integration, per-tenant access

Admin Token Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^

Set via environment variable::

    export GMP_API_TOKEN=your-secret-admin-token

Or via CLI when starting::

    mail-proxy start myserver --api-token your-secret-admin-token

Tenant Token Configuration
^^^^^^^^^^^^^^^^^^^^^^^^^^

*(BSL 1.1 - Enterprise Edition)*

Tenant-specific tokens are managed via the API:

.. code-block:: bash

    # Create tenant with dedicated token
    curl -X POST http://localhost:8000/tenant \
      -H "X-API-Token: admin-token" \
      -H "Content-Type: application/json" \
      -d '{
        "tenant_id": "acme",
        "name": "ACME Corp",
        "token": "acme-secret-token"
      }'

Token Storage
^^^^^^^^^^^^^

- Admin token: stored as SHA-256 hash in instance configuration
- Tenant tokens: stored as SHA-256 hash in database (BSL 1.1)
- Tokens are **never** stored in plaintext

Tenant Isolation
----------------

*(BSL 1.1 - Enterprise Edition)*

Multi-tenant deployments provide strict data isolation:

.. list-table::
   :widths: 30 70

   * - Messages
     - Each tenant's messages are tagged with ``tenant_id``; queries are filtered
   * - Accounts
     - SMTP accounts belong to a specific tenant; no cross-tenant access
   * - Rate Limits
     - Rate limits are per-account, scoped to tenant
   * - Callbacks
     - Delivery reports are sent only to the tenant's configured endpoint

Database-Level Isolation
^^^^^^^^^^^^^^^^^^^^^^^^

All database queries include tenant filtering:

.. code-block:: sql

    -- Messages are always filtered by tenant
    SELECT * FROM messages WHERE tenant_id = ?

    -- Accounts belong to tenants
    SELECT * FROM accounts WHERE tenant_id = ?

API-Level Isolation
^^^^^^^^^^^^^^^^^^^

Tenant tokens can only access their own data:

.. code-block:: bash

    # With tenant token, only see own messages
    curl http://localhost:8000/messages \
      -H "X-API-Token: acme-tenant-token"
    # Returns only ACME's messages

    # Admin token can specify tenant
    curl "http://localhost:8000/messages?tenant_id=acme" \
      -H "X-API-Token: admin-token"

Network Security
----------------

TLS for SMTP
^^^^^^^^^^^^

Always use TLS when connecting to SMTP servers:

.. code-block:: bash

    mail-proxy myserver acme accounts add \
      --host smtp.gmail.com \
      --port 587 \
      --tls starttls \
      --user user@gmail.com

Supported TLS modes:

- ``none``: No encryption (not recommended)
- ``starttls``: Upgrade connection with STARTTLS (port 587)
- ``ssl``: Direct TLS connection (port 465)

TLS for API
^^^^^^^^^^^

Run behind a reverse proxy (nginx, Traefik) with TLS termination:

.. code-block:: nginx

    server {
        listen 443 ssl;
        server_name mail-proxy.example.com;

        ssl_certificate /etc/letsencrypt/live/mail-proxy.example.com/fullchain.pem;
        ssl_certificate_key /etc/letsencrypt/live/mail-proxy.example.com/privkey.pem;

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
        }
    }

Security Best Practices
-----------------------

1. **Use strong encryption keys**: Generate random 32-byte keys, never use predictable values

2. **Rotate tokens periodically**: Change API tokens regularly, especially after personnel changes

3. **Use secrets management**: Store encryption keys and tokens in Vault, AWS Secrets Manager, or similar

4. **Enable TLS everywhere**: Use TLS for SMTP connections and HTTPS for API access

5. **Restrict network access**: Run the proxy on a private network, expose only through reverse proxy

6. **Monitor access logs**: Track API access patterns for anomalies

7. **Keep the proxy updated**: Apply security patches promptly

Audit Logging
-------------

The proxy logs security-relevant events:

- API authentication attempts (success/failure)
- Tenant creation/deletion
- Account modifications
- Configuration changes

Configure log level for security auditing::

    export GMP_LOG_LEVEL=INFO

Example log entries:

.. code-block:: text

    INFO  - API auth successful for tenant 'acme'
    WARN  - API auth failed: invalid token
    INFO  - Account 'smtp1' created for tenant 'acme'
