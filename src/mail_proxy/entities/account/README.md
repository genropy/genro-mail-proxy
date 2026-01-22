# Account Entity

SMTP account configurations for email delivery.

## Overview

An Account represents an SMTP server configuration used to send emails. Each account belongs to a tenant and can have its own rate limits.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| id | string | Unique account identifier |
| tenant_id | string | Parent tenant FK |
| host | string | SMTP server hostname |
| port | integer | SMTP server port |
| user | string | SMTP username (optional) |
| password | string | SMTP password (optional) |
| use_tls | boolean | Enable TLS/STARTTLS |
| ttl | integer | Connection TTL in seconds |
| batch_size | integer | Max messages per dispatch cycle |
| limit_per_minute | integer | Rate limit per minute |
| limit_per_hour | integer | Rate limit per hour |
| limit_per_day | integer | Rate limit per day |
| limit_behavior | string | "defer" or "reject" when limit hit |

## TLS Behavior

- Port 465: Implicit TLS (SSL wrapper)
- Port 587: STARTTLS after connection
- Other ports: STARTTLS if use_tls=true

## Rate Limiting

When rate limits are exceeded:
- `defer`: Message is deferred and retried later
- `reject`: Message is rejected with error
