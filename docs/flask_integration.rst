
Flask Integration
=================

This guide shows how to integrate genro-mail-proxy with a Flask application.

Flask has no built-in email system. The common solution is Flask-Mail, which
is synchronous and blocks the request thread during SMTP delivery. For async
delivery, you typically need Celery + Redis, which adds operational complexity.

genro-mail-proxy provides an alternative: a dedicated email service that handles
queuing, retry, and delivery reports. It acts as a specialized message broker
for email, with built-in SMTP handling and delivery tracking.

.. note::

   The HTTP calls to enqueue messages are synchronous (blocking) in the examples
   below. The "async" benefit is that SMTP delivery happens in the background
   after the HTTP call returns. For truly non-blocking enqueue, use an async
   HTTP client or a background thread.

When to use the proxy with Flask
--------------------------------

Consider genro-mail-proxy when:

- You don't want to introduce Celery just for email
- You need delivery reports with automatic callback
- Multiple services share the same email infrastructure
- You need rate limiting shared across Flask instances

When Flask-Mail is sufficient:

- Low email volume where blocking is acceptable
- Simple transactional emails without retry requirements
- Single Flask instance with no rate limiting needs

Comparison with Flask-Mail + Celery
-----------------------------------

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Feature
     - Flask-Mail + Celery
     - genro-mail-proxy
   * - Dependencies
     - Redis/RabbitMQ + Celery worker
     - Only the proxy
   * - Delivery reports
     - Manual implementation
     - Built-in HTTP callback
   * - Retry on failure
     - Celery retry decorator
     - Built-in exponential backoff
   * - Rate limiting
     - Manual implementation
     - Built-in per account
   * - Blocking HTTP call
     - No (with Celery)
     - Yes (enqueue is sync)*
   * - Operational complexity
     - High (broker + worker)
     - Medium (one service)

\* The HTTP call to enqueue messages blocks until the proxy accepts them,
but SMTP delivery happens asynchronously. For non-blocking enqueue, use
an async HTTP client (httpx with asyncio) or a background thread.

Installation
------------

.. code-block:: bash

   pip install requests  # or httpx

Configuration
-------------

.. code-block:: python

   # config.py

   class Config:
       MAIL_PROXY_URL = "http://localhost:8000"
       MAIL_PROXY_TOKEN = "your-api-token"
       MAIL_PROXY_ACCOUNT = "default"

Client module
-------------

.. code-block:: python

   # mail_proxy.py
   """Flask client for genro-mail-proxy."""

   import uuid
   import requests
   from flask import current_app


   class MailProxy:
       """Client for sending emails through genro-mail-proxy."""

       def _config(self, key):
           return current_app.config.get(key)

       def _headers(self):
           return {
               "X-API-Token": self._config("MAIL_PROXY_TOKEN"),
               "Content-Type": "application/json",
           }

       def send_mail(
           self,
           subject,
           body,
           sender,
           recipients,
           cc=None,
           bcc=None,
           html=None,
           attachments=None,
           priority=2,
           message_id=None,
       ):
           """Send an email through the mail proxy.

           Args:
               subject: Email subject.
               body: Plain text body.
               sender: Sender email address.
               recipients: List of recipient addresses.
               cc: List of CC addresses (optional).
               bcc: List of BCC addresses (optional).
               html: HTML body (optional).
               attachments: List of attachment dicts (optional).
               priority: 0=immediate, 1=high, 2=medium (default), 3=low.
               message_id: Custom message ID (auto-generated if not provided).

           Returns:
               Dict with "queued" count and "rejected" list.
           """
           message = {
               "id": message_id or str(uuid.uuid4()),
               "account_id": self._config("MAIL_PROXY_ACCOUNT") or "default",
               "from": sender,
               "to": recipients if isinstance(recipients, list) else [recipients],
               "subject": subject,
               "body": html or body,
               "content_type": "html" if html else "plain",
               "priority": priority,
           }

           if cc:
               message["cc"] = cc if isinstance(cc, list) else [cc]
           if bcc:
               message["bcc"] = bcc if isinstance(bcc, list) else [bcc]
           if attachments:
               message["attachments"] = attachments

           response = requests.post(
               f"{self._config('MAIL_PROXY_URL')}/commands/add-messages",
               headers=self._headers(),
               json={"messages": [message]},
               timeout=10,
           )
           response.raise_for_status()
           return response.json()


   # Module-level instance
   mail_proxy = MailProxy()

Flask application
-----------------

