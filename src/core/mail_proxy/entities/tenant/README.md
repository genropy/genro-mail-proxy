# Tenant Entity

Multi-tenant configuration for isolated email service instances.

## Overview

A Tenant represents a client organization with its own accounts, rate limits, and callback configuration.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| id | string | Unique tenant identifier |
| name | string | Human-readable name |
| client_auth | TenantAuth | Authentication for HTTP callbacks |
| client_base_url | string | Base URL for HTTP endpoints |
| client_sync_path | string | Delivery report callback path |
| client_attachment_path | string | Attachment fetcher path |
| rate_limits | TenantRateLimits | Per-tenant rate limits |
| large_file_config | TenantLargeFileConfig | Large attachment handling |
| active | boolean | Enable/disable tenant |

## Authentication

Supports three methods for HTTP callbacks:
- `none`: No authentication
- `bearer`: Authorization: Bearer <token>
- `basic`: HTTP Basic (user:password)

## Large File Handling

When `large_file_config.enabled=true`, attachments exceeding `max_size_mb`:
- `warn`: Log warning, send normally
- `reject`: Fail the message
- `rewrite`: Upload to storage, replace with download link
