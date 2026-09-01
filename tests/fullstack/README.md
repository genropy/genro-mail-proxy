# Fullstack Integration Tests — the stress suite

End-to-end tests that validate genro-mail-proxy against real services:
real SMTP delivery, attachments, bounce handling, rate limiting, and the
outbound client protocol, on the Docker compose stack. Every test here
carries the `fullstack` marker (deselect with `-m "not fullstack"`).

The HTTP contract alone — routing, auth, validation, response shapes for
all 26 routes — is covered without Docker by `tests/local_e2e`, which boots
the real app in-process on a temporary SQLite database. Run that suite for
transport changes; run this one for delivery behaviour and load.

Both suites build every URL from `tests/api_routes.py`, the single module
that holds the route paths.

## Prerequisites

- Docker and Docker Compose
- Python 3.10+ with pytest
- At least 8GB RAM

## Quick Start

```bash
# Start infrastructure
docker compose -f tests/docker/docker-compose.fulltest.yml up -d --build

# Wait for services to be ready
docker compose -f tests/docker/docker-compose.fulltest.yml ps

# Run all fullstack tests
pytest tests/fullstack/ -v

# Stop infrastructure
docker compose -f tests/docker/docker-compose.fulltest.yml down
```

## Test Structure

Tests are organized in numbered groups for execution order:

```
tests/fullstack/
├── conftest.py              # Shared fixtures
├── helpers.py               # Helper functions and constants
├── 00_core/                 # Core functionality
│   ├── test_00_health.py        # Health endpoint
│   ├── test_05_docker_integration.py  # Docker connectivity
│   ├── test_10_infrastructure.py      # Infrastructure checks
│   ├── test_20_tenants.py       # Tenant CRUD
│   └── test_30_accounts.py      # Account CRUD
├── 10_messaging/            # Message handling
│   ├── test_00_validation.py    # Input validation
│   ├── test_10_dispatch.py      # Message dispatch
│   ├── test_20_messages.py      # Message API
│   ├── test_30_batch.py         # Batch operations
│   └── test_40_priority.py      # Priority queuing
├── 20_attachments/          # Attachment handling
│   ├── test_00_attachments.py   # Basic attachments
│   ├── test_10_large_files.py   # Large file handling
│   └── test_20_unicode.py       # Unicode filenames
├── 30_delivery/             # Delivery handling
│   ├── test_00_smtp_errors.py   # SMTP error handling
│   └── test_10_delivery_reports.py  # Delivery reports
├── 40_operations/           # Operational features
│   ├── test_00_metrics.py       # Prometheus metrics
│   ├── test_10_service_control.py   # Service control API
│   ├── test_20_rate_limiting.py     # Rate limiting
│   └── test_30_retention.py     # Data retention
├── 50_security/             # Security tests
│   ├── test_00_isolation.py     # Tenant isolation
│   ├── test_10_security.py      # Security checks
│   └── test_20_tenant_auth.py   # Tenant authentication
└── 60_imap/                 # IMAP/Bounce detection
    ├── test_00_bounce.py        # Bounce parsing
    └── test_10_bounce_live.py   # Live bounce polling
```

## Running Tests

### Run All Tests

```bash
pytest tests/fullstack/ -v
```

### Run a Specific Group

```bash
# Core tests
pytest tests/fullstack/00_core/ -v

# Messaging tests
pytest tests/fullstack/10_messaging/ -v

# Attachments tests
pytest tests/fullstack/20_attachments/ -v

# Delivery tests
pytest tests/fullstack/30_delivery/ -v

# Operations tests
pytest tests/fullstack/40_operations/ -v

# Security tests
pytest tests/fullstack/50_security/ -v

# IMAP/Bounce tests
pytest tests/fullstack/60_imap/ -v
```

### Run by Marker

```bash
# Bounce detection tests
pytest tests/fullstack/ -m bounce_e2e -v

# Retention/cleanup tests
pytest tests/fullstack/ -m retention -v

# Rate limiting tests
pytest tests/fullstack/ -m rate_limit -v

# Docker integration tests
pytest tests/fullstack/ -m docker -v

# Exclude specific markers (e.g., for CI)
pytest tests/fullstack/ -m "not bounce_e2e" -v
```

## Infrastructure Services

| Service           | Internal Port | External Port | Purpose                      |
|-------------------|---------------|---------------|------------------------------|
| PostgreSQL        | 5432          | 5432          | Database                     |
| MinIO             | 9000/9001     | 9000/9001     | S3-compatible storage        |
| MailHog T1        | 1025/8025     | 1025/8025     | SMTP/API Tenant 1            |
| MailHog T2        | 1026/8026     | 1026/8026     | SMTP/API Tenant 2            |
| Dovecot           | 10143         | 10143         | IMAP for bounce detection    |
| Client Mock T1    | 8081          | 8081          | Callback server Tenant 1     |
| Client Mock T2    | 8082          | 8082          | Callback server Tenant 2     |
| Attachment Server | 8083          | 8083          | File server for attachments  |
| Mail Proxy        | 8000          | 8000          | Application under test       |

## Test Markers

| Marker       | Description                              |
|--------------|------------------------------------------|
| `fullstack`  | All fullstack integration tests          |
| `asyncio`    | Async tests (auto-applied via conftest)  |
| `bounce_e2e` | Bounce detection end-to-end tests        |
| `retention`  | Data retention/cleanup tests             |
| `rate_limit` | Rate limiting tests                      |
| `docker`     | Docker-specific integration tests        |

## Configuration

