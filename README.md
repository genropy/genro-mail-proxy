# genro-mail-proxy

[![PyPI version](https://img.shields.io/pypi/v/genro-mail-proxy?style=flat)](https://pypi.org/project/genro-mail-proxy/)
[![Tests](https://github.com/genropy/genro-mail-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/genropy/genro-mail-proxy/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/genropy/genro-mail-proxy/branch/main/graph/badge.svg)](https://codecov.io/gh/genropy/genro-mail-proxy)
[![Documentation](https://readthedocs.org/projects/genro-mail-proxy/badge/?version=latest)](https://genro-mail-proxy.readthedocs.io/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A microservice that decouples email delivery from your application.

## What it does

genro-mail-proxy sits between your application and SMTP servers. Your application sends messages to the proxy via REST API; the proxy handles delivery.

### Community Edition (Apache 2.0)

- **Persistent queue**: Messages stored in SQLite or PostgreSQL, survive restarts
- **Automatic retry**: Failed deliveries retried with exponential backoff
- **Rate limiting**: Per-account limits (minute/hour/day) shared across instances
- **Priority queuing**: Four levels (immediate, high, medium, low) with FIFO within each
- **Delivery reports**: Results posted back to your application via HTTP callback
- **Connection pooling**: SMTP connections pooled with acquire/release semantics
- **Credential encryption**: SMTP passwords encrypted at rest with AES-256-GCM
- **Attachments**: Multi-source fetching (base64, filesystem, HTTP, endpoint) with caching
- **Prometheus metrics**: Built-in monitoring via `/metrics` endpoint
- **CLI tool**: Full instance management without HTTP API

### Enterprise Edition (BSL 1.1)

- **Multi-tenancy**: Multiple organizations share one instance with data isolation
- **Bounce detection**: IMAP polling for bounces with DSN parsing and hard/soft classification
- **PEC support**: Italian certified email (Posta Elettronica Certificata) with receipt tracking
- **Large file handling**: Auto-upload attachments to S3/GCS/Azure and replace with download links

```text
┌─────────────┐      REST       ┌──────────────────┐      SMTP      ┌─────────────┐
│ Application │ ──────────────► │ genro-mail-proxy │ ─────────────► │ SMTP Server │
└─────────────┘                 └──────────────────┘                └─────────────┘
        ▲                               │
        │                               │
        └───────────────────────────────┘
                 delivery reports
```

## When to use it

Consider this proxy when:

- Multiple application instances need shared rate limits for outbound email
- Email delivery should not block your application's main request flow
- Delivery tracking is needed with central logging and Prometheus metrics
- Retry logic is required without implementing it in every service
- Multi-tenant isolation is needed for different organizations or environments

## When NOT to use it

This proxy adds operational complexity. Direct SMTP may be simpler when:

- You have a single application instance with low email volume
- Latency is acceptable (direct SMTP adds ~500-600ms per send)
- No retry logic is needed (transactional emails with immediate feedback)
- No rate limiting is required by your SMTP provider
- You prefer fewer moving parts in your infrastructure

## Quick start

**Docker**:

```bash
docker run -p 8000:8000 \
  -e GMP_API_TOKEN=your-secret-token \
  genro-mail-proxy
```

**CLI**:

```bash
pip install genro-mail-proxy
mail-proxy start myserver
```

Then configure a tenant, add an SMTP account, and start sending messages.

## Command-line interface

The `mail-proxy` CLI manages instances without going through the HTTP API:

```bash
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
```

Each instance stores its configuration in `~/.mail-proxy/<name>/mail_service.db`.
The CLI supports both command-line arguments and interactive prompts for complex operations.

## REST API

The proxy exposes a FastAPI REST API secured by `X-API-Token`:

- `POST /commands/add-messages` - Queue messages for delivery
- `GET /messages` - List queued messages
- `POST /commands/run-now` - Trigger immediate dispatch cycle
- `GET /accounts` - List SMTP accounts
- `GET /metrics` - Prometheus metrics

See [API Reference](https://genro-mail-proxy.readthedocs.io/en/latest/api_reference.html) for details.

## Attachment handling

The proxy supports multiple attachment sources via explicit `fetch_mode`:

| fetch_mode | storage_path example | Description |
| ---------- | -------------------- | ----------- |
| `base64` | `base64:SGVsbG8gV29ybGQ=` | Inline base64-encoded content (requires `base64:` prefix) |
| `filesystem` | `/tmp/file.pdf` | Local filesystem path |
| `endpoint` | `doc_id=123` | HTTP POST to tenant's attachment endpoint |
| `http_url` | `https://storage.example.com/file.pdf` | HTTP GET from external URL |

A two-tiered cache (memory + disk) reduces redundant fetches. Filenames can include an MD5 hash marker (`report_{MD5:abc123}.pdf`) for cache lookup.

### Large file offloading *(Enterprise Edition)*

For attachments exceeding a size threshold, the proxy can upload them to external storage (S3, GCS, Azure, or local filesystem) and replace them with download links in the email body.

```bash
pip install genro-mail-proxy[enterprise-s3]  # or [enterprise-gcs], [enterprise-azure]
```

Configure per-tenant via `large_file_config`:

```json
{
  "enabled": true,
  "max_size_mb": 10,
  "storage_url": "s3://bucket/mail-attachments",
  "action": "rewrite"
}
```

Actions: `warn` (log only), `reject` (fail message), `rewrite` (upload and replace with link).

## Configuration

Configuration is managed via the CLI. Each instance stores its settings in `~/.mail-proxy/<name>/mail_service.db`.

```bash
# Start an instance (creates it if new)
mail-proxy start myserver

# Add a tenant with attachment endpoint
mail-proxy myserver tenants add
# Interactive prompts: tenant_id, name, base_url, attachment_path, auth method

# Add an SMTP account
mail-proxy myserver acme accounts add
# Interactive prompts: account_id, host, port, user, password, TLS, rate limits
```

For Docker deployments, use environment variables (prefixed with `GMP_`):

```bash
docker run -p 8000:8000 \
  -e GMP_API_TOKEN=your-secret-token \
  -e GMP_DB_PATH=/data/mail_service.db \
  -v mail-data:/data \
  genro-mail-proxy
```

See [Usage](https://genro-mail-proxy.readthedocs.io/en/latest/usage.html) for all options.

## Security

SMTP credentials are encrypted at rest using **AES-256-GCM**. Configure the encryption key:

```bash
# Generate a key
python -c "from tools.encryption import generate_key; print(generate_key())"

# Set via environment variable
export MAIL_PROXY_ENCRYPTION_KEY="<base64-encoded-32-byte-key>"
```

For Docker/Kubernetes, mount the key as a secret or pass via environment variable:

```bash
docker run -p 8000:8000 \
  -e GMP_API_TOKEN=your-api-token \
  -e MAIL_PROXY_ENCRYPTION_KEY=K7gNU3sdo+OL0wNhqoVWhr3g6s1xYv72ol/pe/Unols= \
  genro-mail-proxy
```

See [Security Documentation](https://genro-mail-proxy.readthedocs.io/en/latest/security.html) for key rotation, Kubernetes secrets, and best practices.

## Performance notes

- **Request latency**: ~30ms to queue a message (vs ~600ms for direct SMTP)
- **Throughput**: Limited by SMTP provider rate limits, not the proxy
- **Memory**: Attachment content is held in memory during send; use HTTP endpoints for large files

The SQLite database handles typical workloads but doesn't scale well under high concurrency. For high-volume deployments, use PostgreSQL:

```bash
pip install genro-mail-proxy[postgresql]
```

Then configure the DSN via environment variable or CLI.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

This project uses a **dual-license** model:

| License        | Features                                                                                                                       |
|----------------|--------------------------------------------------------------------------------------------------------------------------------|
| **Apache 2.0** | Core functionality: message queue, retry, rate limiting, priority, delivery reports, attachments, SMTP pooling, REST API, CLI |
| **BSL 1.1**    | Multi-Tenancy, Bounce Detection, PEC Support, Large Files                                                                      |

**Apache 2.0** ([LICENSE](LICENSE)): Free for any use.

**BSL 1.1** ([LICENSE-BSL-1.1](LICENSE-BSL-1.1)): Free for testing, development, and non-production. Production use requires a commercial license from Softwell S.r.l. Each version converts to Apache 2.0 four years after its release (e.g., v0.7.0 released Jan 2026 → Apache 2.0 on Jan 2030).

See [NOTICE](NOTICE) for full details.

Copyright 2025 Softwell S.r.l. — Genropy Team
