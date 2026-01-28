"""
Dynamic attachment server for pressure testing.

Generates random binary content of requested size on-the-fly.
Supports caching headers for MD5-based cache testing.

Endpoints:
    GET /attachment?size=<bytes>     - Random binary of specified size
    GET /attachment?size=small       - 10KB file
    GET /attachment?size=medium      - 500KB file
    GET /attachment?size=large       - 2MB file
    POST /fetch                      - Endpoint mode (JSON body with storage_path)
    GET /health                      - Health check
"""

import hashlib
import json
import os
import random
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import parse_qs, urlparse

# Predefined sizes
SIZES = {
    "small": 10 * 1024,        # 10 KB
    "medium": 500 * 1024,      # 500 KB
    "large": 2 * 1024 * 1024,  # 2 MB
    "xlarge": 10 * 1024 * 1024, # 10 MB
}

# Cache for consistent content when using same seed
CONTENT_CACHE: dict[str, bytes] = {}
MAX_CACHE_SIZE = 50 * 1024 * 1024  # 50 MB cache limit


def generate_content(size: int, seed: str | None = None) -> bytes:
    """Generate random binary content of specified size."""
    if seed:
        cache_key = f"{seed}:{size}"
        if cache_key in CONTENT_CACHE:
            return CONTENT_CACHE[cache_key]

        # Use seed for reproducible content
        random.seed(seed)
        content = bytes(random.getrandbits(8) for _ in range(size))

        # Cache if not too large
        total_cached = sum(len(v) for v in CONTENT_CACHE.values())
        if total_cached + size < MAX_CACHE_SIZE:
            CONTENT_CACHE[cache_key] = content

        return content
    else:
        # Pure random
        return os.urandom(size)


class AttachmentHandler(BaseHTTPRequestHandler):
    """HTTP handler for attachment requests."""

    def log_message(self, format, *args):
        """Suppress default logging for performance."""
        pass

    def do_GET(self):
        """Handle GET requests."""
        parsed = urlparse(self.path)

        if parsed.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok": true}')
            return

        if parsed.path == "/attachment":
            self.handle_attachment(parsed)
            return

        self.send_error(404, "Not found")

    def do_POST(self):
        """Handle POST requests (endpoint mode)."""
        if self.path == "/fetch":
            self.handle_fetch()
            return

        self.send_error(404, "Not found")

    def handle_attachment(self, parsed):
        """Generate attachment based on query params."""
        params = parse_qs(parsed.query)

        # Get size
        size_param = params.get("size", ["small"])[0]
        if size_param in SIZES:
            size = SIZES[size_param]
        else:
            try:
                size = int(size_param)
            except ValueError:
                self.send_error(400, f"Invalid size: {size_param}")
                return

        # Cap size at 50MB
        size = min(size, 50 * 1024 * 1024)

        # Optional seed for reproducible content
        seed = params.get("seed", [None])[0]

        # Generate content
        content = generate_content(size, seed)
        content_hash = hashlib.md5(content).hexdigest()

        # Send response
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Content-Disposition", f'attachment; filename="test_{size}bytes.bin"')
        self.send_header("ETag", f'"{content_hash}"')
        self.send_header("X-Content-MD5", content_hash)
        self.end_headers()
        self.wfile.write(content)

    def handle_fetch(self):
        """Handle endpoint-mode fetch (POST with JSON body)."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self.send_error(400, "Missing request body")
            return

        try:
            body = self.rfile.read(content_length)
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "Invalid JSON")
            return

        storage_path = data.get("storage_path", "")

        # Parse storage_path for size info
        # Format: "size=<bytes>" or "size=small|medium|large"
        size = SIZES["small"]  # default
        for part in storage_path.split("&"):
            if part.startswith("size="):
                size_param = part[5:]
                if size_param in SIZES:
                    size = SIZES[size_param]
                else:
                    try:
                        size = int(size_param)
                    except ValueError:
                        pass

        # Cap size
        size = min(size, 50 * 1024 * 1024)

        # Use storage_path as seed for consistent content
        content = generate_content(size, storage_path)

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def main():
    port = int(os.environ.get("PORT", "8080"))
    server = HTTPServer(("0.0.0.0", port), AttachmentHandler)
    print(f"Attachment server running on port {port}")
    print(f"  GET /attachment?size=small|medium|large|<bytes>")
    print(f"  POST /fetch (JSON body with storage_path)")
    print(f"  GET /health")
    server.serve_forever()


if __name__ == "__main__":
    main()
