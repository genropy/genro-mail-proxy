# Pressure Test Infrastructure

Realistic load testing setup for genro-mail-proxy with full protocol simulation.

## Quick Start

```bash
# Start all services
cd pressure-test
docker compose -f docker-compose.pressure.yml up -d

# Wait for setup to complete (creates tenants and accounts)
docker compose -f docker-compose.pressure.yml logs setup

# Open monitoring UIs
open http://localhost:3000   # Grafana - metrics (admin/admin)
open http://localhost:8025   # MailHog - SMTP sink

# Check client stats
curl http://localhost:8081/stats  # high-volume client
curl http://localhost:8082/stats  # standard client
```

## Architecture

This setup simulates the **complete mail proxy protocol**:

```
┌─────────────────────────────────────────────────────────────────┐
│              SIMULATED TENANT CLIENTS                           │
│  4 clients with different behaviors:                            │
│  - high-volume: 20 msg/sync, few attachments                   │
│  - standard: 5 msg/sync, mixed attachments                      │
│  - newsletter: 50 msg/sync, cached images                       │
│  - documents: 2 msg/sync, large attachments (10-50MB)          │
└──────────────────────────┬──────────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        │                  │                  │
        ▼                  ▼                  ▼
   POST /sync      POST /attachments    (messages returned)
   (reports)       (fetch large files)
        │                  │                  │
        └──────────────────┼──────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                      MAILPROXY                                  │
│  - Calls clients for sync (delivery reports)                    │
│  - Receives new messages in sync response                       │
│  - Fetches attachments via HTTP when needed                     │
│  - Caches attachments using MD5 markers                         │
│  - Sends emails via SMTP to MailHog                             │
└────────┬─────────────────────────────────────┬──────────────────┘
         │ SQL                                 │ SMTP
         ▼                                     ▼
┌─────────────────┐                   ┌─────────────────┐
│   PostgreSQL    │                   │    MailHog      │
│   (persistence) │                   │  (SMTP sink)    │
└─────────────────┘                   └─────────────────┘
```

## How It Works

1. **Proxy syncs with clients**: Every few seconds, mailproxy calls each tenant's `/sync` endpoint
2. **Clients return new messages**: The sync response includes delivery report acknowledgment + new messages to send
3. **Proxy fetches attachments**: When a message has `fetch_mode: "endpoint"`, proxy calls client's `/attachments`
4. **Cache testing**: Clients use `{MD5:hash}` markers in filenames to test proxy-side caching
5. **Emails sent**: Messages are dispatched to MailHog (SMTP sink)
6. **Reports delivered**: Next sync call delivers the results back to clients

## Simulated Client Profiles

| Client | Tenant ID | Msg/Sync | Attachments | Large Files | DND Rate |
|--------|-----------|----------|-------------|-------------|----------|
| high-volume | tenant-high-volume | 20 | 5% | 5% | 2% |
| standard | tenant-standard | 5 | 30% cached | 10% | 5% |
| newsletter | tenant-newsletter | 50 | 50% cached | 15% | 10% |
| documents | tenant-documents | 2 | 20% cached | 50% | 5% |

**Attachment sizes:**
- Small: 10 KB (40% of attachments)
- Medium: 500 KB (30%)
- Large: 2 MB (15%)
- XLarge: 10 MB (10%)
- Huge: 50 MB (5%)

## Monitoring

### Client Statistics

Each client exposes stats at `/stats`:

```bash
curl http://localhost:8081/stats | jq
```

Returns:
```json
{
  "client_id": "high-volume-1",
  "tenant_id": "tenant-high-volume",
  "uptime_seconds": 3600,
  "sync_calls": 1200,
  "reports_received": 24000,
  "messages_generated": 24000,
  "attachments_served": 1200,
  "attachment_mb_served": 450.5,
  "cache_hit_rate": 0.35,
  "dnd_periods": 24,
  "msg_per_sync": 20.0
}
```

### Grafana Dashboard

Open http://localhost:3000 (admin/admin) for:
- Send rate per account
- Pending messages queue depth
- Cumulative sent/errors
- Deferrals and rate limiting

### MailHog

Open http://localhost:8025 to:
- See all emails received
- Verify attachments are present
- Check email content

## Scaling

```bash
# Scale proxy instances (test horizontal scaling)
docker compose -f docker-compose.pressure.yml up -d --scale mailproxy=3

# Add more high-volume clients
docker compose -f docker-compose.pressure.yml up -d --scale client-high-volume=3
```

## Tuning Parameters

### Client Configuration

Environment variables for simulated clients:

| Variable | Default | Description |
|----------|---------|-------------|
| `MSG_RATE_PER_SYNC` | 5 | Average messages returned per sync call |
| `ATTACHMENT_CACHE_HIT_RATE` | 0.3 | Probability of reusing cached attachment |
| `DND_PROBABILITY` | 0.05 | Probability of returning `next_sync_after` |
| `LARGE_ATTACHMENT_RATE` | 0.1 | Probability of 10MB+ attachment |

### Proxy Configuration

In `docker-compose.pressure.yml`:

```yaml
mailproxy:
  environment:
    GMP_SEND_LOOP_INTERVAL: "1"        # Seconds between dispatch cycles
    GMP_BATCH_SIZE_PER_ACCOUNT: "50"   # Messages per SMTP batch
    GMP_POOL_SIZE: "20"                # SMTP connection pool size
    GMP_SYNC_INTERVAL: "5"             # Seconds between tenant syncs
```

## Warning Signs

| Symptom | Possible Cause | Action |
|---------|----------------|--------|
| Pending messages growing | SMTP/dispatch bottleneck | Increase batch size, check MailHog |
| High memory usage | Large attachment accumulation | Enable disk cache, reduce attachment sizes |
| Slow sync responses | Client overloaded | Scale clients, reduce MSG_RATE |
| Many DND periods | Clients simulating serverless | Reduce DND_PROBABILITY |
| Low cache hit rate | Not enough repeated attachments | Increase ATTACHMENT_CACHE_HIT_RATE |

## Cleanup

```bash
# Stop and remove everything (including data)
docker compose -f docker-compose.pressure.yml down -v

# Keep data for later analysis
docker compose -f docker-compose.pressure.yml down
```

## Kubernetes Migration

| Docker Service | K8s Resource |
|----------------|--------------|
| mailproxy | Deployment + Service + HPA |
| db | StatefulSet + PVC + Service |
| mailhog | Deployment + Service |
| prometheus | Deployment + ConfigMap + PVC |
| grafana | Deployment + ConfigMap + PVC |
| client-* | Deployment + Service (per tenant) |
| setup | Job (runs once) |