Service URLs are defined in `helpers.py`:

```python
MAILPROXY_URL = "http://localhost:8000"
MAILPROXY_TOKEN = "test-api-token"

MAILHOG_TENANT1_API = "http://localhost:8025"
MAILHOG_TENANT2_API = "http://localhost:8026"

CLIENT_TENANT1_URL = "http://localhost:8081"
CLIENT_TENANT2_URL = "http://localhost:8082"

DOVECOT_IMAP_HOST = "localhost"
DOVECOT_IMAP_PORT = 10143
```

## Bounce Testing

Bounce detection tests require the Dovecot IMAP server:

```bash
# Start with bounce profile
docker compose -f tests/docker/docker-compose.fulltest.yml --profile bounce up -d

# Run bounce tests
pytest tests/fullstack/60_imap/ -v
```

If Dovecot is not available, bounce tests are automatically skipped.

## Troubleshooting

### Check Service Status

```bash
docker compose -f tests/docker/docker-compose.fulltest.yml ps
```

### View Service Logs

```bash
# Mail proxy logs
docker compose -f tests/docker/docker-compose.fulltest.yml logs mailproxy

# Dovecot logs
docker compose -f tests/docker/docker-compose.fulltest.yml logs dovecot

# MailHog logs
docker compose -f tests/docker/docker-compose.fulltest.yml logs mailhog-tenant1
```

### Check MailHog Messages

```bash
# Tenant 1 messages
curl http://localhost:8025/api/v2/messages | jq '.count'

# Tenant 2 messages
curl http://localhost:8026/api/v2/messages | jq '.count'

# Clear all messages
curl -X DELETE http://localhost:8025/api/v1/messages
```

### Common Issues

1. **Services not starting**: Check Docker resources (RAM, disk)
2. **Connection refused**: Wait for services to be ready
3. **Tests timeout**: Increase timeout or check service health
4. **MailHog crash**: Restart with `docker compose restart mailhog-tenant1`
5. **Rate limit tests flaky**: Ensure no pending dispatches from previous tests

## Writing New Tests

1. Place tests in the appropriate numbered group

2. Use `pytestmark` to set markers, **in the test module itself** — pytest
   reads `pytestmark` in modules and classes only, never in a `conftest.py`,
   so a package-wide marker declared there silently marks nothing:

   ```python
   pytestmark = [pytest.mark.fullstack, pytest.mark.asyncio]
   ```

3. Use fixtures from `conftest.py` (e.g., `api_client`, `setup_test_tenants`)

4. Use helpers from `helpers.py` for common operations

5. Add cleanup at the start of tests that depend on clean state:

   ```python
   await asyncio.sleep(2)  # Wait for pending dispatches
   await clear_mailhog(MAILHOG_TENANT1_API)
   ```

## Measured Behaviour of the Stack

Everything in this section was measured, not inferred. It is recorded because
each item cost real time to discover and none of it is visible by reading the
code.

### MailHog memory cannot be bounded from the test side

MailHog's resident memory tracks the cumulative bytes it has ever received,
not the messages it currently holds:

- `DELETE /api/v1/messages` frees nothing — 2.113 GiB resident before the
  call and 2.113 GiB after.
- `MH_MAXMESSAGES` does not exist. There is no message-count flag to bind to.
- `MH_STORAGE=maildir` is about three times worse, not better: 2.116 GiB
  against memory storage's 641.7 MiB on identical load, because every list
  call re-reads and re-decodes every file. It also needs `MH_MAILDIR_PATH`
  under a writable path (`/maildir` panics — the image runs non-root), and it
  returns messages in filesystem order, which is independent evidence that
  reading MailHog by index was always wrong.
- Clearing mailboxes periodically is marginally *worse* than never clearing:
  1.186 GiB when cleared every 20 messages, 1.003 GiB when never cleared.

The only bound is container lifetime plus a hard cap. One full run takes
tenant1 from 15.87 MiB to 1.561 GiB; three runs without a restart is about
4.7 GiB on a 7.653 GiB Docker VM, so the OOM kill is arithmetic, not a
mystery. Hence `mem_limit: 2g` on both MailHog services (28% over the
measured peak), `restart: unless-stopped` for containment, and a session
fixture that restarts the containers instead of emptying them — a restart
returns resident memory to about 15 MiB, so only one run's ingest must fit.

**Mailpit is the durable fix**: it has a real message cap where MailHog has
none. Adopting it edits every helper that speaks the MailHog API, so it was
kept out of the 0.7.6 line deliberately.

### `docker compose up -d` silently rebinds the stack to the default ports

Every published port in `docker-compose.fulltest.yml` reads a `GMP_TEST_*`
variable with the old literal as its default. So bringing the stack up
*without those variables exported* rebinds it to the defaults, while the test
process — which reads the same variables — still talks to the alternate ports.
The symptom is a wall of identical `ConnectError` failures, and it cost three
wasted full runs before anyone checked the bindings.

`docker compose restart` does **not** re-evaluate port bindings, which is why
the session fixture restarts rather than recreates.

### `RestartCount` counts only abnormal deaths

`docker compose restart` does not increment a container's `RestartCount` (it
stays at 0 after restarting both MailHogs), while the `restart:` policy does
increment it when a container dies abnormally. That is what makes
`RestartCount` usable as a health criterion: the session fixture's deliberate
restarts are invisible to it, so a non-zero count means a real death.

### Restart by service name, never by container name

The container names here (`docker-mailhog-tenant1-1`) are what they are only
because the compose project takes the name of its directory. Address services
by their compose service name.
