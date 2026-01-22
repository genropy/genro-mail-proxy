FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Rome

WORKDIR /app

# Install from PyPI
RUN pip install --no-cache-dir genro-mail-proxy

# Database stored in /data by default (GMP_DB_PATH=/data/mail_service.db)
VOLUME ["/data"]
EXPOSE 8000

# Start the server
CMD ["uvicorn", "async_mail_service.server:app", "--host", "0.0.0.0", "--port", "8000"]
