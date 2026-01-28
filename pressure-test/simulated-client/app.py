"""
Simulated tenant client for pressure testing.

This simulates a real tenant application that:
1. Receives delivery reports from the proxy via POST /sync
2. Responds with new messages to send (simulating an outbox)
3. Serves attachments via POST /attachments with cache support
4. Tracks statistics for analysis

The client simulates realistic behavior:
- Messages are generated based on configurable patterns
- Attachments use MD5 markers for cache testing
- Rate of new messages varies to simulate real workloads
- Can simulate "Do Not Disturb" periods

Environment variables:
    CLIENT_ID: Unique identifier for this client (default: client-1)
    TENANT_ID: Tenant ID this client represents (default: tenant-1)
    MSG_RATE_PER_SYNC: Average new messages per sync call (default: 5)
    ATTACHMENT_CACHE_HIT_RATE: Probability of reusing cached attachment (default: 0.3)
    DND_PROBABILITY: Probability of returning next_sync_after (default: 0.05)
    LARGE_ATTACHMENT_RATE: Probability of large (10MB+) attachment (default: 0.1)
"""

import hashlib
import json
import os
import random
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
import uvicorn

# Configuration
CLIENT_ID = os.environ.get("CLIENT_ID", "client-1")
TENANT_ID = os.environ.get("TENANT_ID", "tenant-1")
MSG_RATE_PER_SYNC = int(os.environ.get("MSG_RATE_PER_SYNC", "5"))
ATTACHMENT_CACHE_HIT_RATE = float(os.environ.get("ATTACHMENT_CACHE_HIT_RATE", "0.3"))
DND_PROBABILITY = float(os.environ.get("DND_PROBABILITY", "0.05"))
LARGE_ATTACHMENT_RATE = float(os.environ.get("LARGE_ATTACHMENT_RATE", "0.1"))

# Attachment size distribution
ATTACHMENT_SIZES = {
    "none": (0, 0.4),           # 40% no attachment
    "small": (10 * 1024, 0.3),  # 30% 10KB
    "medium": (500 * 1024, 0.15),  # 15% 500KB
    "large": (2 * 1024 * 1024, 0.1),  # 10% 2MB
    "xlarge": (10 * 1024 * 1024, 0.04),  # 4% 10MB
    "huge": (50 * 1024 * 1024, 0.01),  # 1% 50MB
}

# Pre-generated attachment content for cache testing
# Maps MD5 hash -> content
CACHED_ATTACHMENTS: dict[str, bytes] = {}
MAX_CACHE_SIZE = 100 * 1024 * 1024  # 100MB cache limit


@dataclass
class ClientStats:
    """Statistics for this client."""
    sync_calls: int = 0
    reports_received: int = 0
    messages_generated: int = 0
    attachments_served: int = 0
    attachment_bytes_served: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    dnd_periods: int = 0
    errors: int = 0
    start_time: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        uptime = time.time() - self.start_time
        return {
            "client_id": CLIENT_ID,
            "tenant_id": TENANT_ID,
            "uptime_seconds": round(uptime, 1),
            "sync_calls": self.sync_calls,
            "reports_received": self.reports_received,
            "messages_generated": self.messages_generated,
            "attachments_served": self.attachments_served,
            "attachment_mb_served": round(self.attachment_bytes_served / (1024 * 1024), 2),
            "cache_hit_rate": round(self.cache_hits / max(1, self.cache_hits + self.cache_misses), 3),
            "dnd_periods": self.dnd_periods,
            "errors": self.errors,
            "msg_per_sync": round(self.messages_generated / max(1, self.sync_calls), 2),
        }


stats = ClientStats()
app = FastAPI(title=f"Simulated Client - {CLIENT_ID}")


def generate_attachment_content(size: int, reuse_cached: bool = False) -> tuple[bytes, str]:
    """Generate attachment content, optionally reusing cached content.

    Returns (content, md5_hash).
    """
    global CACHED_ATTACHMENTS

    if reuse_cached and CACHED_ATTACHMENTS and random.random() < ATTACHMENT_CACHE_HIT_RATE:
        # Reuse a cached attachment (simulates repeated documents)
        md5_hash = random.choice(list(CACHED_ATTACHMENTS.keys()))
        stats.cache_hits += 1
        return CACHED_ATTACHMENTS[md5_hash], md5_hash

    # Generate new content
    content = os.urandom(size)
    md5_hash = hashlib.md5(content).hexdigest()

    # Cache it if not too large
    total_cached = sum(len(v) for v in CACHED_ATTACHMENTS.values())
    if total_cached + size < MAX_CACHE_SIZE:
        CACHED_ATTACHMENTS[md5_hash] = content

    stats.cache_misses += 1
    return content, md5_hash


def choose_attachment_size() -> int:
    """Choose attachment size based on distribution."""
    r = random.random()
    cumulative = 0.0
    for size_name, (size_bytes, probability) in ATTACHMENT_SIZES.items():
        cumulative += probability
        if r < cumulative:
            # Apply large attachment rate override
            if size_name in ("xlarge", "huge") and random.random() > LARGE_ATTACHMENT_RATE:
                return ATTACHMENT_SIZES["medium"][0]
            return size_bytes
    return 0  # No attachment


