"""
Locust load test for genro-mail-proxy.

Simulates multiple tenants sending emails with various attachment sizes.

Usage:
    # Via docker compose (recommended)
    docker compose -f docker-compose.pressure.yml up -d
    # Open http://localhost:8089

    # Or standalone
    locust -f locustfile.py --host http://localhost:8000

Configuration via environment:
    MAILPROXY_TOKEN: API token (default: pressure-test-token)
    MAILHOG_HOST: SMTP host for account config (default: mailhog)
    MAILHOG_PORT: SMTP port (default: 1025)
"""

import os
import random
import string
import time
import uuid
from typing import Any

from locust import HttpUser, between, events, task


# Configuration
API_TOKEN = os.environ.get("MAILPROXY_TOKEN", "pressure-test-token")
SMTP_HOST = os.environ.get("MAILHOG_HOST", "mailhog")
SMTP_PORT = int(os.environ.get("MAILHOG_PORT", "1025"))
ATTACHMENT_SERVER = os.environ.get("ATTACHMENT_SERVER", "http://attachment-server:8080")

# Attachment sizes (bytes) - for base64 inline attachments
ATTACHMENT_SIZES = {
    "none": 0,
    "small": 10 * 1024,        # 10 KB
    "medium": 500 * 1024,      # 500 KB
    "large": 2 * 1024 * 1024,  # 2 MB
}

# Attachment sizes for HTTP endpoint fetch (can be larger)
ENDPOINT_ATTACHMENT_SIZES = {
    "small": "10240",           # 10 KB
    "medium": "524288",         # 500 KB
    "large": "2097152",         # 2 MB
    "xlarge": "10485760",       # 10 MB
    "huge": "52428800",         # 50 MB
}

# Tenant profiles with different behaviors
# fetch_mode: "base64" = inline, "endpoint" = HTTP fetch from attachment-server
TENANT_PROFILES = [
    {"name": "high-volume", "weight": 40, "batch_size": 20, "attachment": "none", "fetch_mode": "base64"},
    {"name": "standard", "weight": 25, "batch_size": 5, "attachment": "small", "fetch_mode": "base64"},
    {"name": "newsletter", "weight": 15, "batch_size": 50, "attachment": "medium", "fetch_mode": "base64"},
    {"name": "reports", "weight": 10, "batch_size": 2, "attachment": "large", "fetch_mode": "endpoint"},
    {"name": "large-docs", "weight": 7, "batch_size": 1, "attachment": "xlarge", "fetch_mode": "endpoint"},
    {"name": "huge-files", "weight": 3, "batch_size": 1, "attachment": "huge", "fetch_mode": "endpoint"},
]


def generate_base64_attachment(size: int) -> dict[str, Any] | None:
    """Generate base64-encoded inline attachment."""
    if size == 0:
        return None

    import base64
    content = os.urandom(size)
    return {
        "filename": f"attachment_{uuid.uuid4().hex[:8]}.bin",
        "content": base64.b64encode(content).decode("ascii"),
        "content_type": "application/octet-stream",
        "fetch_mode": "base64",
    }


def generate_endpoint_attachment(size_key: str) -> dict[str, Any]:
    """Generate attachment that will be fetched from HTTP endpoint.

    The attachment-server will generate content on-the-fly based on storage_path.
    This tests the proxy's ability to fetch large attachments via HTTP.
    """
    size_bytes = ENDPOINT_ATTACHMENT_SIZES.get(size_key, "10240")
    unique_id = uuid.uuid4().hex[:8]

    return {
        "filename": f"large_doc_{unique_id}.bin",
        "storage_path": f"size={size_bytes}&id={unique_id}",
        "content_type": "application/octet-stream",
        "fetch_mode": "endpoint",
    }


def generate_message(tenant_id: str, attachment_type: str, fetch_mode: str) -> dict[str, Any]:
    """Generate a test message with optional attachment.

    Args:
        tenant_id: Tenant identifier
        attachment_type: Size key (none, small, medium, large, xlarge, huge)
        fetch_mode: "base64" for inline, "endpoint" for HTTP fetch
    """
    msg_id = f"load-{uuid.uuid4().hex[:12]}"
    recipient = f"test-{random.randint(1000, 9999)}@example.com"

    message = {
        "id": msg_id,
        "tenant_id": tenant_id,
        "sender": f"sender@{tenant_id}.test",
        "recipient_email": recipient,
        "subject": f"Load test {msg_id}",
        "body_text": f"This is a load test message from {tenant_id}.\n" * 10,
        "priority": random.choice([0, 1, 2, 2, 3, 3, 3]),  # Weighted priorities
    }

    if attachment_type != "none":
        if fetch_mode == "endpoint":
            # Large attachments via HTTP endpoint
            message["attachments"] = [generate_endpoint_attachment(attachment_type)]
        else:
            # Small/medium attachments inline as base64
            size = ATTACHMENT_SIZES.get(attachment_type, 0)
            attachment = generate_base64_attachment(size)
            if attachment:
                message["attachments"] = [attachment]

    return message


