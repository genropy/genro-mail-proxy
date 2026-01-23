FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Rome

WORKDIR /app

# Install with PostgreSQL support
RUN pip install --no-cache-dir "genro-mail-proxy[postgresql]"

# SQLite database stored in /data by default (when not using PostgreSQL)
VOLUME ["/data"]
EXPOSE 8000

# Start the server
CMD ["uvicorn", "mail_proxy.server:app", "--host", "0.0.0.0", "--port", "8000"]
