# Fullstack Tests

Tests end-to-end SMTP delivery using Docker containers.

## Requirements

- Docker and Docker Compose
- Python 3.10+
- `httpx` (included in dev dependencies)

## Quick Start

```bash
# Start Docker services
cd tests/fullstack
docker compose up -d

# Wait for services to be healthy
docker compose ps

# Run fullstack tests
pytest tests/fullstack/ -v

# Stop services when done
docker compose down
```

## Services

### Mailpit
- **SMTP**: localhost:1025 (accepts any auth)
- **IMAP**: localhost:1143 (for bounce injection)
- **Web UI**: http://localhost:8025

### Proxy
- **API**: localhost:8000
- **Health**: http://localhost:8000/instance/health

## Test Scenarios

### CSV-Driven Tests
Tests can load message scenarios from CSV files in `fixtures/`.

CSV columns:
- `id`: Message identifier
- `from`: Sender address
- `to`: Recipient address
- `subject`: Email subject
- `body`: Email body
- `expected_status`: Expected final status (`sent`, `bounced`, `error`)
- `simulate_bounce`: Bounce type to inject (`hard`, `soft`, or empty)

### Bounce Injection
The IMAP injector (`imap_injector.py`) can simulate bounces by injecting
RFC 3464 DSN messages directly into the Mailpit IMAP mailbox.

## Troubleshooting

### Tests skipped
If tests are skipped with "Docker services not available":
```bash
docker compose up -d
docker compose logs  # Check for errors
```

### Proxy not starting
Check proxy logs:
```bash
docker compose logs proxy
```

### Rebuild after code changes
```bash
docker compose build proxy
docker compose up -d
```
