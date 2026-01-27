
Django Integration
==================

This guide shows how to integrate genro-mail-proxy with a Django application,
and compares the approach with Celery-based alternatives.

When to use the proxy with Django
---------------------------------

Consider genro-mail-proxy when:

- You don't want to introduce Celery just for email
- You need delivery reports with automatic callback to your app
- Multiple services (not just Django) share the same email infrastructure
- You need rate limiting shared across Django instances
- Multi-tenancy is required (multiple organizations, one proxy)

When to use Celery instead:

- Celery is already in your stack for other tasks
- You prefer Django's standard ``send_mail()`` API
- Your team is familiar with Celery monitoring (Flower, etc.)

Comparison with Celery
----------------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Feature
     - Celery + django-celery-email
     - genro-mail-proxy
   * - Dependencies
     - Redis/RabbitMQ + Celery worker
     - Only the proxy (SQLite internal)
   * - Django API
     - Standard ``send_mail()``
     - HTTP client (custom)
   * - Delivery reports
     - Manual implementation required
     - Built-in HTTP callback
   * - Retry on failure
     - Configurable via Celery
     - Built-in with exponential backoff
   * - Rate limiting
     - Manual implementation
     - Built-in per account
   * - Deferred state
     - No
     - Yes (rate limiting)
   * - Send history
     - Only with result backend
     - Built-in (send_log table)
   * - Multi-tenant
     - No (one backend per project)
     - Yes
   * - Monitoring
     - Flower, Celery events
     - Prometheus metrics
   * - Operational complexity
     - High (broker + worker)
     - Medium (one service)

