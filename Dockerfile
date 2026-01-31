FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Rome

# Install tini for proper signal handling in containers (PID 1 problem)
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy source code
COPY . .

# Install from local source with PostgreSQL support
RUN pip install --no-cache-dir ".[postgresql]"

# SQLite database stored in /data by default (when not using PostgreSQL)
VOLUME ["/data"]
EXPOSE 8000

# Use tini as init to properly handle signals
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "core.mail_proxy.server:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10"]