class TenantSetupMixin:
    """Mixin for tenant and account setup."""

    def setup_tenant(self, tenant_id: str, profile: dict) -> bool:
        """Create tenant and SMTP account if not exists."""
        headers = {
            "X-API-Token": API_TOKEN,
            "Content-Type": "application/json",
        }

        # Create tenant with attachment endpoint configuration
        tenant_payload = {
            "id": tenant_id,
            "name": f"Pressure Test - {profile['name']}",
            "active": True,
            # Configure attachment fetching via HTTP endpoint
            "client_base_url": ATTACHMENT_SERVER,
            "client_attachment_path": "/fetch",
        }
        resp = self.client.post("/tenant", json=tenant_payload, headers=headers, name="setup_tenant")
        if resp.status_code not in (200, 201):
            # Tenant might already exist, try to update it
            self.client.put(
                f"/tenant/{tenant_id}",
                json={
                    "client_base_url": ATTACHMENT_SERVER,
                    "client_attachment_path": "/fetch",
                },
                headers=headers,
                name="setup_tenant_update"
            )

        # Create SMTP account
        account_payload = {
            "id": f"smtp-{tenant_id}",
            "tenant_id": tenant_id,
            "smtp_host": SMTP_HOST,
            "smtp_port": SMTP_PORT,
            "smtp_user": "test",
            "smtp_password": "test",
            "sender_email": f"sender@{tenant_id}.test",
            "use_tls": False,
            "use_ssl": False,
            # No rate limits for pressure test
        }
        resp = self.client.post("/account", json=account_payload, headers=headers, name="setup_account")
        return resp.status_code in (200, 201)


class MailProxyUser(HttpUser, TenantSetupMixin):
    """Simulates a tenant client sending emails."""

    wait_time = between(0.1, 1.0)  # Fast requests

    def on_start(self):
        """Initialize tenant on user start."""
        # Select a profile based on weights
        weights = [p["weight"] for p in TENANT_PROFILES]
        self.profile = random.choices(TENANT_PROFILES, weights=weights)[0]
        self.tenant_id = f"tenant-{self.profile['name']}-{random.randint(1, 10)}"

        # Setup tenant and account
        self.setup_tenant(self.tenant_id, self.profile)

        self.headers = {
            "X-API-Token": API_TOKEN,
            "Content-Type": "application/json",
        }

    @task(10)
    def send_batch(self):
        """Send a batch of messages."""
        batch_size = self.profile["batch_size"]
        attachment_type = self.profile["attachment"]
        fetch_mode = self.profile.get("fetch_mode", "base64")

        messages = [
            generate_message(self.tenant_id, attachment_type, fetch_mode)
            for _ in range(batch_size)
        ]

        payload = {
            "tenant_id": self.tenant_id,
            "messages": messages,
        }

        with self.client.post(
            "/commands/add-messages",
            json=payload,
            headers=self.headers,
            name=f"add_messages/{self.profile['name']}",
            catch_response=True,
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if data.get("queued", 0) != batch_size:
                    response.failure(f"Expected {batch_size} queued, got {data.get('queued')}")
            else:
                response.failure(f"Status {response.status_code}: {response.text[:200]}")

    @task(2)
    def trigger_dispatch(self):
        """Trigger immediate dispatch for this tenant."""
        with self.client.post(
            "/commands/run-now",
            json={"tenant_id": self.tenant_id},
            headers=self.headers,
            name="run_now",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"run-now failed: {response.text[:200]}")

    @task(1)
    def check_status(self):
        """Check service status."""
        with self.client.get(
            "/status",
            headers=self.headers,
            name="status",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"status check failed: {response.status_code}")

    @task(1)
    def list_messages(self):
        """List pending messages for this tenant."""
        with self.client.get(
            f"/messages?tenant_id={self.tenant_id}&limit=10",
            headers=self.headers,
            name="list_messages",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"list_messages failed: {response.status_code}")


class AdminUser(HttpUser, TenantSetupMixin):
    """Simulates admin operations (less frequent)."""

    wait_time = between(5, 15)
    weight = 1  # Much less frequent than tenant users

    def on_start(self):
        self.headers = {
            "X-API-Token": API_TOKEN,
            "Content-Type": "application/json",
        }

    @task(5)
    def list_tenants(self):
        """List all tenants."""
        self.client.get("/tenants", headers=self.headers, name="admin/list_tenants")

    @task(5)
    def sync_status(self):
        """Check sync status for all tenants."""
        self.client.get("/tenants/sync-status", headers=self.headers, name="admin/sync_status")

    @task(3)
    def get_metrics(self):
        """Fetch Prometheus metrics."""
        self.client.get("/metrics", name="admin/metrics")

    @task(1)
    def instance_info(self):
        """Get instance configuration."""
        self.client.get("/instance", headers=self.headers, name="admin/instance")


# Event hooks for custom reporting
@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts."""
    print("=" * 60)
    print("PRESSURE TEST STARTED")
    print(f"Target: {environment.host}")
    print(f"SMTP Sink: {SMTP_HOST}:{SMTP_PORT}")
    print("=" * 60)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops."""
    print("=" * 60)
    print("PRESSURE TEST COMPLETED")
    print("=" * 60)


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, exception, **kwargs):
    """Log slow requests for debugging."""
    if response_time > 5000:  # > 5 seconds
        print(f"SLOW REQUEST: {name} took {response_time}ms")
