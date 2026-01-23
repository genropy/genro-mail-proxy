FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Rome

WORKDIR /app

# Copy source code
COPY . .

# Install from local source with PostgreSQL support
RUN pip install --no-cache-dir ".[postgresql]"

# SQLite database stored in /data by default (when not using PostgreSQL)
VOLUME ["/data"]
EXPOSE 8000

# Start the server with graceful shutdown timeout
CMD ["uvicorn", "mail_proxy.server:app", "--host", "0.0.0.0", "--port", "8000", "--timeout-graceful-shutdown", "10"]
