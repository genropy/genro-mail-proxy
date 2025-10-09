FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 TZ=Europe/Rome

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY async_mail_service ./async_mail_service
COPY main.py .

VOLUME ["/data"]
EXPOSE 8000

CMD ["python", "main.py"]
