[![Documentation Status](https://readthedocs.org/projects/gnr-async-mail-service/badge/?version=latest)](https://gnr-async-mail-service.readthedocs.io/en/latest/)

# gnr-async-mail-service

**Authors:** Softwell S.r.l. - Giovanni Porcari  
**License:** MIT

Asynchronous email dispatcher microservice with scheduling, rate limiting, attachments (S3/URL/base64), REST API (FastAPI), and Prometheus metrics.

Main integration points:

- REST control plane secured by ``X-API-Token`` for queue management and configuration.
- Outbound ``proxy_sync`` call towards Genropy, authenticated via basic auth and configured through ``[sync]`` in ``config.ini``.
- Delivery reports and Prometheus metrics to monitor message lifecycle and rate limiting.

## Quick start

```bash
docker build -t gnr-async-mail-service .
docker run -p 8000:8000 -e SMTP_USER=... -e SMTP_PASSWORD=... -e FETCH_URL=https://your/api gnr-async-mail-service
```