The key difference is **delivery reports**: with Celery you know if the task
succeeded (the SMTP call didn't raise an exception), but you don't get automatic
notification back to your application. The proxy's ``proxy_sync`` cycle handles
this automatically.

Installation
------------

.. code-block:: bash

   pip install httpx  # HTTP client for the proxy

Configuration
-------------

Add to your Django settings:

.. code-block:: python

   # settings.py

   MAIL_PROXY_URL = "http://localhost:8000"
   MAIL_PROXY_TOKEN = "your-api-token"
   MAIL_PROXY_ACCOUNT = "default"  # SMTP account ID in the proxy

Client module
-------------

Create a client module to interact with the proxy:

.. code-block:: python

   # myapp/mail_proxy.py
   """Django client for genro-mail-proxy."""

   import uuid
   import httpx
   from django.conf import settings


   class MailProxyClient:
       """Client for sending emails through genro-mail-proxy."""

       def __init__(self):
           self.base_url = settings.MAIL_PROXY_URL
           self.token = settings.MAIL_PROXY_TOKEN
           self.default_account = getattr(settings, "MAIL_PROXY_ACCOUNT", "default")

       def _headers(self):
           return {"X-API-Token": self.token, "Content-Type": "application/json"}

       def send_mail(
           self,
           subject: str,
           body: str,
           from_email: str,
           to: list[str],
           cc: list[str] | None = None,
           bcc: list[str] | None = None,
           html_body: str | None = None,
           attachments: list[dict] | None = None,
           priority: int = 2,
           account_id: str | None = None,
           message_id: str | None = None,
       ) -> dict:
           """Send an email through the mail proxy.

           Args:
               subject: Email subject.
               body: Plain text body.
               from_email: Sender address.
               to: List of recipient addresses.
               cc: List of CC addresses (optional).
               bcc: List of BCC addresses (optional).
               html_body: HTML body (optional, replaces plain text).
               attachments: List of attachment dicts (optional).
               priority: 0=immediate, 1=high, 2=medium (default), 3=low.
               account_id: SMTP account to use (defaults to MAIL_PROXY_ACCOUNT).
               message_id: Custom message ID (auto-generated if not provided).

           Returns:
               Dict with "queued" count and "rejected" list.
           """
           message = {
               "id": message_id or str(uuid.uuid4()),
               "account_id": account_id or self.default_account,
               "from": from_email,
               "to": to,
               "subject": subject,
               "body": html_body or body,
               "content_type": "html" if html_body else "plain",
               "priority": priority,
           }

           if cc:
               message["cc"] = cc
           if bcc:
               message["bcc"] = bcc
           if attachments:
               message["attachments"] = attachments

           with httpx.Client(timeout=10) as client:
               response = client.post(
                   f"{self.base_url}/commands/add-messages",
                   headers=self._headers(),
                   json={"messages": [message]},
               )
               response.raise_for_status()
               return response.json()


   # Singleton instance
   mail_proxy = MailProxyClient()

Usage in views
--------------

.. code-block:: python

   # myapp/views.py
   from django.http import JsonResponse
   from django.views import View
   from .mail_proxy import mail_proxy


   class SendWelcomeEmailView(View):
       def post(self, request, user_id):
           from django.contrib.auth import get_user_model
           User = get_user_model()

           user = User.objects.get(pk=user_id)

           result = mail_proxy.send_mail(
               subject=f"Welcome {user.first_name}!",
               body=f"Hello {user.first_name}, thanks for signing up.",
               from_email="noreply@example.com",
               to=[user.email],
               priority=1,  # high priority
           )

           return JsonResponse(result)


   class SendInvoiceView(View):
       def post(self, request, invoice_id):
           from .models import Invoice

           invoice = Invoice.objects.get(pk=invoice_id)

           # Attachment via HTTP endpoint (proxy calls your server)
           result = mail_proxy.send_mail(
               subject=f"Invoice #{invoice.number}",
               body=f"Please find attached invoice #{invoice.number}.",
               html_body=f"<p>Please find attached invoice <strong>#{invoice.number}</strong>.</p>",
               from_email="billing@example.com",
               to=[invoice.customer.email],
               attachments=[
                   {
                       "filename": f"invoice_{invoice.number}.pdf",
                       "storage_path": f"invoice_id={invoice.id}",
                       "fetch_mode": "endpoint",
                   }
               ],
           )

           return JsonResponse(result)

Receiving delivery reports
--------------------------

The proxy sends delivery reports to your configured ``client_sync_path``.
Create an endpoint to receive them. **Important**: Authenticate the request
to ensure it comes from your mail proxy instance.

.. code-block:: python

   # myapp/views.py
   import json
   from functools import wraps
   from django.conf import settings
   from django.views.decorators.csrf import csrf_exempt
   from django.views.decorators.http import require_POST
   from django.http import JsonResponse, HttpResponseForbidden


   def verify_proxy_auth(view_func):
       """Decorator to verify mail proxy authentication."""
       @wraps(view_func)
       def wrapper(request, *args, **kwargs):
           # Check Bearer token (configured in tenant's auth_token)
           auth_header = request.headers.get("Authorization", "")
           expected_token = getattr(settings, "MAIL_PROXY_SYNC_TOKEN", None)

           if expected_token:
               if not auth_header.startswith("Bearer "):
                   return HttpResponseForbidden("Missing authorization")
               token = auth_header[7:]  # Remove "Bearer " prefix
               if token != expected_token:
                   return HttpResponseForbidden("Invalid token")

           return view_func(request, *args, **kwargs)
       return wrapper


   @csrf_exempt
   @require_POST
   @verify_proxy_auth
   def mail_delivery_report(request):
       """Receive delivery reports from the mail proxy."""
       data = json.loads(request.body)

       sent = 0
       error = 0
       deferred = 0

       for report in data.get("delivery_report", []):
           message_id = report["id"]

           if report.get("sent_ts"):
               sent += 1
               # Update your model
               # EmailLog.objects.filter(message_id=message_id).update(
               #     status="sent", sent_at=datetime.fromtimestamp(report["sent_ts"])
               # )

           elif report.get("error_ts"):
               error += 1
               error_message = report.get("error", "Unknown error")
               # EmailLog.objects.filter(message_id=message_id).update(
               #     status="error", error_message=error_message
               # )

           elif report.get("deferred_ts"):
               deferred += 1
               # Message was rate-limited, will be retried

       return JsonResponse({"ok": True, "queued": 0})

Serving attachments
-------------------

If you use the ``@params`` format for attachments, create an endpoint that
serves the file content:

.. code-block:: python

   # myapp/views.py
   from django.http import HttpResponse
   from django.views.decorators.csrf import csrf_exempt
   from django.views.decorators.http import require_POST


   @csrf_exempt
   @require_POST
   def attachment_fetch(request):
       """Serve attachment content to the mail proxy."""
       invoice_id = request.POST.get("invoice_id")

       if invoice_id:
           from .models import Invoice
           invoice = Invoice.objects.get(pk=invoice_id)
           pdf_content = invoice.generate_pdf()

           return HttpResponse(pdf_content, content_type="application/pdf")

       return HttpResponse(status=404)

URL configuration
-----------------

.. code-block:: python

   # myapp/urls.py
   from django.urls import path
   from . import views

   urlpatterns = [
       path("mail/welcome/<int:user_id>/", views.SendWelcomeEmailView.as_view()),
       path("mail/invoice/<int:invoice_id>/", views.SendInvoiceView.as_view()),
       path("mail/delivery-report/", views.mail_delivery_report),
       path("mail/attachments/", views.attachment_fetch),
   ]

Proxy tenant configuration
--------------------------

Configure the proxy tenant to point to your Django endpoints:

.. code-block:: bash

   mail-proxy myserver tenants add myapp \
       --base-url "https://myapp.example.com" \
       --sync-path "/mail/delivery-report/" \
       --attachment-path "/mail/attachments/" \
       --auth-method bearer \
       --auth-token "shared-secret"

Celery comparison example
-------------------------

For reference, here's how the same functionality looks with Celery:

.. code-block:: python

   # settings.py (Celery approach)
   EMAIL_BACKEND = 'djcelery_email.backends.CeleryEmailBackend'
   CELERY_EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
   CELERY_BROKER_URL = 'redis://localhost:6379/0'

   # views.py (Celery approach)
   from django.core.mail import send_mail

   def send_welcome(request, user_id):
       user = User.objects.get(pk=user_id)

       # Returns immediately, task queued in Redis
       send_mail(
           subject=f"Welcome {user.first_name}!",
           message="Thanks for signing up.",
           from_email="noreply@example.com",
           recipient_list=[user.email],
       )
       # No delivery report - you don't know if it was actually sent

       return JsonResponse({"ok": True})

The Celery approach is simpler to set up if you already have Celery.
With `django-celery-results <https://django-celery-results.readthedocs.io/>`_
you can also track task results, though delivery reports require custom
implementation rather than being built-in.

Tracking email status
---------------------

To track email status in your Django models:

.. code-block:: python

   # myapp/models.py
   from django.db import models


   class EmailLog(models.Model):
       """Track email delivery status."""

       STATUS_CHOICES = [
           ("queued", "Queued"),
           ("sent", "Sent"),
           ("error", "Error"),
           ("deferred", "Deferred"),
       ]

       message_id = models.CharField(max_length=100, unique=True, db_index=True)
       recipient = models.EmailField()
       subject = models.CharField(max_length=255)
       status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="queued")
       error_message = models.TextField(blank=True)
       queued_at = models.DateTimeField(auto_now_add=True)
       sent_at = models.DateTimeField(null=True, blank=True)

       class Meta:
           ordering = ["-queued_at"]

Update the client to create log entries:

.. code-block:: python

   # myapp/mail_proxy.py (extended)

   def send_mail_tracked(self, subject, body, from_email, to, **kwargs):
       """Send email and create tracking record."""
       from .models import EmailLog
       import uuid

       message_id = str(uuid.uuid4())

       # Create log entry first
       for recipient in to:
           EmailLog.objects.create(
               message_id=message_id,
               recipient=recipient,
               subject=subject,
           )

       # Send through proxy
       return self.send_mail(
           subject=subject,
           body=body,
           from_email=from_email,
           to=to,
           message_id=message_id,
           **kwargs
       )

Then update the delivery report handler to update the log entries.
