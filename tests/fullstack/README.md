# Fullstack Integration Tests

End-to-end tests that validate genro-mail-proxy against real services.

## Prerequisites

- Docker and Docker Compose
- Python 3.10+ with pytest
- At least 8GB RAM

## Quick Start

```bash
# Start infrastructure
docker compose -f tests/docker/docker-compose.fulltest.yml up -d --build

# Wait for services
docker compose -f tests/docker/docker-compose.fulltest.yml ps

# Run all fullstack tests
pytest tests/fullstack/ -v

# Stop infrastructure
docker compose -f tests/docker/docker-compose.fulltest.yml down
```

## Test Categories

Run specific test categories using pytest markers:

```bash
# Bounce detection tests
pytest tests/fullstack/ -m bounce_e2e -v

# Retention/cleanup tests
pytest tests/fullstack/ -m retention -v

# Rate limiting tests
pytest tests/fullstack/ -m rate_limit -v

# Exclude chaos tests (for CI)
pytest tests/fullstack/ -m "not chaos" -v
```

## Infrastructure Services

| Service | Port | Purpose |
|---------|------|---------|
| PostgreSQL | 5432 | Database |
| MinIO | 9000/9001 | S3 storage |
| MailHog T1 | 1025/8025 | SMTP/API Tenant 1 |
| MailHog T2 | 1026/8026 | SMTP/API Tenant 2 |
| Dovecot | 10143 | IMAP for bounce |
| Mail Proxy | 8000 | Application |

## Test Structure

```
tests/fullstack/
├── conftest.py          # Shared fixtures
├── helpers.py           # Helper functions
├── test_health.py       # Health endpoint tests
├── test_dispatch.py     # Message dispatch tests
├── test_bounce.py       # Bounce detection tests
├── test_bounce_live.py  # Live bounce polling tests
├── ...
```

## Troubleshooting

Check service logs:

```bash
docker compose -f tests/docker/docker-compose.fulltest.yml logs mailproxy
docker compose -f tests/docker/docker-compose.fulltest.yml logs dovecot
```
