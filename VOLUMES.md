# Volume Management Guide

## Overview

genro-mail-proxy uses **volumes** to manage attachment storage across multiple backends. Volumes provide a unified interface for accessing files from different storage systems (S3, WebDAV, HTTP, local filesystem, etc.) through [genro-storage](https://github.com/genropy/genro-storage).

### Key Concepts

- **Volume**: A named storage configuration with a specific backend type
- **Backend**: The storage system type (s3, webdav, http, local, etc.)
- **Storage Path**: Format `volume:subpath` used in attachment references
- **Account ID**: Optional tenant isolation (NULL for shared volumes)

## Volume Structure

Each volume has:

- **name**: Unique identifier (e.g., `s3-uploads`, `cdn`)
- **backend**: Storage type (e.g., `s3`, `webdav`, `http`)
- **config**: Backend-specific configuration (JSON object)
- **account_id**: Optional tenant ID (NULL for shared volumes)

## Configuration Methods

### 1. config.ini (Recommended for Bootstrap)

Configure volumes at service startup by adding a `[volumes]` section:

```ini
[volumes]
# Format: volume.{name}.{field} = {value}

# Shared S3 volume (accessible by all tenants)
volume.s3-uploads.backend = s3
volume.s3-uploads.config = {"bucket": "common-uploads", "region": "us-east-1"}

# Public CDN (HTTP read-only)
volume.cdn.backend = http
volume.cdn.config = {"base_url": "https://cdn.example.com"}

# Tenant-specific S3 volume
volume.tenant1-storage.backend = s3
volume.tenant1-storage.config = {"bucket": "tenant1-files", "region": "eu-west-1"}
volume.tenant1-storage.account_id = tenant1

# WebDAV (Nextcloud)
volume.nextcloud.backend = webdav
volume.nextcloud.config = {"base_url": "https://cloud.example.com/remote.php/dav", "username": "user", "password": "secret"}
volume.nextcloud.account_id = tenant2

# Local filesystem
volume.local-tmp.backend = local
volume.local-tmp.config = {"path": "/tmp/attachments"}
```

**Notes:**
- Volumes are loaded on service startup
- Existing volumes in database are NOT replaced
- Empty or omitted `account_id` creates a shared volume

### 2. REST API (Runtime Management)

Manage volumes dynamically at runtime:

#### Create/Update Volume

```bash
POST /volume
X-API-Token: your-token
Content-Type: application/json

{
  "name": "new-volume",
  "backend": "s3",
  "config": {
    "bucket": "my-bucket",
    "region": "us-east-1"
  },
  "account_id": null  # or "tenant1" for tenant-specific
}
```

#### List Volumes

```bash
GET /volumes
X-API-Token: your-token

# Optional: filter by account
GET /volumes?account_id=tenant1
```

Response:
```json
{
  "ok": true,
  "volumes": [
    {
      "id": 1,
      "name": "s3-uploads",
      "backend": "s3",
      "config": {"bucket": "my-bucket"},
      "account_id": null,
      "created_at": "2024-01-01T00:00:00Z",
      "updated_at": "2024-01-01T00:00:00Z"
    }
  ]
}
```

#### Get Specific Volume

```bash
GET /volume/{name}
X-API-Token: your-token
```

#### Delete Volume

```bash
DELETE /volume/{name}
X-API-Token: your-token
```

## Backend-Specific Configuration

### S3 (Amazon S3 and Compatible)

```ini
volume.s3-example.backend = s3
volume.s3-example.config = {
  "bucket": "my-bucket",
  "region": "us-east-1",
  "access_key": "AKIAIOSFODNN7EXAMPLE",
  "secret_key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
  "endpoint_url": "https://s3.amazonaws.com"
}
```

**Configuration fields:**
- `bucket` (required): S3 bucket name
- `region`: AWS region (default: us-east-1)
- `access_key`: AWS access key ID
- `secret_key`: AWS secret access key
- `endpoint_url`: Custom S3-compatible endpoint (e.g., MinIO)

**Usage example:**
```
s3-example:documents/report.pdf
s3-example:images/photo.jpg
```

### HTTP/HTTPS (CDNs and Web Servers)

```ini
volume.cdn.backend = http
volume.cdn.config = {
  "base_url": "https://cdn.example.com",
  "timeout": 30
}
```

**Configuration fields:**
- `base_url` (required): Base URL for file access
- `timeout`: Request timeout in seconds (default: 30)

**Usage example:**
```
cdn:images/logo.png
cdn:assets/stylesheet.css
```

**Note:** HTTP volumes are read-only.

### WebDAV (Nextcloud, ownCloud, SharePoint)

```ini
volume.nextcloud.backend = webdav
volume.nextcloud.config = {
  "base_url": "https://cloud.example.com/remote.php/dav",
  "username": "user@example.com",
  "password": "secret-password",
  "timeout": 30
}
```

**Configuration fields:**
- `base_url` (required): WebDAV endpoint URL
- `username`: WebDAV username
- `password`: WebDAV password
- `timeout`: Request timeout in seconds (default: 30)

**Usage example:**
```
nextcloud:Documents/contract.pdf
nextcloud:Photos/vacation/IMG001.jpg
```

### Local Filesystem

```ini
volume.local-storage.backend = local
volume.local-storage.config = {
  "path": "/var/attachments"
}
```

**Configuration fields:**
- `path` (required): Absolute path to storage directory

**Usage example:**
```
local-storage:uploads/document.pdf
local-storage:reports/2024/annual.pdf
```

**Security:** Ensure proper file permissions and access controls.

### base64 (Special Volume)

The `base64` volume is always available without configuration:

```
base64:SGVsbG8gV29ybGQh
base64:data...
```

Use for small inline attachments. No size limit enforcement, but large base64 data impacts message size.

## Multi-Tenancy and Security

### Shared Volumes

Volumes with `account_id = NULL` are accessible by all tenants:

```ini
volume.public-cdn.backend = http
volume.public-cdn.config = {"base_url": "https://cdn.example.com"}
# No account_id = shared
```

Any message can use `public-cdn:file.jpg`.

### Tenant-Specific Volumes

Volumes with `account_id` set are isolated to that tenant:

```ini
volume.tenant1-files.backend = s3
volume.tenant1-files.config = {"bucket": "tenant1-private"}
volume.tenant1-files.account_id = tenant1
```

Only messages with `account_id = "tenant1"` can use `tenant1-files:*` paths.

### Volume Validation

Messages are validated at submission time:

1. Extract volume names from all attachment `storage_path` fields
2. Check each volume exists and is accessible by the message's `account_id`
3. Reject message if any volume is invalid/unauthorized

**Example rejection:**
```json
{
  "ok": false,
  "queued": 0,
  "rejected": [
    {
      "id": "msg123",
      "reason": "Invalid/unauthorized storage volumes: tenant2-files"
    }
  ]
}
```

## Usage in Messages

### Message Format with Attachments

```json
{
  "id": "msg123",
  "from": "sender@example.com",
  "to": "recipient@example.com",
  "subject": "Document attached",
  "body": "Please find the document attached.",
  "account_id": "tenant1",
  "attachments": [
    {
      "filename": "report.pdf",
      "storage_path": "s3-uploads:documents/2024/report.pdf"
    },
    {
      "filename": "logo.png",
      "storage_path": "cdn:images/logo.png"
    }
  ]
}
```

### Inline base64 Attachments

```json
{
  "attachments": [
    {
      "filename": "small.txt",
      "storage_path": "base64:SGVsbG8gV29ybGQh"
    }
  ]
}
```

## Best Practices

### 1. Use Shared Volumes for Common Assets

```ini
# Good: Shared CDN for logos, templates
volume.cdn.backend = http
volume.cdn.config = {"base_url": "https://cdn.example.com"}
```

### 2. Tenant Isolation for Private Data

```ini
# Good: Separate S3 buckets per tenant
volume.tenant1-private.backend = s3
volume.tenant1-private.config = {"bucket": "tenant1-secure"}
volume.tenant1-private.account_id = tenant1
```

### 3. Bootstrap from config.ini, Manage via API

- **config.ini**: Define default volumes at startup
- **REST API**: Add/remove volumes dynamically for new tenants

### 4. Use Descriptive Volume Names

```
✅ Good: tenant1-invoices, public-assets, cdn-images
❌ Bad: vol1, temp, storage
```

### 5. Prefer Object Storage (S3) for Scalability

- Scales infinitely
- Built-in redundancy
- Geographic distribution
- Cost-effective

### 6. Monitor Volume Usage

Check volume access in logs and metrics to detect:
- Unauthorized access attempts
- Unused volumes (candidates for cleanup)
- Hot volumes (may need caching/CDN)

## Troubleshooting

### Volume Not Found

**Error:** `Invalid/unauthorized storage volumes: my-volume`

**Solutions:**
1. Check volume exists: `GET /volumes`
2. Verify account_id matches message
3. Ensure volume created before message submission

### Authentication Failures

**S3 Errors:** `403 Forbidden`
- Verify access_key and secret_key
- Check bucket permissions (IAM policy)
- Ensure region matches bucket location

**WebDAV Errors:** `401 Unauthorized`
- Verify username/password
- Check WebDAV endpoint URL
- Test authentication with WebDAV client

### Path Resolution Issues

**Error:** File not found in storage

**Solutions:**
1. Use forward slashes: `volume:path/to/file.pdf`
2. No leading slash in subpath: `s3:file.pdf` not `s3:/file.pdf`
3. Check file actually exists in backend

### base64 Special Volume

The `base64` volume requires special handling:
- Always available (no configuration needed)
- Content is the base64-encoded data itself
- No actual file fetching occurs

## Performance Considerations

### AsyncStorageManager

genro-mail-proxy uses `AsyncStorageManager` from genro-storage:

- **Non-blocking I/O**: File operations don't block the event loop
- **Concurrent fetches**: Multiple attachments fetched in parallel
- **Thread pool**: Sync I/O operations run in background threads

### Caching Strategies

For frequently accessed files:

1. **HTTP volumes with CDN**: Let CDN handle caching
2. **Local volume caching**: Copy frequently used files to local volume
3. **Application-level caching**: Cache fetched attachments in memory (future)

### Large Attachments

Consider:
- Pre-generating attachment URLs (signed S3 URLs)
- Using HTTP volumes to offload bandwidth to CDN
- Implementing size limits in validation

## Security Considerations

### Credentials Management

**Never commit credentials to version control:**

```ini
# ❌ Bad: credentials in config.ini committed to git
volume.s3.config = {"access_key": "AKIAIOSFODNN...", "secret_key": "wJalr..."}

# ✅ Good: use environment variables
volume.s3.config = {"access_key": "${S3_ACCESS_KEY}", "secret_key": "${S3_SECRET_KEY}"}
```

### Access Control

1. **Tenant Isolation**: Always use `account_id` for tenant-specific volumes
2. **Least Privilege**: Grant minimal S3/WebDAV permissions
3. **Read-Only Where Possible**: Use HTTP volumes for public assets

### Audit Logging

Monitor volume access:
- Failed validation attempts
- Volume creation/deletion
- Configuration changes

## Migration Guide

### From Inline base64 to Volumes

**Before:**
```json
{
  "attachments": [
    {"filename": "doc.pdf", "storage_path": "base64:JVBERi0x..."}
  ]
}
```

**After:**
```json
{
  "attachments": [
    {"filename": "doc.pdf", "storage_path": "s3-uploads:documents/doc.pdf"}
  ]
}
```

**Steps:**
1. Upload file to S3
2. Configure S3 volume
3. Reference via `s3-volume:path`

### Adding New Backend Support

genro-storage supports custom backends. See [genro-storage documentation](https://github.com/genropy/genro-storage) for implementing custom storage adapters.

## API Reference

### Volume Object

```typescript
{
  id: number;           // Auto-generated
  name: string;         // Unique volume name
  backend: string;      // Backend type (s3, webdav, http, local)
  config: object;       // Backend-specific configuration
  account_id: string?;  // Tenant ID (null for shared)
  created_at: string;   // ISO timestamp
  updated_at: string;   // ISO timestamp
}
```

### Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/volume` | Create/update volume |
| GET | `/volumes` | List all volumes |
| GET | `/volumes?account_id=X` | List volumes for tenant |
| GET | `/volume/{name}` | Get specific volume |
| DELETE | `/volume/{name}` | Delete volume |

All endpoints require `X-API-Token` header.

## Examples

### Complete S3 Setup

```ini
[volumes]
# Production uploads
volume.prod-uploads.backend = s3
volume.prod-uploads.config = {"bucket": "prod-attachments", "region": "us-east-1"}

# Staging uploads
volume.staging-uploads.backend = s3
volume.staging-uploads.config = {"bucket": "staging-attachments", "region": "us-east-1"}

# Per-tenant storage
volume.enterprise-client.backend = s3
volume.enterprise-client.config = {"bucket": "enterprise-secure"}
volume.enterprise-client.account_id = enterprise_001
```

### Multi-Backend Setup

```ini
[volumes]
# S3 for scalable storage
volume.s3-main.backend = s3
volume.s3-main.config = {"bucket": "main-files"}

# CDN for public assets
volume.cdn.backend = http
volume.cdn.config = {"base_url": "https://cdn.example.com"}

# WebDAV for internal shares
volume.intranet.backend = webdav
volume.intranet.config = {"base_url": "https://intranet.company.com/dav", "username": "mailbot"}

# Local for temporary files
volume.temp.backend = local
volume.temp.config = {"path": "/tmp/mail-attachments"}
```

### Dynamic Tenant Onboarding

```python
import httpx

async def onboard_tenant(tenant_id: str, bucket_name: str):
    """Create tenant-specific S3 volume via API."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://mail-proxy:8000/volume",
            headers={"X-API-Token": "secret"},
            json={
                "name": f"tenant-{tenant_id}",
                "backend": "s3",
                "config": {
                    "bucket": bucket_name,
                    "region": "us-east-1"
                },
                "account_id": tenant_id
            }
        )
        return response.json()
```

## Further Reading

- [genro-storage Documentation](https://github.com/genropy/genro-storage)
- [Architecture Overview](docs/architecture_overview.rst)
- [API Documentation](docs/api.rst)
