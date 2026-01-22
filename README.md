# genro-mail-proxy

[![PyPI version](https://img.shields.io/pypi/v/genro-mail-proxy)](https://pypi.org/project/genro-mail-proxy/)
[![Tests](https://github.com/genropy/genro-mail-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/genropy/genro-mail-proxy/actions/workflows/tests.yml)
[![codecov](https://codecov.io/gh/genropy/genro-mail-proxy/branch/main/graph/badge.svg)](https://codecov.io/gh/genropy/genro-mail-proxy)
[![Documentation](https://readthedocs.org/projects/genro-mail-proxy/badge/?version=latest)](https://genro-mail-proxy.readthedocs.io/)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)

A microservice that decouples email delivery from your application.

## What it does

genro-mail-proxy sits between your application and SMTP servers. Your application sends messages to the proxy via REST API; the proxy handles delivery with:

- **Persistent queue**: Messages are stored in SQLite and survive restarts
- **Automatic retry**: Failed deliveries are retried with exponential backoff
- **Rate limiting**: Per-account limits (minute/hour/day) shared across instances
- **Priority queuing**: Four levels (immediate, high, medium, low) with FIFO within each
- **Delivery reports**: Results are posted back to your application via HTTP callback
- **Multi-tenancy**: Multiple organizations can share one instance with separate accounts

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

The proxy supports multiple attachment sources:

| Format | Example | Description |
| ------ | ------- | ----------- |
| `base64:content` | `base64:SGVsbG8=` | Inline base64-encoded content |
| `/absolute/path` | `/tmp/file.pdf` | Local filesystem absolute path |
| `relative/path` | `uploads/doc.pdf` | Relative to configured base_dir |
| `@params` | `@doc_id=123` | HTTP POST to default endpoint |
| `@[url]params` | `@[https://api.example.com]id=456` | HTTP POST to specific URL |

A two-tiered cache (memory + disk) reduces redundant fetches. Filenames can include an MD5 hash marker (`report_{MD5:abc123}.pdf`) for cache lookup.

## Configuration

Configuration via `config.ini` or environment variables (prefixed with `GMP_`):

```ini
[attachments]
base_dir = /var/attachments
http_endpoint = https://api.example.com/attachments
http_auth_method = bearer
http_auth_token = your-secret-token

cache_memory_max_items = 100
cache_disk_dir = /var/cache/mail-proxy
```

See [Usage](https://genro-mail-proxy.readthedocs.io/en/latest/usage.html) for all options.

## Performance notes

- **Request latency**: ~30ms to queue a message (vs ~600ms for direct SMTP)
- **Throughput**: Limited by SMTP provider rate limits, not the proxy
- **Memory**: Attachment content is held in memory during send; use HTTP endpoints for large files

The SQLite database handles typical workloads but doesn't scale well under high concurrency. For high-volume deployments, consider running multiple instances with separate databases.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

Apache License 2.0 — see [LICENSE](LICENSE) for details.

Copyright 2025 Softwell S.r.l. — Genropy Team
