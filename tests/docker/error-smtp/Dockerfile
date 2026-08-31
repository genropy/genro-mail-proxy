FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir aiosmtpd

COPY server.py .

ENV SMTP_PORT=1025
ENV SMTP_ERROR_MODE=none

EXPOSE 1025

CMD ["python", "server.py"]
