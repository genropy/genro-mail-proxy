# Send Log Entity

Delivery event tracking for rate limiting enforcement.

## Overview

The send log records each successful email delivery with a timestamp. This data is used to calculate how many emails have been sent within specific time windows for rate limiting.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| account_id | string | Account that sent the email |
| timestamp | integer | Unix timestamp of delivery |

## Usage

Rate limiting checks query this table:
- `count_since(account_id, timestamp)`: Count deliveries after timestamp
- For per-minute: `count_since(id, now - 60)`
- For per-hour: `count_since(id, now - 3600)`
- For per-day: `count_since(id, now - 86400)`

## Maintenance

Old entries should be periodically purged. Entries older than the longest rate limit window (typically 24 hours) can be safely deleted.
