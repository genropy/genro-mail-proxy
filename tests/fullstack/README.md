# Fullstack Tests

Tests end-to-end SMTP delivery and S3 storage using Docker containers.

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

### Minio (S3-Compatible Storage)
- **S3 API**: localhost:9000
- **Console**: http://localhost:9001
- **Credentials**: minioadmin / minioadmin
- **Test Bucket**: test-attachments

### Proxy
- **API**: localhost:8000
- **Health**: http://localhost:8000/instance/health
- Connects to Mailpit for SMTP delivery
- Connects to Minio for large file storage

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

### Large File Storage Tests
Tests can verify S3 storage integration using Minio:
- Upload/download large attachments
- Signed URL generation
- Cleanup of expired files

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

### Minio not accessible
Check minio-setup completed:
```bash
docker compose logs minio-setup
# Should show "Bucket created successfully"
```

### Rebuild after code changes
```bash
docker compose build proxy
docker compose up -d
```

## Environment Variables

The proxy container accepts these variables (with defaults):

| Variable | Default | Description |
|----------|---------|-------------|
| `MAIL_PROXY_DB_PATH` | `/data/mail.db` | SQLite database path |
| `MAIL_PROXY_SMTP_HOST` | `mailpit` | SMTP server hostname |
| `MAIL_PROXY_SMTP_PORT` | `1025` | SMTP server port |
| `MAIL_PROXY_STORAGE_URL` | `s3://test-attachments` | S3 storage URL |
| `AWS_ENDPOINT_URL` | `http://minio:9000` | S3 endpoint (Minio) |
| `AWS_ACCESS_KEY_ID` | `minioadmin` | S3 access key |
| `AWS_SECRET_ACCESS_KEY` | `minioadmin` | S3 secret key |