def generate_message() -> dict[str, Any]:
    """Generate a realistic message with optional attachment."""
    msg_id = f"{TENANT_ID}-{uuid.uuid4().hex[:12]}"
    recipient = f"user-{random.randint(1, 10000)}@example.com"

    message = {
        "id": msg_id,
        "tenant_id": TENANT_ID,
        "sender": f"noreply@{TENANT_ID}.example.com",
        "recipient_email": recipient,
        "subject": f"Message {msg_id[:8]} - {datetime.now().strftime('%H:%M:%S')}",
        "body_text": f"This is an automated message from {TENANT_ID}.\n" * 5,
        "body_html": f"<html><body><p>This is an automated message from {TENANT_ID}.</p></body></html>",
        "priority": random.choices([0, 1, 2, 3], weights=[5, 15, 50, 30])[0],
    }

    # Add attachment?
    attachment_size = choose_attachment_size()
    if attachment_size > 0:
        # Decide if this should use cache (MD5 marker)
        use_cache = random.random() < ATTACHMENT_CACHE_HIT_RATE
        _, md5_hash = generate_attachment_content(attachment_size, reuse_cached=use_cache)

        # Use endpoint fetch mode - proxy will call us back
        # MD5 marker in filename enables proxy-side caching
        filename = f"document_{{MD5:{md5_hash}}}.bin" if use_cache else f"document_{msg_id[:8]}.bin"
        message["attachments"] = [{
            "filename": filename,
            "storage_path": f"hash={md5_hash}&size={attachment_size}",
            "content_type": "application/octet-stream",
            "fetch_mode": "endpoint",
        }]

    return message


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"ok": True, "client_id": CLIENT_ID, "tenant_id": TENANT_ID}


@app.get("/stats")
async def get_stats():
    """Return client statistics."""
    return stats.to_dict()


@app.post("/sync")
async def sync(request: Request):
    """
    Receive delivery reports from proxy and return new messages.

    This is the main sync endpoint that the proxy calls periodically.
    We process the reports and return any new messages to send.
    """
    stats.sync_calls += 1

    try:
        body = await request.json()
    except Exception as e:
        stats.errors += 1
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

    # Process delivery reports
    reports = body.get("reports", [])
    stats.reports_received += len(reports)

    # Log some reports for debugging
    for report in reports[:3]:  # Log first 3
        status = "sent" if report.get("sent_ts") else "error" if report.get("error_ts") else "unknown"
        print(f"[{CLIENT_ID}] Report: {report.get('id', 'N/A')} -> {status}")

    # Generate new messages to send
    # Use Poisson-like distribution around MSG_RATE_PER_SYNC
    num_messages = max(0, int(random.gauss(MSG_RATE_PER_SYNC, MSG_RATE_PER_SYNC * 0.5)))
    new_messages = [generate_message() for _ in range(num_messages)]
    stats.messages_generated += len(new_messages)

    response = {
        "ok": True,
        "processed": len(reports),
    }

    # Return new messages if any
    if new_messages:
        response["messages"] = new_messages

    # Simulate "Do Not Disturb" occasionally
    if random.random() < DND_PROBABILITY:
        # Don't call me for 30-300 seconds
        dnd_seconds = random.randint(30, 300)
        response["next_sync_after"] = time.time() + dnd_seconds
        stats.dnd_periods += 1
        print(f"[{CLIENT_ID}] Entering DND for {dnd_seconds}s")

    return response


@app.post("/attachments")
async def fetch_attachment(request: Request):
    """
    Serve attachment content to the proxy.

    The proxy calls this when it needs to fetch an attachment
    with fetch_mode="endpoint". We return the file content.

    Query params in storage_path:
    - hash: MD5 hash of content (for cache testing)
    - size: Size in bytes
    """
    stats.attachments_served += 1

    try:
        body = await request.json()
    except Exception:
        # Try form data or query params
        body = {}

    storage_path = body.get("storage_path", "")

    # Parse storage_path
    params = {}
    for part in storage_path.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k] = v

    md5_hash = params.get("hash")
    size = int(params.get("size", "10240"))

    # Check if we have this in cache
    if md5_hash and md5_hash in CACHED_ATTACHMENTS:
        content = CACHED_ATTACHMENTS[md5_hash]
        stats.cache_hits += 1
    else:
        # Generate new content
        content = os.urandom(size)
        if md5_hash:
            # Store for future requests
            total_cached = sum(len(v) for v in CACHED_ATTACHMENTS.values())
            if total_cached + size < MAX_CACHE_SIZE:
                CACHED_ATTACHMENTS[md5_hash] = content
        stats.cache_misses += 1

    stats.attachment_bytes_served += len(content)

    return Response(
        content=content,
        media_type="application/octet-stream",
        headers={
            "Content-Length": str(len(content)),
            "X-Content-MD5": hashlib.md5(content).hexdigest(),
        }
    )


@app.post("/reset-stats")
async def reset_stats():
    """Reset statistics."""
    global stats
    stats = ClientStats()
    return {"ok": True, "message": "Stats reset"}


@app.get("/cache-info")
async def cache_info():
    """Return cache statistics."""
    return {
        "cached_items": len(CACHED_ATTACHMENTS),
        "cached_bytes": sum(len(v) for v in CACHED_ATTACHMENTS.values()),
        "cached_mb": round(sum(len(v) for v in CACHED_ATTACHMENTS.values()) / (1024 * 1024), 2),
        "max_cache_mb": MAX_CACHE_SIZE / (1024 * 1024),
    }


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"Starting simulated client {CLIENT_ID} for tenant {TENANT_ID}")
    print(f"  MSG_RATE_PER_SYNC: {MSG_RATE_PER_SYNC}")
    print(f"  ATTACHMENT_CACHE_HIT_RATE: {ATTACHMENT_CACHE_HIT_RATE}")
    print(f"  DND_PROBABILITY: {DND_PROBABILITY}")
    print(f"  LARGE_ATTACHMENT_RATE: {LARGE_ATTACHMENT_RATE}")
    uvicorn.run(app, host="0.0.0.0", port=port)
