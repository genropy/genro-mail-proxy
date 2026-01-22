# Instance Config Entity

Key-value configuration storage for instance-specific settings.

## Overview

A simple key-value store for runtime configuration that persists across restarts.

## Fields

| Field | Type | Description |
|-------|------|-------------|
| key | string | Configuration key (primary key) |
| value | string | Configuration value |
| updated_at | timestamp | Last update time |

## Common Keys

| Key | Description |
|-----|-------------|
| `last_sync_ts` | Last delivery report sync timestamp |
| `last_cleanup_ts` | Last database cleanup timestamp |
| `instance_id` | Unique instance identifier |

## Usage

```python
# Get a value
value = await config.get("last_sync_ts", default="0")

# Set a value
await config.set("last_sync_ts", str(time.time()))

# Get all config
all_config = await config.get_all()
```