.. code-block:: python

   # app.py
   from flask import Flask, jsonify, request
   from mail_proxy import mail_proxy

   app = Flask(__name__)
   app.config.from_object("config.Config")


   @app.route("/send-welcome/<int:user_id>", methods=["POST"])
   def send_welcome(user_id):
       # Get user from database
       user = get_user(user_id)

       result = mail_proxy.send_mail(
           subject=f"Welcome {user.name}!",
           body=f"Hello {user.name}, thanks for signing up.",
           sender="noreply@example.com",
           recipients=[user.email],
           priority=1,
       )

       return jsonify(result)


   @app.route("/send-invoice/<int:invoice_id>", methods=["POST"])
   def send_invoice(invoice_id):
       invoice = get_invoice(invoice_id)

       result = mail_proxy.send_mail(
           subject=f"Invoice #{invoice.number}",
           body=f"Please find attached invoice #{invoice.number}.",
           html=f"<p>Please find attached invoice <strong>#{invoice.number}</strong>.</p>",
           sender="billing@example.com",
           recipients=[invoice.customer_email],
           attachments=[
               {
                   "filename": f"invoice_{invoice.number}.pdf",
                   "storage_path": f"invoice_id={invoice.id}",
                   "fetch_mode": "endpoint",
               }
           ],
       )

       return jsonify(result)

Delivery reports endpoint
-------------------------

**Important**: Authenticate the request to ensure it comes from your mail proxy
instance. The proxy sends the token configured in the tenant's ``auth_token`` field.

.. code-block:: python

   # app.py (continued)
   from functools import wraps

   def verify_proxy_auth(f):
       """Decorator to verify mail proxy authentication."""
       @wraps(f)
       def decorated(*args, **kwargs):
           # Check Bearer token (configured in tenant's auth_token)
           auth_header = request.headers.get("Authorization", "")
           expected_token = current_app.config.get("MAIL_PROXY_SYNC_TOKEN")

           if expected_token:
               if not auth_header.startswith("Bearer "):
                   return jsonify({"error": "Missing authorization"}), 403
               token = auth_header[7:]  # Remove "Bearer " prefix
               if token != expected_token:
                   return jsonify({"error": "Invalid token"}), 403

           return f(*args, **kwargs)
       return decorated


   @app.route("/mail/delivery-report", methods=["POST"])
   @verify_proxy_auth
   def delivery_report():
       """Receive delivery reports from the mail proxy."""
       data = request.get_json()

       sent = 0
       error = 0

       for report in data.get("delivery_report", []):
           message_id = report["id"]

           if report.get("sent_ts"):
               sent += 1
               # Update your database
               # mark_email_sent(message_id)

           elif report.get("error_ts"):
               error += 1
               # Log the error
               # mark_email_failed(message_id, report.get("error"))

       return jsonify({"ok": True, "queued": 0})


   @app.route("/mail/attachments", methods=["POST"])
   def serve_attachment():
       """Serve attachment content to the mail proxy."""
       invoice_id = request.form.get("invoice_id")

       if invoice_id:
           invoice = get_invoice(invoice_id)
           pdf_content = generate_invoice_pdf(invoice)
           return pdf_content, 200, {"Content-Type": "application/pdf"}

       return "", 404

Flask-Mail comparison
---------------------

For reference, here's how Flask-Mail with Celery looks:

.. code-block:: python

   # With Flask-Mail + Celery
   from flask_mail import Mail, Message
   from celery import Celery

   mail = Mail(app)
   celery = Celery(app.name, broker="redis://localhost:6379/0")

   @celery.task
   def send_async_email(subject, sender, recipients, body):
       with app.app_context():
           msg = Message(subject, sender=sender, recipients=recipients)
           msg.body = body
           mail.send(msg)
       # No delivery report - you don't know if it succeeded

   # Usage
   send_async_email.delay("Welcome!", "noreply@example.com", [user.email], "Hello")

The Celery approach requires running a Redis server and a Celery worker process.
The mail proxy consolidates this into a single service with built-in delivery tracking.

Blueprint example
-----------------

For larger applications, organize the mail functionality as a blueprint:

.. code-block:: python

   # blueprints/mail.py
   from flask import Blueprint, jsonify, request
   from mail_proxy import mail_proxy

   mail_bp = Blueprint("mail", __name__, url_prefix="/mail")


   @mail_bp.route("/send", methods=["POST"])
   def send():
       data = request.get_json()

       result = mail_proxy.send_mail(
           subject=data["subject"],
           body=data.get("body", ""),
           html=data.get("html"),
           sender=data["from"],
           recipients=data["to"],
           priority=data.get("priority", 2),
       )

       return jsonify(result)


   @mail_bp.route("/delivery-report", methods=["POST"])
   def delivery_report():
       data = request.get_json()
       # Process reports...
       return jsonify({"ok": True, "queued": 0})

   # app.py
   from blueprints.mail import mail_bp
   app.register_blueprint(mail_bp)

Proxy tenant configuration
--------------------------

Configure the proxy tenant to point to your Flask endpoints:

.. code-block:: bash

   mail-proxy myserver tenants add myflaskapp \
       --base-url "https://myflaskapp.example.com" \
       --sync-path "/mail/delivery-report" \
       --attachment-path "/mail/attachments" \
       --auth-method bearer \
       --auth-token "shared-secret"
