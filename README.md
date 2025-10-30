[![Documentation Status](https://readthedocs.org/projects/genro-mail-proxy/badge/?version=latest)](https://genro-mail-proxy.readthedocs.io/en/latest/)

# genro-mail-proxy

**Authors:** Softwell S.r.l. - Giovanni Porcari  
**License:** MIT

Asynchronous email dispatcher microservice with scheduling, rate limiting, attachments (S3/URL/base64), REST API (FastAPI), and Prometheus metrics.

## Why Use an Email Proxy?

Instead of directly connecting to SMTP servers from your application, genro-mail-proxy provides a **decoupled, resilient email delivery layer** with:

- ⚡ **19x faster requests** (32ms vs 620ms) - non-blocking async operations
- 🔄 **Never lose messages** - automatic retry, guaranteed persistence
- 🎯 **Connection pooling** - 10-50x faster for burst sends
- 📊 **Centralized monitoring** - Prometheus metrics, not scattered logs
- 🛡️ **Built-in rate limiting** - shared across all app instances
- 🎛️ **Priority queuing** - immediate/high/medium/low with automatic ordering

**See [Architecture Overview](docs/architecture_overview.rst)** for detailed comparison with direct SMTP.

## Main integration points:

- REST control plane secured by ``X-API-Token`` for queue management and configuration.
- Outbound ``proxy_sync`` call towards Genropy, authenticated via basic auth and configured through ``[client]`` in ``config.ini``.
- Delivery reports and Prometheus metrics to monitor message lifecycle and rate limiting.
- Unified SQLite storage with a single ``messages`` table that tracks queue state (`priority`, `deferred_ts`) and delivery lifecycle (`sent_ts`, `error_ts`, `reported_ts`).
- Background loops:
  - **SMTP dispatch loop** selects records from ``messages`` that lack ``sent_ts``/``error_ts`` and have ``deferred_ts`` in the past, enforces rate limits, then stamps ``sent_ts`` or ``error_ts``/``error``.
  - **Client report loop** batches completed items (sent/error/deferred) that are still missing ``reported_ts`` and posts them to the upstream ``proxy_sync`` endpoint; on acknowledgement the records receive ``reported_ts`` and are later purged according to the retention window.

## Quick start

```bash
docker build -t genro-mail-proxy .
docker run -p 8000:8000 \
  -e GMP_CLIENT_SYNC_URL=https://your-app/proxy_sync \
  -e GMP_CLIENT_SYNC_USER=syncuser \
  -e GMP_CLIENT_SYNC_PASSWORD=syncpass \
  -e GMP_API_TOKEN=your-secret-token \
  genro-mail-proxy
```

See `config.ini.example` for all available environment variables (all prefixed with `GMP_`).

## Example client

A complete integration example is provided in `example_client.py`. This demonstrates the recommended pattern for integrating with the mail service:

```bash
# Install dependencies
pip install fastapi uvicorn aiohttp

# Configure your email address
nano example_config.ini  # Edit recipient_email

# Start the example client
python3 example_client.py

# Send test email
curl -X POST http://localhost:8081/send-test-email
```

The example shows:
- Local-first persistence (never lose messages)
- Async submission to mail service
- run-now trigger for fast delivery
- Delivery report handling via proxy_sync

**See [Example Client Documentation](docs/example_client.rst)** for detailed walkthrough.

## Configuration highlights

- ``[delivery]`` exposes ``delivery_report_retention_seconds`` to control how long reported messages stay in the ``messages`` table (default seven days).
- ``/commands/add-messages`` validates each payload (``id``, ``from``, ``to`` etc.), enqueues valid messages with `priority=2` when omitted, and returns a response with queued count plus a `rejected` list containing `{"id","reason"}` entries for failures.

## Attachment Handling

genro-mail-proxy uses [genro-storage](https://github.com/genropy/genro-storage) for unified attachment handling across multiple storage backends.

### Supported Storage Types

- **base64**: Inline base64-encoded content (always available)
- **S3**: Amazon S3 and compatible object storage
- **HTTP/HTTPS**: Files from web servers and CDNs
- **WebDAV**: Nextcloud, ownCloud, SharePoint
- **Local**: Local filesystem

### Attachment Path Format

All attachments use the `volume:subpath` format:

```
base64:SGVsbG8gV29ybGQh
s3-uploads:documents/report.pdf
cdn:images/logo.png
webdav:shared/contracts/agreement.pdf
```

### Volume Configuration

Volumes can be configured via:

1. **config.ini** (loaded at startup):

```ini
[volumes]
# Shared volumes (accessible by all tenants)
volume.shared-s3.backend = s3
volume.shared-s3.config = {"bucket": "common-uploads", "region": "us-east-1"}

# Tenant-specific volumes
volume.tenant1-storage.backend = s3
volume.tenant1-storage.config = {"bucket": "tenant1-files"}
volume.tenant1-storage.account_id = tenant1
```

2. **REST API** (runtime management):

```bash
# Add volume
curl -X POST http://localhost:8000/volume \
  -H "X-API-Token: your-token" \
  -H "Content-Type: application/json" \
  -d '{"name": "new-volume", "backend": "s3", "config": {"bucket": "my-bucket"}}'

# List volumes
curl http://localhost:8000/volumes -H "X-API-Token: your-token"

# Delete volume
curl -X DELETE http://localhost:8000/volume/volume-name \
  -H "X-API-Token: your-token"
```

**See [VOLUMES.md](VOLUMES.md) for comprehensive volume documentation.**
