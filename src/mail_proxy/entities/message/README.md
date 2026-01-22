# Message Entity

Email queue entries with delivery status tracking.

## Overview

A Message represents an email in the delivery queue. It tracks the full lifecycle from submission through delivery or error.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| id | string | Unique message identifier |
| account_id | string | SMTP account FK |
| priority | integer | 0=immediate, 1=high, 2=normal, 3=low |
| payload | JSON | Full message data (from, to, subject, body, etc.) |
| deferred_ts | integer | Unix timestamp for deferred delivery |
| sent_ts | integer | Timestamp when successfully sent |
| error_ts | integer | Timestamp when error occurred |
| error | string | Error message if failed |
| reported_ts | integer | Timestamp when reported to client |

## Status Flow

```
PENDING → SENT → (reported)
    ↓
DEFERRED → PENDING (retry)
    ↓
  ERROR → (reported)
```

## Priority Levels

- `0`: Immediate - processed first, bypasses batching
- `1`: High - processed before normal priority
- `2`: Normal - default priority
- `3`: Low - processed last

## Attachments

Messages can include attachments with different fetch modes:
- `endpoint`: Fetch from tenant's configured HTTP endpoint
- `http_url`: Fetch from arbitrary HTTP URL
- `base64`: Inline base64-encoded content
