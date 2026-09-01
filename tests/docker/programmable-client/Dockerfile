FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir fastapi uvicorn

COPY server.py .

ENV CLIENT_PORT=8080

EXPOSE 8080

CMD ["python", "server.py"]
